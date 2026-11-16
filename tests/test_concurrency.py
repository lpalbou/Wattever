import threading
import json
import csv
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import unittest
from unittest.mock import patch

from wattever import runner, store


class Sampler:
    def window(self, start, end):
        return {"gpu_energy_j": end - start}


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.runner = runner.BenchRunner()
        self.runner.sampler = Sampler()
        self.cfg = dict(runner.DEFAULT_CONFIG, per_test_timeout_s=1)

    def test_csv_exports_request_offsets_without_response_text(self):
        requests = [{"status": "ok", "start_offset_s": 0.0, "ttft_s": 2, "sample_text": "private"},
                    {"status": "failed", "start_offset_s": 1.5}]
        row = next(csv.DictReader(io.StringIO(store.to_csv([{"results": [{"requests": requests}]}]))))
        self.assertEqual(json.loads(row["request_metrics_json"]), [
            {"status": "ok", "start_offset_s": 0.0, "ttft_s": 2},
            {"status": "failed", "start_offset_s": 1.5}])

    def test_csv_derives_group_rates_and_preserves_recorded_values(self):
        row = {"status": "failed", "concurrency": 3, "wall_s": 10,
               "prefill_tps": 999, "decode_tps": 888, "aggregate_completion_tokens": 777,
               "metric_sources": {"decode_tps": "legacy"}, "requests": [
                   {"status": "ok", "start_offset_s": 0, "ttft_s": 3, "prompt_tokens": 100, "completion_tokens": 10},
                   {"status": "ok", "start_offset_s": 5, "ttft_s": 2, "prompt_tokens": 100, "completion_tokens": 20},
                   {"status": "failed"}]}
        original = json.dumps(row, sort_keys=True)
        def export():
            return next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        result = export()
        self.assertAlmostEqual(float(result["prefill_tps"]), 200 / 5)
        self.assertEqual(result["decode_tps"], "")
        self.assertEqual(float(result["group_output_tps"]), 3)
        self.assertEqual(result["aggregate_completion_tokens"], "30")
        self.assertEqual(result["recorded_aggregate_completion_tokens"], "777")
        self.assertEqual(result["recorded_prefill_tps"], "999")
        self.assertEqual(json.loads(result["metric_sources_json"])["decode_tps"], "legacy")
        self.assertEqual(json.dumps(row, sort_keys=True), original)
        del row["requests"][1]["start_offset_s"]
        self.assertEqual(export()["prefill_tps"], "")
        del row["requests"][1]["completion_tokens"]
        self.assertEqual(export()["decode_tps"], "")
        self.assertEqual(export()["aggregate_completion_tokens"], "")

    def test_csv_decode_uses_observed_phase_and_preserves_recorded_rate(self):
        row = {"status": "ok", "concurrency": 2, "wall_s": 20, "decode_tps": 999,
               "metric_sources": {"decode_tps": "historical"}, "requests": [
                   {"status": "ok", "start_offset_s": 0, "ttft_s": 2, "decode_s": 4,
                    "decode_token_count": 8, "completion_tokens": 9},
                   {"status": "ok", "start_offset_s": 8, "ttft_s": 2, "decode_s": 6,
                    "decode_token_count": 12, "completion_tokens": 13}]}
        before = json.dumps(row, sort_keys=True)
        result = next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        self.assertEqual(float(result["decode_tps"]), 2)
        self.assertEqual(float(result["group_output_tps"]), 22 / 20)
        self.assertEqual(result["recorded_decode_tps"], "999")
        self.assertEqual(json.loads(result["metric_sources_json"])["decode_tps"], "historical")
        self.assertEqual(json.loads(result["request_metrics_json"])[0]["decode_s"], 4)
        self.assertEqual(json.dumps(row, sort_keys=True), before)

    def test_requests_overlap_and_aggregate_is_group_throughput(self):
        barrier = threading.Barrier(3)
        def measure(*args, **kwargs):
            barrier.wait(timeout=0.5)
            time.sleep(0.03)
            return {"completion_tokens": 10, "decode_tps": 7, "prefill_tps": 100, "ttft_s": 0.1, "prompt_tokens": 100}
        with patch.object(runner.lmstudio, "measure_completion", side_effect=measure):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 3, None)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["completed_requests"], 3)
        self.assertEqual(result["aggregate_completion_tokens"], 30)
        self.assertNotIn("aggregate_completion_tps", result)
        self.assertAlmostEqual(result["group_output_tps"], 30 / result["wall_s"])
        self.assertNotIn("decode_tps", result)
        from wattever.collective_metrics import collective_prefill_phase
        self.assertAlmostEqual(result["prefill_tps"], collective_prefill_phase(result["requests"])["tps"])
        self.assertEqual(result["requests"][0]["decode_tps"], 7)
        self.assertNotIn("power", result)

    def test_new_group_records_decode_phase(self):
        def measure(*args, **kwargs):
            return {"completion_tokens": 11, "decode_token_count": 10,
                    "decode_s": 2, "ttft_s": 1, "prompt_tokens": 100}
        with patch.object(self.runner, "_measure_completion", side_effect=measure):
            row = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc", 2, None)
        from wattever.collective_metrics import collective_decode_phase
        phase = collective_decode_phase(row["requests"])
        self.assertEqual(row["decode_tps"], phase["tps"])
        self.assertEqual(phase["decode_tokens"], 20)
        self.assertAlmostEqual(row["group_output_tps"], 22 / row["wall_s"])
        self.assertIn("union_of_observed_decode_intervals", row["metric_sources"]["decode_tps"])

    def test_partial_group_uses_successful_counts_and_whole_wall(self):
        counter = iter([True, False])
        def measure(*args, **kwargs):
            if next(counter):
                return {"completion_tokens": 10, "prompt_tokens": 100, "ttft_s": 0.01}
            time.sleep(0.03)
            raise RuntimeError("failed")
        with patch.object(self.runner, "_measure_completion", side_effect=measure):
            row = self.runner._concurrent_test({"nonce":"abc"}, self.cfg, "m", "Benchmark request abc", 2, None)
        self.assertEqual(row["status"], "failed")
        self.assertGreaterEqual(row["wall_s"], 0.025)
        self.assertAlmostEqual(row["group_output_tps"], 10 / row["wall_s"])
        self.assertNotIn("decode_tps", row)
        successes = [request for request in row["requests"] if request["status"] == "ok"]
        self.assertEqual(len(successes), 1)
        self.assertAlmostEqual(row["prefill_tps"], 100 / .01)

    def test_failure_and_timeout_are_recorded(self):
        def measure(*args, **kwargs):
            raise RuntimeError("server failed")
        with patch.object(runner.lmstudio, "measure_completion", side_effect=measure):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fail_kind"], runner.FAIL_ERROR)
        self.cfg["per_test_timeout_s"] = 0.03
        def blocked(*args, **kwargs):
            while not kwargs["cancel"].is_set():
                time.sleep(0.001)
            raise runner.lmstudio.Cancelled()
        def recover(model, threads, responses):
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        with patch.object(runner.lmstudio, "measure_completion", side_effect=blocked), \
             patch.object(self.runner, "_recover_requests", side_effect=recover) as recovery:
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
        self.assertEqual(result["timeout_trigger"], "configured deadline")
        self.assertEqual(result["timeout_elapsed_s"], result["wall_s"])
        self.assertNotIn("recovery_error", result)
        recovery.assert_called_once()

    def test_cancel_is_recorded(self):
        self.runner.cancel_event.set()
        with patch.object(runner.lmstudio, "measure_completion", side_effect=runner.lmstudio.Cancelled):
            result = self.runner._concurrent_test({"nonce": "abc"}, self.cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["message"], "Cancelled by the user.")

    def test_external_timeout_preserves_unverified_cancellation(self):
        cfg = dict(self.cfg, provider="openai", per_test_timeout_s=0.03)
        def blocked(*args, **kwargs):
            while not kwargs["cancel"].is_set():
                time.sleep(0.001)
            raise runner.lmstudio.Cancelled()
        def recover(model, threads, responses):
            for thread in threads:
                thread.join(timeout=5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            raise runner.RecoveryError("External server cancellation is unverified")
        with patch.object(self.runner, "_measure_completion", side_effect=blocked), \
             patch.object(self.runner, "_recover_requests", side_effect=recover):
            result = self.runner._concurrent_test({"nonce":"abc"}, cfg, "m", "Benchmark request abc\n", 2, None)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["fail_kind"], runner.FAIL_TIMEOUT)
        self.assertIn("server-side cancellation could not be verified", result["message"])
        self.assertNotIn("managed model unload", result["message"])
        self.assertIn("unverified", result["recovery_error"])


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
            self.assertIsNone(fallback["completion_tokens"])
            self.assertIsNone(fallback["decode_token_count"])
            self.assertFalse(fallback["completion_tokens_estimated"])
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
