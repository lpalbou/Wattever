"""Local HTTP server: JSON API + the single-page UI.

Standard library only, so the launcher works on any Python 3.9+ install with no
pip step. Binds to localhost only.
"""
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from wattever import corpus, lmstudio, platform_util, power, runner, store
else:
    from . import corpus, lmstudio, platform_util, power, runner, store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
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
    server_version = "GpuBench/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers -----------------------------------------------------------
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, code=200):
        self._send(code, json.dumps(payload, allow_nan=False, default=str))

    def _error(self, code, message):
        self._json({"error": message}, code)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, OSError):
            return {}

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
            if route == "/api/models":
                return self._json({"models": self.models_payload()})
            if route == "/api/status":
                return self._json(BENCH.status())
            if route == "/api/events":
                cursor = int((query.get("cursor") or ["0"])[0])
                events, total = BENCH.events_since(cursor)
                return self._json({"events": events, "cursor": total,
                                   "status": BENCH.status()})
            if route == "/api/runs":
                return self._json({"runs": [_mark_interrupted(r) for r in store.list_runs()]})
            if route.startswith("/api/runs/"):
                run = store.load(route[len("/api/runs/"):])
                return self._json(_mark_interrupted(run)) if run else self._error(404, "no such run")
            if route == "/api/export.csv":
                runs = self._selected_runs(query)
                return self._send(200, store.to_csv(runs), "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=gpu-bench.csv"})
            if route == "/api/export.json":
                runs = self._selected_runs(query)
                return self._send(200, json.dumps({"runs": runs}, indent=1, default=str),
                                  "application/json; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=gpu-bench.json"})
            if route == "/api/preview":
                target = int((query.get("tokens") or ["512"])[0])
                prompt, meta = corpus.build_prompt(target)
                return self._json({"meta": meta, "head": prompt[:1800],
                                   "tail": prompt[-700:]})
            return self._error(404, "not found")
        except Exception as exc:                     # noqa: BLE001
            return self._error(500, str(exc))

    def do_POST(self):
        route = urllib.parse.urlparse(self.path).path
        try:
            if route == "/api/bench/start":
                run_id = BENCH.start(self._body())
                return self._json({"run_id": run_id})
            if route == "/api/bench/cancel":
                BENCH.cancel()
                return self._json({"ok": True})
            if route == "/api/lmstudio/start":
                return self._json({"ok": BENCH.cli.start_server()})
            if route == "/api/lmstudio/unload":
                BENCH.cli.unload_all()
                return self._json({"ok": True})
            if route.endswith("/meta") and route.startswith("/api/runs/"):
                run_id = route[len("/api/runs/"):-len("/meta")]
                body = self._body()
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
        if route.startswith("/api/runs/"):
            return self._json({"deleted": store.delete(route[len("/api/runs/"):])})
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

    def env_payload(self):
        models = self.models_payload()
        selected, missing = default_selection(models)
        server_up = BENCH.cli.server_running()
        gpu = power.gpu_info(0)
        return {
            "gpus": power.list_gpus(),
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
            "lms": {"cli": BENCH.cli.available, "exe": BENCH.cli.exe,
                    "server": server_up, "version": BENCH.cli.version()},
            "models": models,
            "default_models": selected,
            "missing_defaults": missing,
            "corpus": corpus.master_stats(),
            "defaults": runner.DEFAULT_CONFIG,
            "size_presets": [512, 5000, 10000, 20000, 40000, 80000],
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


def main():
    # the launcher window is the only place errors surface, so do not buffer it
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    port = find_port(int(os.environ.get("GPU_BENCH_PORT", DEFAULT_PORT)))
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
    print("\n  Leave this window open. Press Ctrl+C to stop the server.\n")
    if os.environ.get("GPU_BENCH_NO_BROWSER") != "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        BENCH.cancel()
        httpd.server_close()


if __name__ == "__main__":
    main()
