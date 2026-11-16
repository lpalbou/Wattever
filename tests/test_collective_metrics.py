import unittest

from wattever.collective_metrics import collective_decode_phase, collective_prefill_phase, collective_row_metrics, enrich_result_row


class CollectiveMetricsTests(unittest.TestCase):
    def test_group_uses_wall_time_not_sum(self):
        row = {
            "status": "ok",
            "concurrency": 2,
            "wall_s": 40.0,
            "aggregate_completion_tokens": 512,
            "requests": [
                {"status": "ok", "prefill_tps": 598.0, "decode_tps": 6.1, "start_offset_s": 0.0, "ttft_s": 3.0, "prompt_tokens": 5000, "completion_tokens": 256},
                {"status": "ok", "prefill_tps": 602.0, "decode_tps": 6.5, "start_offset_s": 2.0, "ttft_s": 8.0, "prompt_tokens": 5000, "completion_tokens": 256},
            ],
        }
        metrics = collective_row_metrics(row)
        self.assertAlmostEqual(metrics["group_output_tps"], 512 / 40.0)
        self.assertNotIn("decode_tps", metrics)
        self.assertAlmostEqual(metrics["prefill_tps"], 10000 / 10.0)

    def test_missing_timing_and_partial_counts_are_not_invented(self):
        row = {"status":"failed", "concurrency":3, "wall_s":10, "aggregate_completion_tokens":999,
               "requests":[{"status":"ok", "prompt_tokens":100, "completion_tokens":20, "start_offset_s":2, "ttft_s":3},
                           {"status":"ok", "prompt_tokens":100, "completion_tokens":30, "start_offset_s":4, "ttft_s":None},
                           {"status":"failed"}]}
        self.assertEqual(collective_row_metrics(row), {})
        metrics = collective_row_metrics(row, partial=True)
        self.assertEqual(metrics["group_output_tps"], 5)
        self.assertNotIn("decode_tps", metrics)
        self.assertNotIn("prefill_tps", metrics)
        row["requests"][1]["ttft_s"] = 2
        self.assertEqual(collective_row_metrics(row, partial=True)["prefill_tps"], 200 / 4)
        del row["requests"][1]["start_offset_s"]
        row["prefill_tps"] = 77
        row["aggregate_completion_tps"] = 99
        enrich_result_row(row, partial=True)
        self.assertEqual(row["prefill_tps"], 77)
        self.assertEqual(row["aggregate_completion_tps"], 99)

    def test_provider_timings_do_not_replace_observed_window(self):
        row = {"status":"ok", "concurrency":2, "wall_s":10,
               "requests":[{"status":"ok", "prompt_tokens":100, "completion_tokens":10,
                            "start_offset_s":0, "ttft_s":2, "reported_timings":{"prompt_ms":1}},
                           {"status":"ok", "prompt_tokens":100, "completion_tokens":10,
                            "start_offset_s":4, "ttft_s":2, "reported_timings":{"prompt_ms":1}}]}
        metrics = collective_row_metrics(row)
        self.assertEqual(metrics["prefill_tps"], 200 / 4)
        self.assertEqual(metrics["group_output_tps"], 2)
        self.assertNotIn("decode_tps", metrics)
        row["requests"].append({"status":"failed"})
        self.assertEqual(collective_row_metrics(row), {})
        self.assertEqual(collective_row_metrics(row, partial=True), {})

    def test_decode_union_and_scoped_tokens(self):
        def request(start, ttft, duration, count):
            return {"status": "ok", "start_offset_s": start, "ttft_s": ttft,
                    "decode_s": duration, "decode_token_count": count, "completion_tokens": count + 1}
        requests = [request(0, 26.8061993, 31.9114442 - 26.8061993, 206),
                    request(0, 58.3451310, 66.9541079 - 58.3451310, 206)]
        phase = collective_decode_phase(requests)
        self.assertAlmostEqual(phase["decode_s"], 13.7142218)
        self.assertEqual(phase["decode_tokens"], 412)
        self.assertAlmostEqual(phase["tps"], 412 / 13.7142218)
        overlap = [request(0, 1, 10, 20), request(0, 1, 10, 30)]
        self.assertEqual(collective_decode_phase(overlap), {"tps": 5, "decode_s": 10, "decode_tokens": 50})
        overlap[1]["start_offset_s"] = 5
        self.assertAlmostEqual(collective_decode_phase(overlap)["tps"], 50 / 15)
        for item in overlap:
            item["ttft_s"] += 1000
        self.assertAlmostEqual(collective_decode_phase(overlap)["tps"], 50 / 15)

    def test_partial_decode_requires_all_successful_observations(self):
        requests = [{"status": "ok", "start_offset_s": 0, "ttft_s": 1, "decode_s": 2, "decode_token_count": 4, "completion_tokens": 5},
                    {"status": "ok", "start_offset_s": 5, "ttft_s": 2, "decode_s": 3, "decode_token_count": 6, "completion_tokens": 7},
                    {"status": "failed"}]
        row = {"status": "failed", "concurrency": 3, "wall_s": 20, "requests": requests}
        self.assertEqual(collective_row_metrics(row, partial=True)["decode_tps"], 2)
        self.assertEqual(collective_row_metrics(row, partial=True)["group_output_tps"], 12 / 20)
        for key in ("start_offset_s", "ttft_s", "decode_s", "decode_token_count"):
            original = requests[1].pop(key)
            self.assertNotIn("decode_tps", collective_row_metrics(row, partial=True), key)
            requests[1][key] = original

    def test_rejects_contradictory_wall_unrepresentable_end_and_unsafe_sums(self):
        requests = [{"status": "ok", "start_offset_s": 0, "ttft_s": 2,
                     "decode_s": 3, "decode_token_count": 4, "prompt_tokens": 10, "wall_s": 5}]
        self.assertIsNotNone(collective_decode_phase(requests))
        requests[0]["wall_s"] = 4.99
        self.assertIsNone(collective_decode_phase(requests))
        requests[0]["wall_s"] = 1
        self.assertIsNone(collective_prefill_phase(requests))
        requests[0].update(wall_s=5, start_offset_s=1e308)
        self.assertIsNone(collective_decode_phase(requests))
        self.assertIsNone(collective_prefill_phase(requests))
        requests[0].update(start_offset_s=0, decode_token_count=2**53-1, prompt_tokens=2**53-1)
        requests.append(dict(requests[0], decode_token_count=1, prompt_tokens=1))
        self.assertIsNone(collective_decode_phase(requests))
        self.assertIsNone(collective_prefill_phase(requests))

    def test_phase_wall_optional_but_invalid_values_are_rejected(self):
        request = {"start_offset_s": 0, "ttft_s": 2, "decode_s": 3,
                   "prompt_tokens": 10, "decode_token_count": 4}
        for wall in (None, 5):
            with self.subTest(wall=wall):
                request["wall_s"] = wall
                self.assertIsNotNone(collective_prefill_phase([request]))
                self.assertIsNotNone(collective_decode_phase([request]))
        del request["wall_s"]
        self.assertIsNotNone(collective_prefill_phase([request]))
        self.assertIsNotNone(collective_decode_phase([request]))
        for wall in (-1, "5", True, float("nan"), float("inf"), [], {}):
            with self.subTest(wall=wall):
                request["wall_s"] = wall
                self.assertIsNone(collective_prefill_phase([request]))
                self.assertIsNone(collective_decode_phase([request]))
    def test_single_row_unchanged(self):
        row = {"status": "ok", "concurrency": 1, "prefill_tps": 120.0, "decode_tps": 12.0, "ttft_s": 1.0}
        metrics = collective_row_metrics(row)
        self.assertEqual(metrics, {})

    def test_prompt_median_preserves_even_midpoint_and_requires_complete_counts(self):
        row = {"status": "ok", "concurrency": 2, "requests": [
            {"status": "ok", "prompt_tokens": 100, "ttft_s": 1},
            {"status": "ok", "prompt_tokens": 101, "ttft_s": 2}]}
        self.assertEqual(collective_row_metrics(row)["prompt_tokens"], 100.5)
        row["requests"][1]["prompt_tokens"] = None
        self.assertNotIn("prompt_tokens", collective_row_metrics(row))

    def test_enrich_sets_wall_metric_sources(self):
        row = {
            "status": "ok",
            "concurrency": 2,
            "wall_s": 12.0,
            "aggregate_completion_tokens": 200,
            "requests": [
                {"status": "ok", "prefill_tps": 5000.0, "decode_tps": 30.0, "start_offset_s": 0.0, "ttft_s": 3.0, "prompt_tokens": 20010, "completion_tokens": 100},
                {"status": "ok", "prefill_tps": 4000.0, "decode_tps": 40.0, "start_offset_s": 0.0, "ttft_s": 5.0, "prompt_tokens": 20011, "completion_tokens": 100},
            ],
        }
        self.assertTrue(enrich_result_row(row))
        self.assertAlmostEqual(row["group_output_tps"], 200 / 12.0)
        self.assertNotIn("decode_tps", row)
        self.assertIn("group_wall_s", row["metric_sources"]["group_output_tps"])


if __name__ == "__main__":
    unittest.main()
