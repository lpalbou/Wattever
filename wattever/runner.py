"""Benchmark orchestration.

All workloads for one model finish before moving to the next model. By default,
each context/concurrency combination gets its own load-time KV allocation.
The optional fixed policy reuses the largest allocation throughout a model's
MTP mode; its different memory conditions are recorded with every result.

A load that fails is a result, not an error: "this card cannot hold this model at
this context" is precisely what the user is trying to find out.
"""
import datetime
import copy
import math
import uuid
import threading
import time

from . import corpus, lmstudio, power, store
from .platform_util import IS_MAC

FAIL_LOAD = "load_failed"
FAIL_CONTEXT = "context_overflow"
FAIL_TIMEOUT = "timeout"
FAIL_UNSUPPORTED = "exceeds_model_context"
FAIL_ERROR = "error"
FAIL_SKIPPED = "skipped_after_failure"
FAIL_MTP = "mtp_unsupported"

DEFAULT_CONFIG = {
    "models": [],
    "concurrency_levels": [1],
    "sizes": [512, 5000, 10000, 20000, 40000],
    "max_tokens": 256,
    "repeats": 1,
    # "max" rather than "auto": LM Studio's automatic placement is deliberately
    # conservative and will leave several GB of VRAM unused on a model that only
    # just fits, which costs an order of magnitude on decode. Measured on a
    # 5060 Ti with a 14.1 GB model: auto used 12.9 of 16.3 GB and gave 2.9 tk/s,
    # max used 15.4 GB and gave 23.4 tk/s. A benchmark should report what the
    # card can do; the fallback ladder covers the case where max does not fit.
    "gpu_offload": "max",
    "fallback_on_failure": True,
    "stop_model_after_failure": True,
    "context_headroom": 512,
    # Token counts are estimated before inference. Reserve proportional space
    # too, rather than turning a small tokenizer error into a false capacity limit.
    "context_safety_fraction": 0.05,
    "context_policy": "per_test",
    "context_planner_version": 1,
    "per_test_timeout_s": 1800,
    "load_timeout_s": 900,
    "warmup": True,
    # Reasoning is off by default: with it on, almost the whole output budget is
    # spent on a chain of thought, so "decode tk/s" would describe thinking
    # rather than the answer, and the figure stops being comparable between
    # models that think by default and models that do not.
    "disable_thinking": True,
    # MTP speculative decoding. Off by default because only a GGUF with a
    # bundled MTP head supports it, and because it changes what decode tk/s
    # means; ["off", "on"] measures both in one session.
    "mtp_modes": ["off"],
    "power_interval_ms": 150,
    # Everything that is not the GPU. On macOS the CPU rail is measured
    # directly, so this covers only display, storage, fans and DRAM; on a
    # desktop PC it has to stand in for the idle CPU and board as well.
    "other_idle_w": 10.0 if IS_MAC else 60.0,
    # Only used when the CPU rail cannot be measured (Windows/Linux), where the
    # CPU side is inferred as TDP x measured load.
    "cpu_tdp_w": 25.0 if IS_MAC else 140.0,
    "label": "",
    "notes": "",
    "gpu_index": 0,
}

FALLBACK_LADDER = ["auto", "0.5", "off"]


def context_budget(size, cfg, concurrency, model_max=None):
    """Return (per-request capacity, shared KV capacity) in tokens.

    LM Studio's unified KV pool must accommodate every simultaneous request.
    Model context limits apply to one request, not to the whole shared pool.
    """
    minimum = size + int(cfg["max_tokens"]) + int(cfg["context_headroom"])
    if model_max and minimum > model_max:
        return None, None
    per_request = minimum + math.ceil(size * float(cfg.get("context_safety_fraction", 0.05)))
    if model_max:
        per_request = min(per_request, int(model_max))
    return per_request, per_request * concurrency


def completed_cases(run, cfg):
    """Identify checkpoints without rerunning measured failures or completed work."""
    cases = set()
    for row in run.get("results") or []:
        if row.get("status") not in ("ok", "failed", "skipped"):
            continue
        if (row.get("model") not in cfg["models"] or row.get("target_tokens") not in cfg["sizes"]
                or row.get("concurrency", 1) not in cfg.get("concurrency_levels", [1])
                or bool(row.get("mtp")) not in _mtp_modes(cfg, row.get("model"))):
            continue
        repeats = [row["repeat"]] if "repeat" in row else range(int(cfg["repeats"]))
        for repeat in repeats:
            if not isinstance(repeat, int) or not 0 <= repeat < int(cfg["repeats"]):
                continue
            cases.add((row.get("model"), bool(row.get("mtp")), row.get("concurrency", 1),
                       row.get("target_tokens"), repeat))
    return cases


def _mtp_modes(cfg, model=None):
    """Config carries strings ("off"/"on") so it round-trips through JSON cleanly."""
    modes = (cfg.get("model_modes") or {}).get(model) if model else None
    modes = modes or cfg.get("mtp_modes") or ["off"]
    out = []
    for mode in modes:
        flag = mode is True or str(mode).lower() in ("on", "true", "1", "mtp")
        if flag not in out:
            out.append(flag)
    return out or [False]


def planned_cases(cfg):
    return {(model, mtp, concurrency, size, rep)
            for model in cfg["models"] for mtp in _mtp_modes(cfg, model)
            for concurrency in cfg["concurrency_levels"] for size in cfg["sizes"]
            for rep in range(int(cfg["repeats"]))}


