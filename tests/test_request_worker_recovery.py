"""CPU-only timeout recovery tests; no model or user-owned engine lifecycle."""
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from wattever import endpoints, lmstudio, runner, server


class Sampler:
    def now(self):
        return {}

    def window(self, start, end):
        return {"available": False}


class ResidualRequestWorkerTests(unittest.TestCase):
    def setUp(self):
        self.bench = runner.BenchRunner()
        self.bench.sampler = Sampler()
        self.cfg = dict(runner.DEFAULT_CONFIG, provider="openai", per_test_timeout_s=.05)

    def finish_workers(self, release):
        workers = self.bench._live_request_workers()
        release.set()
        for worker in workers:
            worker.join(timeout=2)
        self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_single_external_timeout_remains_busy_until_request_worker_exits(self):
        release = threading.Event()
        self.bench.cli = endpoints.ExternalControl()
        def blocked(*args, **kwargs):
            release.wait(timeout=5)
            raise lmstudio.Cancelled()
        with patch.object(self.bench, "_measure_completion", side_effect=blocked), \
             patch.object(self.bench.cli, "unload_confirmed") as unload, \
             patch.object(runner.corpus, "build_prompt", return_value=("Benchmark request fixture\n", {"nonce":"fixture", "corpus_wraps":0})):
            try:
                result = self.bench._one_test({}, self.cfg, "fixture-model", 512, 0, 1,
                    {}, None, 1536, None, 4.6)
                self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
                self.assertIn("cancellation", result["recovery_error"])
                self.assertTrue(self.bench.running)
                status = self.bench.status()
                self.assertTrue(status["running"])
                self.assertEqual(status["live"]["pending_request_workers"], 1)
                self.assertIn("cancellation unverified", status["live"]["phase"])
                with patch.object(runner.endpoints, "resolve_identity") as resolve:
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        self.bench.start({})
                    resolve.assert_not_called()
                unload.assert_not_called()
            finally:
                self.finish_workers(release)
        self.assertFalse(self.bench.running)
        self.assertFalse(self.bench.status()["running"])
        self.assertNotIn("pending_request_workers", self.bench.status()["live"])

    def test_concurrent_external_recovery_retains_both_residual_workers(self):
        release = threading.Event()
        self.bench.cli = endpoints.ExternalControl()
        def blocked(*args, **kwargs):
            release.wait(timeout=5)
            raise lmstudio.Cancelled()
        with patch.object(self.bench, "_measure_completion", side_effect=blocked), \
             patch.object(self.bench.cli, "unload_confirmed") as unload:
            try:
                result = self.bench._concurrent_test({"nonce":"fixture"}, self.cfg,
                    "fixture-model", "Benchmark request fixture\n", 2, None)
                self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
                self.assertIn("cancellation", result["recovery_error"])
                self.assertEqual(len(self.bench._live_request_workers()), 2)
                self.assertTrue(self.bench.running)
                unload.assert_not_called()
            finally:
                self.finish_workers(release)
        self.assertFalse(self.bench.running)

    def test_managed_recovery_still_requires_unload_and_worker_exit(self):
        release = threading.Event()
        def blocked(*args, **kwargs):
            release.wait(timeout=5)
            raise lmstudio.Cancelled()
        def unload(model):
            release.set()
            return True
        self.bench.cli = SimpleNamespace(unload_confirmed=Mock(side_effect=unload))
        managed = dict(self.cfg, provider="lmstudio")
        with patch.object(self.bench, "_measure_completion", side_effect=blocked):
            try:
                result = self.bench._concurrent_test({"nonce":"fixture"}, managed,
                    "fixture-model", "Benchmark request fixture\n", 2, None)
                self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
                self.assertNotIn("recovery_error", result)
                self.bench.cli.unload_confirmed.assert_called_once_with("fixture-model")
                self.assertFalse(self.bench.running)
            finally:
                self.finish_workers(release)

    def test_pending_speed_request_blocks_capability_start_at_http_boundary(self):
        release = threading.Event()
        worker = threading.Thread(target=lambda:release.wait(timeout=5), daemon=True)
        self.bench._start_request_worker(worker)
        capability = SimpleNamespace(running=False, start=Mock(side_effect=AssertionError("must not start")))
        httpd = None
        with patch.object(server, "BENCH", self.bench), \
             patch.object(server, "CAP_BENCH", capability), \
             patch.object(server, "_other_helper_running", return_value=False):
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                threading.Thread(target=httpd.serve_forever, daemon=True).start()
                request = urllib.request.Request("http://127.0.0.1:%d/api/capabilities/start" % httpd.server_port,
                    data=json.dumps({}).encode(), headers={"Content-Type":"application/json"})
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(request, timeout=2)
                try:
                    self.assertEqual(failure.exception.code, 409)
                finally:
                    failure.exception.close()
                capability.start.assert_not_called()
                self.assertTrue(server._any_running())
            finally:
                self.finish_workers(release)
                if httpd:
                    httpd.shutdown()
                    httpd.server_close()
            self.assertFalse(server._any_running())


if __name__ == "__main__":
    unittest.main()
