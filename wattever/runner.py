"""Benchmark orchestration.

One test = (model, prefill size, repeat). For each test the runner reloads the
model with a context length sized to that prefill target, because context length
is a load-time decision and loading 80k of context "just in case" would fail on
a small card and distort every smaller measurement with cache pressure that the
workload does not actually have.

A load that fails is a result, not an error: "this card cannot hold this model at
this context" is precisely what the user is trying to find out.
"""
import datetime
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


def _mtp_modes(cfg):
    """Config carries strings ("off"/"on") so it round-trips through JSON cleanly."""
    modes = cfg.get("mtp_modes") or ["off"]
    out = []
    for mode in modes:
        flag = mode is True or str(mode).lower() in ("on", "true", "1", "mtp")
        if flag not in out:
            out.append(flag)
    return out or [False]


def _now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


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
        self.live = {}
        self._response = None

    # -- event plumbing ----------------------------------------------------
    def emit(self, kind, **payload):
        with self.lock:
            self.events.append(dict(payload, kind=kind, seq=len(self.events),
                                    t=time.time()))
            if len(self.events) > 4000:
                del self.events[:1000]

    def events_since(self, cursor):
        with self.lock:
            return [e for e in self.events if e["seq"] >= cursor], len(self.events)

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
        if self.running:
            raise RuntimeError("A benchmark is already running.")
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(config or {})
        if not cfg["models"]:
            raise ValueError("Select at least one model.")
        sizes = sorted({int(s) for s in cfg["sizes"] if int(s) > 0})
        if not sizes:
            raise ValueError("Select at least one prefill size.")
        cfg["sizes"] = sizes

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
        store.save(run)
        store.save_config(cfg)

        with self.lock:
            self.events = []
        self.run = run
        self.cancel_event.clear()
        self.live = {"phase": "starting", "done": 0,
                     "total": (len(cfg["models"]) * len(sizes) * len(_mtp_modes(cfg))
                               * max(1, int(cfg["repeats"])))}
        self.thread = threading.Thread(target=self._run, args=(run, cfg), daemon=True)
        self.thread.start()
        return run["id"]

    def cancel(self):
        self.cancel_event.set()
        resp = self._response
        if resp is not None:
            try:
                resp.close()          # unblock a read that is waiting on prefill
            except OSError:
                pass

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
            if idle.get("gpu_w_avg") is not None:
                self.log("Idle: %.1f W GPU, %.0f W system (estimated), via %s"
                         % (idle["gpu_w_avg"], idle.get("system_w_est") or 0,
                            sampler.source))
            else:
                self.log("Power measurement unavailable (%s) - speed will still be "
                         "measured, wattage will be blank."
                         % (sampler.error or "no supported sensor"), level="warn")
            store.save(run)

            done = 0
            reps = max(1, int(cfg["repeats"]))
            for model in cfg["models"]:
                if self.cancel_event.is_set():
                    break
                # the tokeniser ratio is a property of the model, so it is
                # learned once and reused across MTP modes and sizes
                cpt = corpus.DEFAULT_CHARS_PER_TOKEN
                model_max_ctx = self._model_max_context(model)
                for mtp in _mtp_modes(cfg):
                    if self.cancel_event.is_set():
                        break
                    mode_failed = False
                    for size in cfg["sizes"]:
                        if self.cancel_event.is_set():
                            break
                        tag = {"model": model, "target_tokens": size, "mtp": mtp}
                        if mode_failed and cfg["stop_model_after_failure"]:
                            done += reps
                            self._record(run, dict(tag, **{
                                "status": "skipped", "fail_kind": FAIL_SKIPPED,
                                "message": "Skipped: a smaller context already failed "
                                           "for this model%s." % (" with MTP" if mtp else ""),
                            }), done)
                            continue

                        needed = size + int(cfg["max_tokens"]) + int(cfg["context_headroom"])
                        if model_max_ctx and needed > model_max_ctx:
                            done += reps
                            self._record(run, dict(tag, **{
                                "status": "failed", "fail_kind": FAIL_UNSUPPORTED,
                                "message": "Needs %s tokens of context; the model supports %s."
                                           % (format(needed, ","), format(model_max_ctx, ",")),
                            }), done)
                            continue

                        ok, detail, offload = self._load_with_fallback(model, needed, cfg, mtp)
                        if not ok:
                            mode_failed = True
                            unsupported = bool(detail.get("mtp_unsupported"))
                            done += reps
                            self._record(run, dict(tag, **{
                                "status": "skipped" if unsupported else "failed",
                                "fail_kind": FAIL_MTP if unsupported else FAIL_LOAD,
                                "requested_context": needed,
                                "message": ("This model has no bundled MTP head, so MTP "
                                            "speculative decoding does not apply to it."
                                            if unsupported
                                            else detail.get("error", "load failed")[:600]),
                                "load_s": detail.get("load_s"),
                            }), done)
                            continue

                        vram_loaded = (sampler.now() or {}).get("vram_mb")
                        self.log("Loaded %s  ctx=%s  offload=%s  mtp=%s  (%.1fs)"
                                 % (model, format(detail.get("context_length") or needed, ","),
                                    offload, "on" if mtp else "off", detail.get("load_s") or 0))

                        if cfg["warmup"]:
                            self._warmup(model, cfg)

                        for rep in range(reps):
                            if self.cancel_event.is_set():
                                break
                            done += 1
                            result = self._one_test(run, cfg, model, size, rep, reps,
                                                    detail, offload, needed, vram_loaded,
                                                    cpt, mtp)
                            cpt = result.pop("_cpt", cpt)
                            self._record(run, result, done)
                            if result.get("status") != "ok":
                                mode_failed = True
                                break

                        self.cli.unload_all()

            run["status"] = "cancelled" if self.cancel_event.is_set() else "completed"
            run["finished_at"] = _now_iso()
            store.save(run)
            self.log("Run %s: %s" % (run["id"], run["status"]),
                     level="warn" if run["status"] == "cancelled" else "ok")
            self.emit("done", run_id=run["id"], status=run["status"])
        except Exception as exc:                     # noqa: BLE001 - surfaced to the UI
            run["status"] = "error"
            run["error"] = str(exc)
            run["finished_at"] = _now_iso()
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

    def _load_with_fallback(self, model, needed, cfg, mtp=False):
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
            ok, detail = self.cli.load(model, needed, gpu=attempt, parallel=1,
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
                  needed, vram_loaded, cpt, mtp=False):
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

        def on_first(ttft):
            self.live.update({"phase": "decode", "ttft_s": ttft})
            self.emit("phase", phase="decode", model=model, size=size, ttft_s=ttft)

        def on_progress(count, elapsed):
            self.live.update({"tokens_out": count,
                              "decode_tps_live": (count / elapsed) if elapsed > 0 else None})

        def keep_response(resp):
            self._response = resp

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
            self._response = None

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

    def _record(self, run, result, done):
        run["results"].append(result)
        store.save(run)
        self.live["done"] = done
        self.emit("result", result=result, done=done,
                  total=self.live.get("total"), run_id=run["id"])
