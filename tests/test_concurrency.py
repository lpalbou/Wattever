import threading
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import unittest
from unittest.mock import patch

from wattever import runner


class Sampler:
    def window(self, start, end):
        return {"gpu_energy_j": end - start}


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.runner = runner.BenchRunner()
        self.runner.sampler = Sampler()
        self.cfg = dict(runner.DEFAULT_CONFIG, per_test_timeout_s=1)

    def test_requests_overlap_and_aggregate_is_group_throughput(self):
        barrier = threading.Barrier(3)
        def measure(*args, **kwargs):
            barrier.wait(timeout=0.5)
            time.sleep(0.03)
            return {"completion_tokens": 10, "decode_tps": 7, "ttft_s": 0.01}
        with patch.object(runner.lmstudio, "measure_completion", side_effect=measure):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 3, None)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["completed_requests"], 3)
        self.assertEqual(result["aggregate_completion_tokens"], 30)
        self.assertAlmostEqual(result["aggregate_completion_tps"], 30 / result["wall_s"])
        self.assertNotIn("decode_tps", result)
        self.assertEqual(result["requests"][0]["decode_tps"], 7)
        self.assertNotIn("power", result)

    def test_failure_and_timeout_are_recorded(self):
        def measure(*args, **kwargs):
            raise RuntimeError("server failed")
        with patch.object(runner.lmstudio, "measure_completion", side_effect=measure):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fail_kind"], runner.FAIL_ERROR)
        self.cfg["per_test_timeout_s"] = 0.03
        with patch.object(runner.lmstudio, "measure_completion", side_effect=lambda *a, **k: time.sleep(0.2)):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
        self.assertLess(result["wall_s"], 0.15)

    def test_cancel_is_recorded(self):
        self.runner.cancel_event.set()
        with patch.object(runner.lmstudio, "measure_completion", side_effect=runner.lmstudio.Cancelled):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["status"], "cancelled")


class StreamingIntegrationTests(unittest.TestCase):
    def test_http_streams_overlap_and_validate_completion(self):
        barrier = threading.Barrier(2)
        prompts = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                prompt = body["messages"][0]["content"]
                prompts.append(prompt)
                self.send_response(200)
                self.end_headers()
                if prompt.startswith("parallel"):
                    barrier.wait(timeout=2)
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
                if prompt != "truncated":
                    if prompt != "missing usage":
                        self.wfile.write(b'data: {"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n')
                    self.wfile.write(b'data: [DONE]\n\n')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:%d" % server.server_port
        try:
            results = []
            workers = [threading.Thread(target=lambda: results.append(
                runner.lmstudio.measure_completion("m", "parallel", 5, base=base, timeout=2)))
                for _ in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(3)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(r["completion_tokens"] == 2 and not r["completion_tokens_estimated"] for r in results))
            fallback = runner.lmstudio.measure_completion("m", "missing usage", 5, base=base)
            self.assertTrue(fallback["completion_tokens_estimated"])
            with self.assertRaisesRegex(RuntimeError, r"before \[DONE\]"):
                runner.lmstudio.measure_completion("m", "truncated", 5, base=base)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_group_uses_distinct_prefixes(self):
        r = runner.BenchRunner()
        r.sampler = Sampler()
        prompts = []
        def measure(model, prompt, *args, **kwargs):
            prompts.append(prompt)
            return {"completion_tokens": 1}
        with patch.object(runner.lmstudio, "measure_completion", side_effect=measure):
            result = r._concurrent_test({"nonce": "abc"}, runner.DEFAULT_CONFIG,
                                        "m", "Benchmark request abc\nbody", 3, None)
        self.assertEqual(len(set(prompts)), 3)
        self.assertEqual(len({x["nonce"] for x in result["requests"]}), 3)


if __name__ == "__main__":
    unittest.main()
