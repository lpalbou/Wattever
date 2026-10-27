"""Isolated static-site/provider fixture for remote browser CI; never calls LM Studio."""
import functools
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
SECRET = "static-fixture-secret"
stats = {"static_api": [], "models": 0, "completions": 0, "authorized": 0,
         "origins": [], "prompts": []}
lock = threading.Lock()


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def headers_for(self, code, content_type="application/json"):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", content_type)
        self.end_headers()

    def do_OPTIONS(self):
        self.headers_for(204)

    def authorized(self):
        valid = self.headers.get("Authorization") == "Bearer " + SECRET
        if not valid:
            self.headers_for(401)
            self.wfile.write(b'{"error":"fixture authorization required"}')
            return False
        with lock:
            stats["authorized"] += 1
            stats["origins"].append(self.headers.get("Origin"))
        return True

    def do_GET(self):
        if self.path != "/v1/models":
            self.headers_for(404)
            return
        if not self.authorized():
            return
        with lock:
            stats["models"] += 1
        self.headers_for(200)
        self.wfile.write(b'{"data":[{"id":"fixture-model","object":"model"}]}')

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.headers_for(404)
            return
        if not self.authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        with lock:
            stats["completions"] += 1
            stats["prompts"].append(body["messages"][0]["content"])
        self.headers_for(200, "text/event-stream")
        for event in (
            {"choices": [{"delta": {"content": "One"}}]},
            {"choices": [{"delta": {"content": " two"}}]},
            {"choices": [{"delta": {"content": " three"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        ):
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.025)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class Static(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/__fixture/stats":
            with lock:
                value = json.dumps(stats).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(value)))
            self.end_headers()
            self.wfile.write(value)
            return
        if route.startswith("/api/") or "/api/" in route:
            with lock:
                stats["static_api"].append(route)
            self.send_error(404, "This fixture has no Python helper API")
            return
        super().do_GET()

    def do_POST(self):
        with lock:
            stats["static_api"].append(urlsplit(self.path).path)
        self.send_error(404, "This fixture has no Python helper API")


def main():
    with tempfile.TemporaryDirectory(prefix="wattever-static-") as directory:
        site = Path(directory) / "Wattever"
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_pages.py"),
                        "--output-dir", str(site)], cwd=ROOT, check=True,
                       stdout=subprocess.DEVNULL)
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        static = ThreadingHTTPServer(("127.0.0.1", 0),
                                     functools.partial(Static, directory=directory))
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        print(json.dumps({"site": "http://127.0.0.1:%d/Wattever/" % static.server_port,
                          "provider": "http://127.0.0.1:%d/v1" % provider.server_port}), flush=True)
        try:
            static.serve_forever()
        finally:
            static.server_close()
            provider.shutdown()
            provider.server_close()


if __name__ == "__main__":
    main()
