"""Local HTTP server: JSON API + the single-page UI.

Standard library only, so the launcher works on any Python 3.9+ install with no
pip step. Binds to localhost only.
"""
import json
import copy
import errno
import hashlib
import mimetypes
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from wattever import __version__, corpus, endpoints, hardware, lmstudio, platform_util, power, runner, store
else:
    from . import __version__, corpus, endpoints, hardware, lmstudio, platform_util, power, runner, store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
from wattever.privacy import public_projection
DEFAULT_PORT = 8713

# The four models this was built for. Matching is loose because publishers
# rename and quantisation suffixes vary; a model still downloading simply will
# not appear in LM Studio's list yet, and the UI says so.
# Each entry lists patterns in order of preference, because publishers ship the
# same model at several quantisations under different ids and the difference
# decides whether it fits in VRAM at all: the 27B is 17.7 GB at Q4_K_M and
# 15.2 GB at IQ4_XS, which is the difference between fitting in 16 GB and not.
DEFAULT_MODEL_PATTERNS = [
    ("google/gemma-4-e4b", [r"gemma-?4.*e4b"]),
    ("qwen/qwen3.5-9b", [r"qwen3\.5-9b"]),
    ("qwen/qwen3.6-35b-a3b", [r"qwen3\.6-35b-a3b"]),
    ("unsloth/Qwen3.8-27B-GGUF (IQ4_XS)",
     [r"unsloth.*qwen3\.8.*27b", r"qwen3\.8.*27b"]),
]

BENCH = runner.BenchRunner()
CAP_BENCH = None
WORK_LOCK = threading.RLock()