def extension_config(source, selection):
    """A deliberately narrow edit: add workloads without changing measurement settings."""
    modes = selection.get("model_modes")
    if not isinstance(modes, dict) or not modes:
        raise ValueError("Choose at least one model and its Standard or MTP mode.")
    for model, values in modes.items():
        if (not isinstance(model, str) or not model or not isinstance(values, list)
                or not values or any(mode not in ("off", "on") for mode in values)):
            raise ValueError("Each model needs Standard (off), MTP (on), or both.")
    cfg = dict(DEFAULT_CONFIG, **(source.get("config") or {}))
    cfg.update(models=list(modes), model_modes=copy.deepcopy(modes),
               context_planner_version=DEFAULT_CONFIG["context_planner_version"])
    # Legacy runs inherit the corrected planner, not an accidental zero margin.
    if not (source.get("config") or {}).get("context_planner_version"):
        cfg["context_safety_fraction"] = DEFAULT_CONFIG["context_safety_fraction"]
    for key in ("sizes", "concurrency_levels"):
        values = selection.get(key, cfg[key])
        if not isinstance(values, list) or not values or any(type(v) is not int for v in values):
            raise ValueError("Choose valid prompt sizes and simultaneous query counts.")
        cfg[key] = sorted(set(values))
    if any(size <= 0 or size > 1000000 for size in cfg["sizes"]):
        raise ValueError("Prompt sizes must be between 1 and 1,000,000 tokens.")
    if any(level < 1 or level > 100 for level in cfg["concurrency_levels"]):
        raise ValueError("Simultaneous query counts must be between 1 and 100.")
    return cfg


def extension_plan(source, cfg, retry_failed=False, rerun_existing=False):
    cases = planned_cases(cfg)
    completed = completed_cases(source, cfg)
    successful = completed_cases({"results": [row for row in source.get("results", [])
                                               if row.get("status") == "ok"]}, cfg)
    retries = (completed - successful) if retry_failed or rerun_existing else set()
    reruns = successful if rerun_existing else set()
    return {"existing": len(completed - retries - reruns), "new_tests": len(cases - completed),
            "retry_tests": len(retries), "rerun_tests": len(reruns),
            "total_tests": len(cases - completed) + len(retries) + len(reruns),
            "warnings": (["Earlier results used the legacy context planner. New tests use corrected memory allocation; each session's settings are recorded."]
                         if not (source.get("config") or {}).get("context_planner_version") else [])}


def merged_config(source_cfg, added_cfg):
    """Describe the full benchmark while execution_config records the current selection."""
    merged = copy.deepcopy(source_cfg)
    merged["models"] = list(dict.fromkeys(source_cfg["models"] + added_cfg["models"]))
    for key in ("sizes", "concurrency_levels"):
        merged[key] = sorted(set(source_cfg.get(key, [1]) + added_cfg[key]))
    merged["model_modes"] = {}
    for model in merged["models"]:
        modes = (_mtp_modes(source_cfg, model) if model in source_cfg["models"] else [])
        if model in added_cfg["models"]:
            modes += _mtp_modes(added_cfg, model)
        merged["model_modes"][model] = ["on" if mode else "off" for mode in dict.fromkeys(modes)]
    merged["mtp_modes"] = ["on" if mode else "off" for mode in dict.fromkeys(
        mode for model in merged["models"] for mode in _mtp_modes(merged, model))]
    return merged


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _classify(message):
    low = (message or "").lower()
    if "context" in low and ("exceed" in low or "larger" in low or "overflow" in low
                             or "too long" in low or "maximum" in low):
        return FAIL_CONTEXT
    if "timed out" in low or "timeout" in low:
        return FAIL_TIMEOUT
    if "memory" in low or "oom" in low or "allocate" in low or "vram" in low:
        return FAIL_LOAD
    return FAIL_ERROR