def build_id():
    """Identify the Python/UI build, including unreleased working-tree edits."""
    from pathlib import Path
    digest = hashlib.sha256()
    for folder in (Path(ROOT) / "wattever", Path(WEB_DIR)):
        for path in sorted(folder.iterdir()):
            if path.suffix in (".py", ".js", ".html", ".css"):
                digest.update(path.name.encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()[:20]


BUILD_ID = build_id()


def _cap_bench():
    global CAP_BENCH
    with WORK_LOCK:
        if CAP_BENCH is None:
            from . import capabilities, datasets
            datasets.register_bundled_suites()
            CAP_BENCH = capabilities.CapabilityRunner(on_run_start=_cap_telemetry_start,
                                                      on_run_finish=_cap_telemetry_finish)
    return CAP_BENCH


def _cap_telemetry_start(run, cfg):
    client = hardware.inventory()
    selected = next((g for g in client.get("gpus", []) if g.get("sensor_index") == cfg.get("gpu_index", 0)), None)
    metadata = {"hardware": client, "gpu": selected}
    runner._attach_transfer_profile(metadata, dict(cfg, hardware_probe_policy="defer"))
    run["hardware_probe"] = metadata["hardware_probe"]
    run["hardware_probe"]["reason"] = "Capability evaluations never launch RAM/storage/CUDA transfer microbenchmarks beside a loaded model."
    run["measurement_client"] = {"hardware": client, "scope": "load-generating benchmark client"}
    run["measurement_scope"] = {"inference_hardware_source": "user_reported_local" if cfg.get("endpoint_inference_local") else "unknown",
                                "inference_hardware_verified": False,
                                "power_boundary": "selected GPU and whole-client CPU/RAM; other workloads may contribute" if cfg.get("endpoint_inference_local") else "unavailable for remote/unverified inference"}
    if not cfg.get("endpoint_inference_local"):
        run["inference_hardware"] = None
        return None
    run["inference_hardware"] = client
    sampler = power.PowerSampler(interval_ms=cfg.get("power_interval_ms", 500),
                                 gpu_index=cfg.get("gpu_index", 0))
    sampler.start()
    if hasattr(sampler, 'measurement_scope'):
        run['measurement_scope'].update(sampler.measurement_scope())
    return sampler


def _cap_telemetry_finish(run, sampler):
    if sampler is not None:
        sampler.stop()
        run["telemetry"] = {"history": sampler.history(), "summary": sampler.summary()}


def _any_running():
    return BENCH.running or bool(CAP_BENCH and CAP_BENCH.running)


def _other_helper_running(current_port):
    """A preview must not interfere with an active default helper instance."""
    if current_port == DEFAULT_PORT: return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/status" % DEFAULT_PORT, timeout=2) as response:
            if not response.headers.get("Server", "").startswith(("Wattever/", "GpuBench/")): return False
            status = json.load(response)
        return bool(status.get("running") or status.get("capability_running"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _mark_interrupted(run):
    """Relabel a run left stuck on "running" by a hard kill.

    The final status is stamped at the end of the benchmark loop, so closing the
    console window or pulling the power leaves the file saying "running" forever
    even though every completed test in it is intact. This is corrected for
    display only -- nothing is rewritten, so there is no chance of stamping over
    a run that some other server instance is genuinely still executing.
    """
    if not run or run.get("status") != "running":
        return run
    if BENCH.running and (BENCH.run or {}).get("id") == run.get("id"):
        return run                      # this one really is in progress
    run = dict(run)
    run["status"] = "interrupted"
    run["interrupted_note"] = ("The benchmark was stopped before it finished "
                               "(console closed, or the machine went down). "
                               "Every test listed below completed and is valid.")
    return run


def default_selection(models):
    """Return (selected ids, descriptions of defaults that are not installed yet)."""
    ids = [m["id"] for m in models if m.get("type") != "embeddings"]
    selected, missing = [], []
    for label, patterns in DEFAULT_MODEL_PATTERNS:
        hit = None
        for pattern in patterns:          # first pattern that matches anything wins
            hit = next((i for i in ids if re.search(pattern, i, re.I)), None)
            if hit:
                break
        if hit:
            selected.append(hit)
        else:
            missing.append(label)
    return selected, missing


class Handler(BaseHTTPRequestHandler):
    server_version = "Wattever/" + __version__
    protocol_version = "HTTP/1.1"

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    # -- helpers -----------------------------------------------------------
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)
        except OSError as exc:
            if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)) or exc.errno in (errno.EPIPE, errno.ECONNRESET, errno.ECONNABORTED) or getattr(exc, "winerror", None) in (10053, 10054):
                self.close_connection = True
                return False
            raise
        return True

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, allow_nan=False, default=str))

    def _error(self, code, message):
        # A rejected request can still have an unread body. Closing avoids
        # interpreting it as the next request on an HTTP/1.1 connection.
        self.close_connection = True
        self._send(code, json.dumps({"error":message}), extra={"Connection":"close"})

    def _work_conflict(self):
        if _any_running(): return "A benchmark is already running."
        if _other_helper_running(self.server.server_port):
            return "The helper on port %d is benchmarking. Finish that run before starting another helper job." % DEFAULT_PORT
        return None

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValueError("Invalid Content-Length.") from None
        if length < 0 or length > 32 * 1024 * 1024:
            raise ValueError("Request body must be at most 32 MiB.")
        if not length:
            return {}
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict): raise ValueError("Expected a JSON object.")
            return value
        except (ValueError, OSError):
            raise ValueError("Request body must be valid JSON.") from None

    def _mutation_guard(self):
        """Permit CLI clients and this helper's own browser origin only.

        Endpoint credentials live in the helper process. An unrelated webpage
        must not be able to forward a named environment secret to its server.
        """
        host = urllib.parse.urlsplit("http://" + self.headers.get("Host", ""))
        try: port = host.port or 80
        except ValueError: return "Invalid local Host."
        if host.hostname not in ("localhost", "127.0.0.1", "::1") or port != self.server.server_port:
            return "This helper accepts loopback Host names only."
        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            try: origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError: return "Invalid request Origin."
            if parsed.scheme != "http" or parsed.hostname != host.hostname or origin_port != port:
                return "Cross-origin changes to the local helper are not allowed."
        if self.command == "POST" and self.headers.get("Content-Length", "0") != "0":
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return "JSON requests require Content-Type: application/json."
        return None

    def _static(self, rel):
        rel = rel.lstrip("/") or "index.html"
        path = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not path.startswith(WEB_DIR) or not os.path.isfile(path):
            return self._error(404, "not found")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # -- routes ------------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                return self._static("index.html")
            if route.startswith("/static/"):
                return self._static(route[len("/static/"):])
            if route == "/api/env":
                return self._json(self.env_payload())
            if route == "/api/bench/extend-plan":
                return self._json(BENCH.extension_options((query.get("run_id") or [None])[0]))
            if route == "/api/version":
                return self._json({"application": "Wattever", "version": __version__, "build_id": BUILD_ID,
                                   "api_schema_version": 3,
                                   "features": {"atomic_expected_run_id_cancel": True,
                                                "external_requested_mtp_metadata": True,
                                                "capability_start_transfer_probes": False,
                                                "hardware_probe_policy": ["auto", "defer"]}})
            if route == "/api/capabilities/suites":
                from . import capabilities
                _cap_bench()
                return self._json({"suites": capabilities.list_suites(), "runner": {
                    "completion_policy": capabilities.COMPLETION_POLICY,
                    "defaults": {key: capabilities.DEFAULT_CONFIG[key] for key in ("max_tokens", "timeout_s")},
                    "limits": {"max_tokens": {"min": 1, "max": 131072},
                               "timeout_s": {"min": 0, "max": 86400}},
                }})
            if route == "/api/capabilities/status":
                return self._json(_cap_bench().status())
            if route == "/api/capabilities/events":
                events, cursor = _cap_bench().events_since(int((query.get("cursor") or ["0"])[0]))
                return self._json({"events": events, "cursor": cursor, "status": _cap_bench().status()})
            if route == "/api/capabilities/runs":
                return self._json({"runs": _cap_bench().list_runs()})
            if route.startswith("/api/capabilities/runs/"):
                run_id = urllib.parse.unquote(route[len("/api/capabilities/runs/"):])
                download = run_id.endswith("/download")
                run_id = run_id[:-len("/download")] if download else run_id
                run = _cap_bench().load(run_id)
                if run is None: return self._error(404, "No such capability run.")
                if download:
                    return self._send(200, json.dumps(_cap_bench().export(run_id), ensure_ascii=False, allow_nan=False),
                                      extra={"Content-Disposition": 'attachment; filename="%s.json"' % run_id})
                return self._json(run)
            if route == "/api/capabilities/export.json":
                ids = (query.get("ids") or [""])[0].split(",")
                runs = [_cap_bench().export(r["id"]) for r in _cap_bench().list_runs() if not ids[0] or r["id"] in ids]
                return self._json({"capability_runs": runs})
            if route == "/api/models":
                cfg = {k: v[0] for k, v in query.items()}
                if cfg.get("endpoint_api_key_env"):
                    return self._error(400, "Use same-origin POST /api/endpoint/check for authenticated discovery.")
                if cfg.get("provider") == "openai":
                    cfg.pop("endpoint_inference_local", None)
                    return self._json(self.endpoint_payload(cfg))
                return self._json({"models": self.models_payload()})
            if route == "/api/status":
                return self._json(dict(BENCH.status(), capability_running=bool(CAP_BENCH and CAP_BENCH.running)))
            if route == "/api/events":
                cursor = int((query.get("cursor") or ["0"])[0])
                events, total = BENCH.events_since(cursor)
                return self._json({"events": events, "cursor": total,
                                   "status": BENCH.status()})
            if route == "/api/runs":
                return self._json({"runs": [_mark_interrupted(r) for r in store.list_runs()]})
            if route.startswith("/api/runs/"):
                run_id = urllib.parse.unquote(route[len("/api/runs/"):])
                download = run_id.endswith("/download")
                if download:
                    run_id = run_id[:-len("/download")]
                run = store.load(run_id)
                if run and download:
                    return self._send(200, json.dumps(public_projection(run), indent=1, ensure_ascii=False,
                                                     allow_nan=False),
                                      extra={"Content-Disposition": 'attachment; filename="%s.json"' % run_id})
                return self._json(_mark_interrupted(run)) if run else self._error(404, "no such run")
            if route == "/api/export.csv":
                runs = public_projection(self._selected_runs(query))
                return self._send(200, store.to_csv(runs), "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=gpu-bench.csv"})
            if route == "/api/export.json":
                runs = public_projection(self._selected_runs(query))
                return self._send(200, json.dumps({"runs": runs}, indent=1, default=str),
                                  "application/json; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=gpu-bench.json"})
            if route == "/api/preview":
                target = int((query.get("tokens") or ["512"])[0])
                prompt, meta = corpus.build_prompt(target)
                return self._json({"meta": meta, "head": prompt[:1800],
                                   "tail": prompt[-700:]})
            return self._error(404, "not found")
        except (ValueError, RuntimeError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:                     # noqa: BLE001
            return self._error(500, str(exc))

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        denied = self._mutation_guard()
        if denied:
            self.close_connection = True
            return self._error(403, denied)
        try:
            if route == "/api/hardware/gpu-setup":
                with WORK_LOCK:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before changing GPU placement.")
                    from . import gpu_setup
                    return self._json(gpu_setup.stage(self._body()))
            if route == "/api/bench/consolidate":
                with WORK_LOCK:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before consolidating results.")
                    from .consolidation import consolidate
                    body = self._body()
                    return self._json(store.summarise(consolidate(body.get("run_ids"), body.get("label"))))
            if route == "/api/bench/link":
                with WORK_LOCK:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before linking results.")
                    body = self._body()
                    return self._json(store.link_runs(body.get("run_ids"), body.get("label", "Machine benchmark")))
            if route == "/api/hardware/memory":
                with WORK_LOCK:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before detecting memory topology.")
                    from . import memory_topology
                    body = self._body()
                    gpu_index = body.get("gpu_index", 0)
                    if type(gpu_index) is not int or not 0 <= gpu_index <= 255:
                        raise ValueError("GPU sensor index must be an integer in 0..255.")
                    target = store.load(body["run_id"]) if body.get("run_id") else None
                    if body.get("run_id") and target is None:
                        return self._error(404, "no such run")
                    current = {"hardware": hardware.inventory(), "gpu": power.gpu_info(gpu_index)}
                    topology = current["hardware"]["memory"]["channel_topology"]
                    if target:
                        memory_topology.backfill(copy.deepcopy(target), current, topology)
                        store.backup(target["id"])
                        memory_topology.backfill(target, current, topology)
                        store.save(target)
                    return self._json({"memory": current["hardware"]["memory"], "run_id": body.get("run_id")})
            if route == "/api/hardware/bandwidth":
                with WORK_LOCK:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before measuring transfers.")
                    from . import bandwidth
                    body = self._body()
                    folder = body.get("storage_probe_directory")
                    if folder not in (None, "") and (not isinstance(folder, str) or not os.path.isdir(folder)):
                        raise ValueError("Storage test folder must be an existing local directory.")
                    folders = body.get("storage_probe_directories")
                    endpoints.validate(dict(provider="lmstudio", storage_probe_directories=folders))
                    gpu_index = body.get("gpu_index", 0)
                    if type(gpu_index) is not int or not 0 <= gpu_index <= 255:
                        raise ValueError("GPU sensor index must be an integer in 0..255.")
                    target = store.load(body["run_id"]) if body.get("run_id") else None
                    if body.get("run_id") and target is None:
                        return self._error(404, "no such run")
                    current = {"config": {}, "corpus": {}, "gpu": power.gpu_info(gpu_index), "host": store.host_info()}
                    if target:
                        store.prepare_run(current)
                        # Validate before doing resource-consuming work or making a backup.
                        bandwidth.backfill(copy.deepcopy(target), current,
                                           {"captured_at": "validation"})
                    profile = bandwidth.measure(gpu_index, directory=folder or None,
                                                **({"directories": folders} if folders else {}))
                    if target:
                        store.backup(target["id"])
                        bandwidth.backfill(target, current, profile)
                        store.save(target)
                    return self._json({"transfer_bandwidth": profile, "run_id": body.get("run_id")})
            if route == "/api/capabilities/start":
                with WORK_LOCK:
                    conflict = self._work_conflict()
                    if conflict: return self._error(409, conflict)
                    return self._json({"run_id": _cap_bench().start(self._body())})
            if route == "/api/capabilities/cancel":
                body = self._body()
                if "expected_run_id" in body:
                    if not _cap_bench().cancel_if_current(body["expected_run_id"]):
                        return self._error(409, "The requested capability run is no longer current.")
                else:
                    _cap_bench().cancel()
                return self._json({"ok": True})
            if route == "/api/capabilities/import":
                with WORK_LOCK:
                    if _any_running(): return self._error(409, "Wait for the benchmark to finish before importing.")
                    return self._json({"imported": _cap_bench().import_run(self._body())})
            if route == "/api/bench/start":
                with WORK_LOCK:
                    conflict = self._work_conflict()
                    if conflict: return self._error(409, conflict)
                    run_id = BENCH.start(self._body())
                return self._json({"run_id": run_id})
            if route == "/api/endpoint/check":
                return self._json(self.endpoint_payload(self._body()))
            if route == "/api/bench/cancel":
                body = self._body()
                if "expected_run_id" in body:
                    if not BENCH.cancel_if_current(body["expected_run_id"]):
                        return self._error(409, "The requested benchmark run is no longer current.")
                else:
                    BENCH.cancel()
                return self._json({"ok": True})
            if route == "/api/bench/resume":
                with WORK_LOCK:
                    conflict = self._work_conflict()
                    if conflict: return self._error(409, conflict)
                    run_id = BENCH.resume(self._body().get("run_id"))
                return self._json({"run_id": run_id})
            if route == "/api/bench/extend-plan":
                return self._json(BENCH.plan_extension(self._body()))
            if route == "/api/bench/extend-options":
                body = self._body()
                return self._json(BENCH.extension_options(body.get("run_id"), body))
            if route == "/api/bench/extend":
                with WORK_LOCK:
                    conflict = self._work_conflict()
                    if conflict: return self._error(409, conflict)
                    return self._json({"run_id": BENCH.extend(self._body())})
            if route == "/api/lmstudio/start":
                conflict = self._work_conflict()
                if conflict: return self._error(409, conflict)
                return self._json({"ok": lmstudio.LmsCli().start_server()})
            if route == "/api/lmstudio/unload":
                conflict = self._work_conflict()
                if conflict: return self._error(409, conflict)
                lmstudio.LmsCli().unload_all()
                return self._json({"ok": True})
            if route.endswith("/models/delete") and route.startswith("/api/runs/"):
                body = self._body()
                run_id = urllib.parse.unquote(route[len("/api/runs/"):-len("/models/delete")])
                with WORK_LOCK, BENCH._start_lock:
                    if _any_running():
                        return self._error(409, "Wait for the benchmark to finish before removing a model.")
                    from .model_actions import remove_models
                    result = remove_models(run_id, body.get("models"))
                    if (BENCH.run or {}).get("id") == run_id:
                        BENCH.run = store.load(run_id)
                    return self._json(result)
            if route.endswith("/meta") and route.startswith("/api/runs/"):
                run_id = urllib.parse.unquote(route[len("/api/runs/"):-len("/meta")])
                body = self._body()
                with BENCH._start_lock:
                    if BENCH.running and (BENCH.run or {}).get("id") == run_id:
                        return self._error(409, "Wait for this benchmark to stop before editing it.")
                    run = store.set_meta(run_id, body.get("label"), body.get("notes"))
                return self._json(store.summarise(run)) if run else self._error(404, "no such run")
            if route == "/api/shutdown":
                self._json({"ok": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return None
            return self._error(404, "not found")
        except (ValueError, RuntimeError) as exc:
            return self._error(400, str(exc))
        except Exception as exc:                     # noqa: BLE001
            return self._error(500, str(exc))

    def do_DELETE(self):
        route = urllib.parse.urlparse(self.path).path
        denied = self._mutation_guard()
        if denied:
            self.close_connection = True
            return self._error(403, denied)
        if route.startswith("/api/runs/"):
            run_id = urllib.parse.unquote(route[len("/api/runs/"):])
            try:
                with BENCH._start_lock:
                    if BENCH.running and (BENCH.run or {}).get("id") == run_id:
                        return self._error(409, "Stop the active benchmark before deleting it.")
                    deleted = store.delete(run_id)
                    if deleted and (BENCH.run or {}).get("id") == run_id:
                        BENCH.run = None
                return self._json({"deleted": deleted}) if deleted else self._error(404, "no such run")
            except ValueError as exc:
                return self._error(400, str(exc))
            except OSError as exc:
                return self._error(500, str(exc))
        return self._error(404, "not found")

    # -- payloads ----------------------------------------------------------
    def _selected_runs(self, query):
        ids = [i for i in (query.get("ids") or [""])[0].split(",") if i]
        if not ids:
            return store.list_runs(full=True)
        return [r for r in (store.load(i) for i in ids) if r]

    def models_payload(self):
        try:
            return lmstudio.list_models()
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def endpoint_payload(self, cfg):
        cfg = dict(cfg, provider="openai")
        endpoints.validate(cfg)
        try:
            models = endpoints.list_models(cfg)
            endpoints.resolve_identity(cfg)
            return {"models": models, "ok": True, "provider": endpoints.public_provider(cfg),
                    "endpoint_identity": dict(cfg["endpoint_identity"], id=cfg["endpoint_id"], label=cfg["endpoint_label"]),
                    "manual_model_ids": True,
                    "note": "Models discovered. Effective inference settings remain unverified."}
        except ValueError as exc:
            endpoints.resolve_identity(cfg)
            return {"models": [], "ok": False, "error": str(exc), "manual_model_ids": True,
                    "endpoint_identity": dict(cfg["endpoint_identity"], id=cfg["endpoint_id"], label=cfg["endpoint_label"]),
                    "provider": endpoints.public_provider(cfg)}

    def env_payload(self):
        from . import bandwidth, memory_topology, gpu_setup
        models = self.models_payload()
        selected, missing = default_selection(models)
        cli = lmstudio.LmsCli()
        server_up = cli.server_running()
        gpu = power.gpu_info(0)
        sensors = power.list_gpus()
        machine = hardware.inventory()
        profile = bandwidth.latest(machine, sensors=sensors)
        if profile is None:
            profile = bandwidth.saved_capture(machine, gpu, sensors=sensors)
        return {
            "gpus": sensors,
            "gpu_setup": gpu_setup.status(),
            "gpu": gpu,
            "platform": platform_util.platform_label(),
            "power": {
                "source": gpu.get("power_source") or power.SRC_NONE,
                "readable": bool(gpu.get("power_readable")),
                "note": gpu.get("power_note"),
                # macOS only: watts need root, and the launcher is what arranges it
                "needs_sudo": platform_util.IS_MAC and not gpu.get("power_readable"),
            },
            "host": store.host_info(),
            "hardware": dict(machine, transfer_bandwidth=profile),
            "hardware_follow_ups": memory_topology.pending(store.list_runs(full=True), sensors),
            "lms": {"cli": cli.available, "exe": cli.exe,
                    "server": server_up, "version": cli.version()},
            "models": models,
            "default_models": selected,
            "missing_defaults": missing,
            "corpus": corpus.master_stats(),
            "defaults": runner.DEFAULT_CONFIG,
            "capabilities": {"resume": True, "extend": True, "context_planning": True, "context_planner_version": 1,
                             "multi_gpu_telemetry": True,
                             "providers": ["lmstudio", "openai"], "external_model_management": False,
                             "capability_benchmarks": True, "transfer_bandwidth": True,
                             "memory_topology": True},
            "size_presets": [512, 5000, 10000, 20000, 40000, 80000,
                             100000, 120000, 150000, 200000, 260000],
            "concurrency_presets": list(range(1, 11)) + [15, 20, 30, 40, 50, 75, 100],
            "last_config": store.load_config(),
            "running": BENCH.running,
        }


def find_port(start=DEFAULT_PORT, tries=20):
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No free port found near %d" % start)


def reuse_existing(port):
    """Keep the desktop launcher on one instance and replace idle old versions."""
    url = "http://127.0.0.1:%d/" % port
    try:
        with urllib.request.urlopen(url + "api/status", timeout=3) as response:
            identity = response.headers.get("Server", "")
            if not identity.startswith(("Wattever/", "GpuBench/")):
                return False
            status = json.load(response)
        try:
            with urllib.request.urlopen(url + "api/version", timeout=3) as response:
                information = json.load(response)
                version = information.get("version")
                remote_build = information.get("build_id")
        except (urllib.error.URLError, OSError, ValueError):
            version = remote_build = None
        same_build = version == __version__ and remote_build == BUILD_ID
        if same_build or status.get("running") or status.get("capability_running"):
            print("Wattever is already running at %s" % url)
            if not same_build:
                print("An older version is benchmarking. Relaunch after it finishes to update.")
            if os.environ.get("GPU_BENCH_NO_BROWSER") != "1":
                webbrowser.open(url)
            return True
        # Only an identified Wattever instance with no active benchmark is replaced.
        request = urllib.request.Request(url + "api/shutdown", data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=5):
            pass
        for _ in range(30):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                if sock.connect_ex(("127.0.0.1", port)) != 0:
                    return False
            time.sleep(0.1)
        raise RuntimeError("The old Wattever instance is still stopping. Relaunch in a moment.")
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main():
    # the launcher window is the only place errors surface, so do not buffer it
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    requested_port = int(os.environ.get("GPU_BENCH_PORT", DEFAULT_PORT))
    if reuse_existing(requested_port):
        return
    port = find_port(requested_port)
    url = "http://127.0.0.1:%d/" % port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    print("=" * 62)
    print("  LLM GPU Benchmark  --  %s" % url)
    print("=" * 62)
    gpu = power.gpu_info(0)
    if gpu:
        unified = gpu.get("memory_architecture") == "unified"
        print("  GPU      : %s (%s MB %s, %s)"
              % (gpu.get("name"), int(gpu.get("vram_total_mb") or 0),
                 "unified memory" if unified else "VRAM",
                 gpu.get("driver") or "no driver string"))
        if gpu.get("power_readable"):
            print("  Power    : measured via %s" % gpu.get("power_source"))
        elif platform_util.IS_MAC:
            print("  Power    : NOT measured (%s)" % (gpu.get("power_note") or "needs sudo"))
            print("             Relaunch with `Launch GPU Benchmark.command` and answer")
            print("             yes to the power prompt to record watts.")
        else:
            print("  Power    : NOT measured (this GPU reports no power draw)")
    else:
        print("  GPU      : no supported accelerator detected (power unavailable)")
    print("  LM Studio: %s" % ("API up" if BENCH.cli.server_running() else
                               "not running (the app will start it)"))
    sensors = power.list_gpus()
    if len(sensors) > 1:
        print("  Telemetry: all %d NVIDIA GPUs, per card and combined" % len(sensors))
    print("\n  Leave this window open. Press Ctrl+C to stop the server.\n")
    if os.environ.get("GPU_BENCH_NO_BROWSER") != "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        BENCH.cancel()
        if CAP_BENCH: CAP_BENCH.cancel()
        httpd.server_close()


if __name__ == "__main__":
    main()