class BenchRunner:
    def __init__(self):
        self.cli = lmstudio.LmsCli()
        self.lock = threading.Lock()
        self.thread = None
        self.cancel_event = threading.Event()
        self.sampler = None
        self.run = None
        self.events = []
        self._next_event_seq = 0
        self._start_lock = threading.Lock()
        self.live = {}
        self._responses = set()
        self._responses_lock = threading.Lock()

    # -- event plumbing ----------------------------------------------------
    def emit(self, kind, **payload):
        with self.lock:
            self.events.append(dict(payload, kind=kind, seq=self._next_event_seq,
                                    t=time.time()))
            self._next_event_seq += 1
            if len(self.events) > 4000:
                del self.events[:1000]

    def events_since(self, cursor):
        with self.lock:
            return [e for e in self.events if e["seq"] >= cursor], self._next_event_seq

    def log(self, message, level="info"):
        self.emit("log", message=message, level=level)

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def status(self):
        live = dict(self.live)
        if self.sampler:
            telemetry = self.sampler.now()
            if telemetry:
                live["telemetry"] = telemetry
        return {
            "running": self.running,
            "run_id": (self.run or {}).get("id"),
            "run": store.summarise(self.run) if self.run else None,
            "live": live,
            "cancelling": self.cancel_event.is_set() and self.running,
        }

    # -- lifecycle ---------------------------------------------------------
    def start(self, config):
        with self._start_lock:
            return self._start(config)

    def resume(self, run_id):
        """Continue missing cases in a new self-contained file; preserve the source."""
        with self._start_lock:
            if self.running:
                raise RuntimeError("A benchmark is already running.")
            source = store.load(run_id)
            if not source:
                raise ValueError("No such benchmark.")
            return self._start(source.get("execution_config") or source.get("config"), resume_source=source)

    def extension_options(self, run_id):
        source = store.load(run_id)
        if not source:
            raise ValueError("No such benchmark.")
        return {"models": [m for m in lmstudio.list_models() if m.get("type") != "embeddings"],
                "sizes": source["config"]["sizes"],
                "concurrency_levels": source["config"].get("concurrency_levels", [1])}

    def plan_extension(self, selection):
        source = store.load(selection.get("run_id"))
        if not source:
            raise ValueError("No such benchmark.")
        for key in ("retry_failed", "rerun_existing"):
            if type(selection.get(key, False)) is not bool:
                raise ValueError("%s must be true or false." % key)
        cfg = extension_config(source, selection)
        plan = extension_plan(source, cfg, selection.get("retry_failed", False), selection.get("rerun_existing", False))
        if self.running:
            plan["warnings"].append("A benchmark is running. Wait for it to stop before adding tests.")
        return plan

    def extend(self, selection):
        with self._start_lock:
            if self.running:
                raise RuntimeError("Wait for the running benchmark to stop before adding tests.")
            source = store.load(selection.get("run_id"))
            if not source:
                raise ValueError("No such benchmark.")
            for key in ("retry_failed", "rerun_existing"):
                if type(selection.get(key, False)) is not bool:
                    raise ValueError("%s must be true or false." % key)
            cfg = extension_config(source, selection)
            installed = {m["id"] for m in lmstudio.list_models() if m.get("type") != "embeddings"}
            missing = set(cfg["models"]) - installed
            if missing:
                raise ValueError("Models are not installed: " + ", ".join(sorted(missing)))
            return self._start(cfg, resume_source=source, extend_in_place=True,
                               retry_failed=selection.get("retry_failed", False),
                               rerun_existing=selection.get("rerun_existing", False))

    def _start(self, config, resume_source=None, extend_in_place=False, retry_failed=False,
               rerun_existing=False):
        if self.running:
            raise RuntimeError("A benchmark is already running.")
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        cfg["context_planner_version"] = DEFAULT_CONFIG["context_planner_version"]
        if not cfg["models"]:
            raise ValueError("Select at least one model.")
        cfg["models"] = list(dict.fromkeys(cfg["models"]))
        sizes = sorted({int(s) for s in cfg["sizes"] if int(s) > 0})
        if not sizes:
            raise ValueError("Select at least one prefill size.")
        cfg["sizes"] = sizes
        levels = sorted({int(n) for n in cfg.get("concurrency_levels", [1])})
        if not levels or any(n < 1 or n > 100 for n in levels):
            raise ValueError("Concurrency levels must be between 1 and 100.")
        cfg["concurrency_levels"] = levels

        for key in ("max_tokens", "repeats", "per_test_timeout_s", "load_timeout_s", "power_interval_ms"):
            try:
                cfg[key] = int(cfg[key])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("%s must be a positive integer." % key)
            if cfg[key] < 1:
                raise ValueError("%s must be a positive integer." % key)
        for key in ("context_headroom", "gpu_index"):
            try:
                cfg[key] = int(cfg[key])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("%s must be a non-negative integer." % key)
            if cfg[key] < 0:
                raise ValueError("%s must be a non-negative integer." % key)
        for key in ("other_idle_w", "cpu_tdp_w"):
            try:
                cfg[key] = float(cfg[key])
            except (TypeError, ValueError, OverflowError):
                raise ValueError("%s must be a finite non-negative number." % key)
            if not math.isfinite(cfg[key]) or cfg[key] < 0:
                raise ValueError("%s must be a finite non-negative number." % key)
        fraction = float(cfg["context_safety_fraction"])
        if not math.isfinite(fraction) or not 0 <= fraction <= 0.5:
            raise ValueError("Context safety fraction must be between 0 and 0.5.")
        cfg["context_safety_fraction"] = fraction
        if cfg["context_policy"] not in ("per_test", "fixed"):
            raise ValueError("Context policy must be per_test or fixed.")
        gpu = power.gpu_info(cfg["gpu_index"])
        run = {
            "id": store.new_run_id(gpu.get("name") or "cpu"),
            "created_at": _now_iso(),
            "label": cfg["label"] or (gpu.get("name") or "Unknown GPU"),
            "notes": cfg["notes"],
            "gpu": gpu,
            "host": store.host_info(),
            "lms_version": self.cli.version(),
            "config": cfg,
            "status": "running",
            "results": [],
            "corpus": corpus.master_stats(),
        }
        store.prepare_run(run)
        current_application = copy.deepcopy(run["application"])
        if resume_source:
            prior_execution = resume_source.get("execution_config") or resume_source.get("config") or {}
            if not extend_in_place and prior_execution.get("context_planner_version") != DEFAULT_CONFIG["context_planner_version"]:
                raise ValueError("This run used an older context planner. Start a new benchmark to keep measurements comparable.")
            if resume_source.get("lms_version") != run["lms_version"]:
                raise ValueError("LM Studio version differs. Start a new benchmark.")
            old_class = (resume_source.get("classification") or {}).get("hardware_class")
            if not old_class or old_class != run["classification"]["hardware_class"]:
                raise ValueError("Hardware differs or cannot be verified. Start a new benchmark.")
            if any((resume_source.get("gpu") or {}).get(key) != gpu.get(key)
                   for key in ("name", "vram_total_mb", "driver")):
                raise ValueError("The selected GPU or driver differs. Start a new benchmark.")
            old_hash = (resume_source.get("corpus") or {}).get("sha256")
            if not old_hash or old_hash != run["corpus"]["sha256"]:
                raise ValueError("The corpus differs or cannot be verified. Start a new benchmark.")
            plan = extension_plan(resume_source, cfg, retry_failed, rerun_existing)
            if not plan["total_tests"]:
                raise ValueError("This benchmark has no unfinished tests.")
            current_application = run["application"]
            current_id, current_created = run["id"], run["created_at"]
            run = copy.deepcopy(resume_source)
            run["sessions"] = run.get("sessions") or [{
                "id": "initial", "started_at": run.get("created_at"), "config": copy.deepcopy(run["config"]),
                "application": copy.deepcopy(run.get("application")), "status": run.get("status"),
                "idle": copy.deepcopy(run.get("idle")), "finished_at": run.get("finished_at"),
            }]
            for row in run["results"]:
                row.setdefault("session_id", "initial")
            retry_cases = set()
            if rerun_existing:
                retry_cases = completed_cases(run, cfg)
            elif retry_failed:
                retry_cases = completed_cases(run, cfg) - completed_cases(
                    {"results": [row for row in run["results"] if row.get("status") == "ok"]}, cfg)
            kept = []
            for row in run["results"]:
                selected_retry = bool(completed_cases({"results": [row]}, cfg) & retry_cases)
                selected_cancelled = (row.get("status") == "cancelled" and row.get("model") in cfg["models"]
                                      and bool(row.get("mtp")) in _mtp_modes(cfg, row.get("model"))
                                      and row.get("concurrency", 1) in levels and row.get("target_tokens") in sizes)
                if (selected_retry and (rerun_existing or row.get("status") != "ok")) or selected_cancelled:
                    run.setdefault("previous_attempts", []).append(dict(row, archived_at=current_created,
                        archive_reason="rerun" if rerun_existing else "cancelled" if selected_cancelled else "retry"))
                else:
                    kept.append(row)
            run["results"] = kept
            run["execution_config"] = cfg
            run["config"] = merged_config(resume_source["config"], cfg)
            run["status"] = "running"
            run["updated_at"] = current_created
            run.pop("finished_at", None)
            run.pop("error", None)
            run.pop("interrupted_note", None)
            run["resume_history"] = copy.deepcopy(resume_source.get("resume_history", [])) + [{
                "source_id": resume_source["id"], "resumed_at": _now_iso(),
                "application": current_application, "preserved_results": len(run["results"]),
                "source_status": resume_source.get("status"),
                "source_idle": resume_source.get("idle"),
            }]
            if extend_in_place:
                store.backup(resume_source["id"])
            else:
                run["id"], run["created_at"] = current_id, current_created
                run["resumed_from"] = resume_source["id"]
                run["label"] = (resume_source.get("label") or gpu.get("name") or "Benchmark") + " (resumed)"
        for session in run.get("sessions", []):
            if session.get("status") == "running":
                session["status"] = "interrupted"
                session["interrupted_note"] = "The previous execution ended without a final status. Completed results were preserved."
        session_id = uuid.uuid4().hex[:12]
        run["active_session_id"] = session_id
        run.setdefault("sessions", []).append({
            "id": session_id, "started_at": _now_iso(), "status": "running",
            "config": copy.deepcopy(cfg), "application": current_application,
            "lms_version": run.get("lms_version"), "retry_failed": retry_failed,
            "rerun_existing": rerun_existing,
        })
        store.save(run)
        store.save_config(cfg)

        with self.lock:
            self.events = []
            self._next_event_seq = 0
        self.run = run
        self.cancel_event.clear()
        self.live = {"phase": "starting", "done": len(completed_cases(run, cfg)),
                     "total": len(planned_cases(cfg))}
        self.thread = threading.Thread(target=self._run, args=(run, cfg), daemon=True)
        self.thread.start()
        return run["id"]

    def cancel(self):
        self.cancel_event.set()
        with self._responses_lock:
            responses = list(self._responses)
        for resp in responses:
            threading.Thread(target=resp.close, daemon=True).start()

    # -- the actual work ---------------------------------------------------
    def _run(self, run, cfg):
        sampler = power.PowerSampler(interval_ms=cfg["power_interval_ms"],
                                     gpu_index=cfg["gpu_index"],
                                     other_idle_w=cfg["other_idle_w"],
                                     cpu_tdp_w=cfg["cpu_tdp_w"])
        self.sampler = sampler
        sampler.start()
        try:
            self.log("Run %s started on %s" % (run["id"], run["gpu"].get("name", "unknown GPU")))
            if not self.cli.server_running():
                self.log("LM Studio API not responding - starting it")
                if not self.cli.start_server():
                    raise RuntimeError("Could not start the LM Studio server (`lms server start`).")

            self.live["phase"] = "idle baseline"
            self.emit("phase", phase="idle baseline")
            self.cli.unload_all()
            idle = sampler.measure_idle(2.5)
            run["idle"] = {
                "gpu_w": idle.get("gpu_w_avg"),
                "system_w_est": idle.get("system_w_est"),
                "cpu_util": idle.get("cpu_util_avg"),
                "cpu_w": idle.get("cpu_w_avg"),
                "vram_mb": idle.get("vram_mb_max"),
                "source": sampler.source,
            }
            run["power_source"] = sampler.source
            for session in run.get("sessions", []):
                if session["id"] == run.get("active_session_id"):
                    session["idle"] = copy.deepcopy(run["idle"])
                    session["power_source"] = sampler.source
            if idle.get("gpu_w_avg") is not None:
                self.log("Idle: %.1f W GPU, %.0f W system (estimated), via %s"
                         % (idle["gpu_w_avg"], idle.get("system_w_est") or 0,
                            sampler.source))
            else:
                self.log("Power measurement unavailable (%s) - speed will still be "
                         "measured, wattage will be blank."
                         % (sampler.error or "no supported sensor"), level="warn")
            store.save(run)

            checkpoints = completed_cases(run, cfg)
            done = len(checkpoints)
            self.live["done"] = done
            reps = int(cfg["repeats"])
            fixed = cfg.get("context_policy") == "fixed"
            for model in cfg["models"]:
                if self.cancel_event.is_set():
                    break
                cpt = corpus.DEFAULT_CHARS_PER_TOKEN
                for previous in run["results"]:
                    if (previous.get("model") == model and previous.get("status") == "ok"
                            and previous.get("concurrency", 1) == 1):
                        cpt = previous.get("chars_per_token_after") or corpus.refine_chars_per_token(
                            previous.get("prompt_chars"), previous.get("prompt_tokens"), cpt)
                model_max_ctx = self._model_max_context(model)
                for mtp in _mtp_modes(cfg, model):
                    if self.cancel_event.is_set():
                        break
                    loaded = None
                    eligible = [size for size in cfg["sizes"] if context_budget(size, cfg, 1, model_max_ctx)[0] is not None]
                    for concurrency in cfg["concurrency_levels"]:
                        mode_failed = False
                        for size in cfg["sizes"]:
                            if self.cancel_event.is_set():
                                break
                            pending = [rep for rep in range(reps) if (model, mtp, concurrency, size, rep) not in checkpoints]
                            for previous in run["results"]:
                                if (previous.get("model") == model and bool(previous.get("mtp")) == mtp
                                        and previous.get("concurrency", 1) == concurrency
                                        and previous.get("target_tokens") == size
                                        and previous.get("chars_per_token_after")):
                                    cpt = previous["chars_per_token_after"]
                            if not pending:
                                # A prior capacity failure still governs unmeasured larger sizes.
                                if any(row.get("model") == model and bool(row.get("mtp")) == mtp
                                       and row.get("concurrency", 1) == concurrency and row.get("target_tokens") == size
                                       and row.get("session_id") == run.get("active_session_id")
                                       and row.get("status") == "failed" for row in run["results"]):
                                    mode_failed = True
                                continue
                            tag = {"model": model, "target_tokens": size, "mtp": mtp, "concurrency": concurrency}
                            if mode_failed and cfg["stop_model_after_failure"]:
                                for rep in pending:
                                    done += 1
                                    self._record(run, dict(tag, repeat=rep, status="skipped", fail_kind=FAIL_SKIPPED,
                                                          message="Skipped: a smaller context already failed for this model and concurrency."), done)
                                continue
                            per_request, needed = context_budget(size, cfg, concurrency, model_max_ctx)
                            if per_request is None:
                                for rep in pending:
                                    done += 1
                                    self._record(run, dict(tag, repeat=rep, status="failed", fail_kind=FAIL_UNSUPPORTED,
                                                          message="Prompt plus output and headroom exceeds the model context limit."), done)
                                continue
                            parallel = concurrency
                            if fixed:
                                parallel = max(cfg["concurrency_levels"])
                                per_request, needed = context_budget(max(eligible), cfg, parallel, model_max_ctx)
                            load_key = (model, mtp, needed, parallel)
                            reused = bool(loaded and loaded["key"] == load_key)
                            if reused:
                                ok, detail, offload = loaded["ok"], dict(loaded["detail"], load_s=0), loaded["offload"]
                            else:
                                ok, detail, offload = self._load_with_fallback(model, needed, cfg, mtp, parallel)
                                loaded = {"key": load_key, "ok": ok, "detail": detail, "offload": offload}
                            if self.cancel_event.is_set():
                                break
                            if not ok:
                                mode_failed = True
                                unsupported = bool(detail.get("mtp_unsupported"))
                                for rep in pending:
                                    done += 1
                                    self._record(run, dict(tag, repeat=rep, status="skipped" if unsupported else "failed",
                                                          fail_kind=FAIL_MTP if unsupported else FAIL_LOAD,
                                                          requested_context=needed, per_request_context_length=per_request,
                                                          load_parallel_capacity=parallel, context_policy=cfg["context_policy"],
                                                          message=detail.get("error", "load failed")[:600], load_s=detail.get("load_s")), done)
                                continue
                            vram_loaded = (sampler.now() or {}).get("vram_mb")
                            if cfg["warmup"] and not reused:
                                self._warmup(model, cfg)
                            for rep in pending:
                                if self.cancel_event.is_set():
                                    break
                                done += 1
                                result = self._one_test(run, cfg, model, size, rep, reps, detail, offload,
                                                        needed, vram_loaded, cpt, mtp, concurrency)
                                result.update(per_request_context_length=per_request, requested_total_context=needed,
                                              load_parallel_capacity=parallel, context_policy=cfg["context_policy"],
                                              load_reused=reused)
                                cpt = result.pop("_cpt", cpt)
                                result["chars_per_token_after"] = cpt
                                self._record(run, result, done)
                                if result.get("status") == "cancelled":
                                    break
                                if result.get("fail_kind") == FAIL_TIMEOUT:
                                    raise RuntimeError("Request timed out; stopping to avoid contaminating subsequent tests.")
                                if result.get("status") != "ok":
                                    mode_failed = True
                                    for later in pending:
                                        if later <= rep:
                                            continue
                                        done += 1
                                        self._record(run, dict(tag, repeat=later, status="skipped", fail_kind=FAIL_SKIPPED,
                                                              message="Skipped after an earlier repeat failed."), done)
                                    break
                            if not fixed:
                                self.cli.unload_all()
                                loaded = None
                    self.cli.unload_all()

            run["status"] = "cancelled" if self.cancel_event.is_set() else "completed"
            run["finished_at"] = _now_iso()
            self._finish_session(run)
            store.save(run)
            self.log("Run %s: %s" % (run["id"], run["status"]),
                     level="warn" if run["status"] == "cancelled" else "ok")
            self.emit("done", run_id=run["id"], status=run["status"])
        except Exception as exc:                     # noqa: BLE001 - surfaced to the UI
            run["status"] = "error"
            run["error"] = str(exc)
            run["finished_at"] = _now_iso()
            self._finish_session(run)
            store.save(run)
            self.log("Run failed: %s" % exc, level="error")
            self.emit("done", run_id=run["id"], status="error", error=str(exc))
        finally:
            self.live["phase"] = "idle"
            try:
                self.cli.unload_all()
            except Exception:                        # noqa: BLE001
                pass
            sampler.stop()

    def _model_max_context(self, model):
        try:
            for m in lmstudio.list_models():
                if m.get("id") == model:
                    return m.get("max_context_length")
        except Exception:                            # noqa: BLE001
            pass
        return None

    def _load_with_fallback(self, model, needed, cfg, mtp=False, concurrency=1):
        """Try the configured placement, then progressively more CPU offload.

        Recording *which* placement worked is half the point: "ran, but only with
        half the layers on the CPU" is a different answer from "ran on the GPU".
        """
        attempts = [cfg["gpu_offload"]]
        if cfg["fallback_on_failure"]:
            attempts += [a for a in FALLBACK_LADDER if a != cfg["gpu_offload"]]
        last = {}
        for attempt in attempts:
            if self.cancel_event.is_set():
                return False, {"error": "cancelled"}, attempt
            self.live.update({"phase": "loading", "model": model, "mtp": mtp,
                              "offload": attempt, "context": needed})
            self.emit("phase", phase="loading", model=model, offload=attempt,
                      context=needed, mtp=mtp)
            self.log("Loading %s at ctx=%s (offload=%s, mtp=%s)"
                     % (model, format(needed, ","), attempt, "on" if mtp else "off"))
            ok, detail = self.cli.load(model, needed, gpu=attempt, parallel=concurrency,
                                       timeout=cfg["load_timeout_s"], mtp=mtp)
            if ok:
                return True, detail, attempt
            last = detail
            if detail.get("mtp_unsupported"):
                # no amount of CPU offload will add an MTP head to the file
                self.log("%s has no MTP head - skipping its MTP pass" % model, level="warn")
                return False, detail, attempt
            self.log("Load failed at offload=%s: %s"
                     % (attempt, (detail.get("error") or "")[:200]), level="warn")
        return False, last, attempts[-1]

    def _warmup(self, model, cfg):
        """A tiny request so the first measured prefill is not paying for lazy init."""
        self.live["phase"] = "warmup"
        self.emit("phase", phase="warmup", model=model)
        try:
            lmstudio.measure_completion(
                model, "Reply with the single word: ready.", 8,
                timeout=min(300, cfg["per_test_timeout_s"]),
                cancel=self.cancel_event,
                disable_thinking=cfg["disable_thinking"])
        except Exception:                            # noqa: BLE001 - warmup is best effort
            pass

    def _one_test(self, run, cfg, model, size, rep, reps, load_detail, offload,
                  needed, vram_loaded, cpt, mtp=False, concurrency=1):
        prompt, meta = corpus.build_prompt(size, chars_per_token=cpt)
        label = "%s @ %s%s" % (model, format(size, ","), " +MTP" if mtp else "")
        self.live.update({"phase": "prefill", "model": model, "size": size,
                          "rep": rep + 1, "reps": reps, "ttft_s": None,
                          "tokens_out": 0, "offload": offload, "mtp": mtp})
        self.emit("phase", phase="prefill", model=model, size=size,
                  rep=rep + 1, reps=reps, mtp=mtp)
        self.log("Testing %s (rep %d/%d, %s chars)"
                 % (label, rep + 1, reps, format(len(prompt), ",")))

        base = {
            "model": model,
            "target_tokens": size,
            "mtp": mtp,
            "concurrency": concurrency,
            "repeat": rep,
            "requested_context": needed,
            "context_length": load_detail.get("context_length") or needed,
            "gpu_offload": offload,
            "quantization": load_detail.get("quantization"),
            "architecture": load_detail.get("architecture"),
            "params": load_detail.get("params"),
            "load_s": load_detail.get("load_s"),
            "prompt_chars": len(prompt),
            "corpus_wraps": meta["corpus_wraps"],
            "nonce": meta["nonce"],
            "max_tokens": cfg["max_tokens"],
            "reasoning_disabled": bool(cfg["disable_thinking"]),
            "timestamp": _now_iso(),
            "_cpt": cpt,
        }

        if concurrency > 1:
            return self._concurrent_test(base, cfg, model, prompt, concurrency, vram_loaded)

        def on_first(ttft):
            self.live.update({"phase": "decode", "ttft_s": ttft})
            self.emit("phase", phase="decode", model=model, size=size, ttft_s=ttft)

        def on_progress(count, elapsed):
            self.live.update({"tokens_out": count,
                              "decode_tps_live": (count / elapsed) if elapsed > 0 else None})

        def keep_response(resp):
            with self._responses_lock:
                self._responses.add(resp)

        try:
            m = lmstudio.measure_completion(
                model, prompt, cfg["max_tokens"],
                timeout=cfg["per_test_timeout_s"],
                on_first_token=on_first, on_progress=on_progress,
                cancel=self.cancel_event,
                disable_thinking=cfg["disable_thinking"],
                on_response=keep_response)
        except lmstudio.Cancelled:
            base.update({"status": "cancelled", "fail_kind": "cancelled",
                         "message": "Cancelled by the user."})
            return base
        except Exception as exc:                     # noqa: BLE001
            message = str(exc)
            if self.cancel_event.is_set():
                # closing the response to abort a blocked read surfaces as an
                # I/O error rather than Cancelled
                base.update({"status": "cancelled", "fail_kind": "cancelled",
                             "message": "Cancelled by the user."})
                return base
            base.update({"status": "failed", "fail_kind": _classify(message),
                         "message": message[:800]})
            self.log("%s failed: %s" % (label, message[:300]), level="error")
            return base
        finally:
            with self._responses_lock:
                self._responses.clear()

        prefill = self.sampler.window(m["t_start"], m["t_first_token"])
        decode = self.sampler.window(m["t_first_token"], m["t_end"])
        cpt_new = corpus.refine_chars_per_token(len(prompt), m["prompt_tokens"], cpt)

        prompt_tokens = m["prompt_tokens"] or 0
        completion_tokens = m["completion_tokens"] or 0
        prefill_j = prefill.get("gpu_energy_j")
        decode_j = decode.get("gpu_energy_j")
        prefill_sys_j = prefill.get("system_energy_j_est")
        decode_sys_j = decode.get("system_energy_j_est")

        base.update({
            "status": "ok",
            "_cpt": cpt_new,
            "prompt_tokens": m["prompt_tokens"],
            "completion_tokens": m["completion_tokens"],
            "completion_tokens_estimated": m.get("completion_tokens_estimated", False),
            "reasoning_tokens": m["reasoning_tokens"],
            "target_error_pct": (100.0 * (prompt_tokens - size) / size) if size else None,
            "ttft_s": m["ttft_s"],
            "decode_s": m["decode_s"],
            "wall_s": m["wall_s"],
            "prefill_tps": m["prefill_tps"],
            "decode_tps": m["decode_tps"],
            "finish_reason": m["finish_reason"],
            "sample_text": m["sample_text"],
            "vram_after_load_mb": vram_loaded,
            "vram_peak_mb": max([v for v in (prefill.get("vram_mb_max"),
                                             decode.get("vram_mb_max"), vram_loaded)
                                 if v is not None] or [None]) if any(
                v is not None for v in (prefill.get("vram_mb_max"),
                                        decode.get("vram_mb_max"), vram_loaded)) else None,
            "power": {
                "available": bool(prefill.get("available") or decode.get("available")),
                "low_confidence": bool(prefill.get("low_confidence")),
                # which tool produced these watts: nvidia-smi reports board power
                # for the card alone, powermetrics reports SoC rails. Recorded so
                # a cross-platform comparison can say what it is comparing.
                "source": prefill.get("source") or decode.get("source"),
                "prefill_cpu_w": prefill.get("cpu_w_avg"),
                "decode_cpu_w": decode.get("cpu_w_avg"),
                "prefill_gpu_w": prefill.get("gpu_w_avg"),
                "prefill_gpu_w_max": prefill.get("gpu_w_max"),
                "decode_gpu_w": decode.get("gpu_w_avg"),
                "decode_gpu_w_max": decode.get("gpu_w_max"),
                "prefill_system_w_est": prefill.get("system_w_est"),
                "decode_system_w_est": decode.get("system_w_est"),
                "prefill_energy_j": prefill_j,
                "decode_energy_j": decode_j,
                "prefill_system_energy_j_est": prefill_sys_j,
                "decode_system_energy_j_est": decode_sys_j,
                "j_per_1k_prefill": (prefill_j / prompt_tokens * 1000.0)
                                    if (prefill_j and prompt_tokens) else None,
                "j_per_1k_prefill_system_est": (prefill_sys_j / prompt_tokens * 1000.0)
                                               if (prefill_sys_j and prompt_tokens) else None,
                "j_per_token_decode": (decode_j / completion_tokens)
                                      if (decode_j and completion_tokens) else None,
                "j_per_token_decode_system_est": (decode_sys_j / completion_tokens)
                                                 if (decode_sys_j and completion_tokens) else None,
                "prefill_gpu_util": prefill.get("gpu_util_avg"),
                "decode_gpu_util": decode.get("gpu_util_avg"),
                "prefill_cpu_util": prefill.get("cpu_util_avg"),
                "decode_cpu_util": decode.get("cpu_util_avg"),
                "temp_c_max": max([t for t in (prefill.get("temp_c_max"),
                                               decode.get("temp_c_max"))
                                   if t is not None] or [None]) if any(
                    t is not None for t in (prefill.get("temp_c_max"),
                                            decode.get("temp_c_max"))) else None,
                "clock_mhz_avg": decode.get("clock_mhz_avg"),
                "samples": (prefill.get("samples", 0) or 0) + (decode.get("samples", 0) or 0),
            },
        })
        self.log("%s  prefill %s tk/s  decode %s tk/s  %s"
                 % (label,
                    "%.0f" % m["prefill_tps"] if m["prefill_tps"] else "n/a",
                    "%.1f" % m["decode_tps"] if m["decode_tps"] else "n/a",
                    "%.0f W GPU" % prefill["gpu_w_avg"] if prefill.get("gpu_w_avg") else ""),
                 level="ok")
        return base

    def _concurrent_test(self, base, cfg, model, prompt, concurrency, vram_loaded):
        """Run a group of streams together; group power covers the entire window."""
        results = [None] * concurrency
        # A unique leading nonce prevents prefix cache reuse between requests.
        prompts = []
        for _ in range(concurrency):
            nonce = uuid.uuid4().hex[:12]
            prompts.append((prompt.replace("Benchmark request " + base["nonce"],
                                           "Benchmark request " + nonce, 1), nonce))
        gate = threading.Event()
        group_cancel = threading.Event()
        class CancelView:
            def is_set(inner):
                return self.cancel_event.is_set() or group_cancel.is_set()
        cancel = CancelView()
        response_lock = threading.Lock()
        responses = set()
        def worker(index):
            def register(resp):
                with response_lock:
                    responses.add(resp)
                with self._responses_lock:
                    self._responses.add(resp)
                if cancel.is_set():
                    resp.close()
            gate.wait()
            try:
                if cancel.is_set():
                    raise lmstudio.Cancelled()
                m = lmstudio.measure_completion(
                    model, prompts[index][0], cfg["max_tokens"], timeout=cfg["per_test_timeout_s"],
                    cancel=cancel, disable_thinking=cfg["disable_thinking"],
                    on_response=register)
                results[index] = {"status": "ok", "nonce": prompts[index][1], **{k: v for k, v in m.items()
                                  if not k.startswith("t_")}}
            except Exception as exc:
                message = str(exc)
                results[index] = {"nonce": prompts[index][1], "status": "cancelled" if self.cancel_event.is_set() else "failed",
                                  "fail_kind": "cancelled" if self.cancel_event.is_set() else _classify(message),
                                  "message": message[:800]}
        threads = [threading.Thread(target=worker, args=(i,), daemon=True)
                   for i in range(concurrency)]
        for thread in threads:
            thread.start()
        start = time.perf_counter()
        gate.set()
        deadline = start + float(cfg["per_test_timeout_s"])
        while any(t.is_alive() for t in threads):
            if self.cancel_event.is_set() or time.perf_counter() >= deadline:
                group_cancel.set()
                with response_lock:
                    active = list(responses)
                # Closing a response may block on an in-flight socket read.
                for resp in active:
                    threading.Thread(target=resp.close, daemon=True).start()
                break
            for thread in threads:
                thread.join(timeout=min(0.02, max(0, deadline - time.perf_counter())))
        end = time.perf_counter()
        with self._responses_lock:
            self._responses.difference_update(responses)
        timed_out = not self.cancel_event.is_set() and any(t.is_alive() for t in threads)
        snapshot = [r if r is not None else {
            "nonce": prompts[i][1], "status": "cancelled" if self.cancel_event.is_set() else "failed",
            "fail_kind": "cancelled" if self.cancel_event.is_set() else FAIL_TIMEOUT,
            "message": "Cancelled by the user." if self.cancel_event.is_set() else "Group timed out."}
            for i, r in enumerate(results)]
        successes = [r for r in snapshot if r["status"] == "ok"]
        window = self.sampler.window(start, end)
        wall = end - start
        base.update({"status": "cancelled" if self.cancel_event.is_set() else
                     ("ok" if len(successes) == concurrency else "failed"),
                     "requests": snapshot, "completed_requests": len(successes),
                     "wall_s": wall, "aggregate_completion_tokens": sum(r.get("completion_tokens") or 0 for r in successes),
                     "aggregate_completion_tps": (sum(r.get("completion_tokens") or 0 for r in successes) / wall) if wall else None,
                     "aggregate_completion_tokens_estimated": any(r.get("completion_tokens_estimated") for r in successes),
                     "vram_after_load_mb": vram_loaded,
                     "group_power": window})
        if base["status"] != "ok":
            failure = next((r for r in snapshot if r["status"] != "ok"), {})
            base.update({"fail_kind": FAIL_TIMEOUT if timed_out else failure.get("fail_kind", FAIL_ERROR),
                         "message": failure.get("message", "One or more requests failed.")})
        return base

    def _record(self, run, result, done):
        if run.get("active_session_id"):
            result["session_id"] = run["active_session_id"]
        run["results"].append(result)
        store.save(run)
        self.live["done"] = done
        self.emit("result", result=result, done=done,
                  total=self.live.get("total"), run_id=run["id"])

    def _finish_session(self, run):
        for session in run.get("sessions", []):
            if session["id"] == run.get("active_session_id"):
                session.update(status=run["status"], finished_at=run.get("finished_at"))
                if run.get("error"):
                    session["error"] = run["error"]
        # A selected addition may finish while another session still has missing cases.
        requested = set()
        for session in run.get("sessions", []):
            cfg = session.get("config")
            if cfg:
                requested.update(planned_cases(dict(DEFAULT_CONFIG, **cfg)))
        if requested:
            present = completed_cases(run, dict(DEFAULT_CONFIG, **run["config"]))
            run["remaining_tests"] = len(requested - present)
            if run["status"] == "completed" and run["remaining_tests"]:
                run["status"] = "interrupted"
                run["interrupted_note"] = "The selected tests finished. Other requested tests remain unfinished; use Add tests to continue them."
