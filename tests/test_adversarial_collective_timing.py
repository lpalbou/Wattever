"""Regression cases for the shared time origin of concurrent requests."""
import copy
import unittest

from wattever.collective_metrics import collective_prefill_phase, collective_row_metrics, enrich_result_row


class CollectiveTimingRegression(unittest.TestCase):
    def setUp(self):
        self.row = {
            "status": "ok", "concurrency": 2, "wall_s": 10,
            "requests": [
                {"status": "ok", "prompt_tokens": 100, "completion_tokens": 10,
                 "start_offset_s": 0, "ttft_s": 3},
                {"status": "ok", "prompt_tokens": 100, "completion_tokens": 10,
                 "start_offset_s": 5, "ttft_s": 2},
            ],
        }

    def test_group_prefill_excludes_dispatch_gaps(self):
        metrics = collective_row_metrics(self.row)
        self.assertAlmostEqual(metrics["prefill_tps"], 200 / 5)
        self.assertEqual(collective_prefill_phase(self.row["requests"]),
                         {"prefill_s": 5, "prefill_tokens": 200, "prompt_tokens": 200, "tps": 40})
        self.assertEqual(metrics["group_output_tps"], 2)

    def test_prefill_union_overlap_offset_zero_and_invalid_observations(self):
        requests = copy.deepcopy(self.row["requests"])
        requests[1].update(start_offset_s=2, ttft_s=3, prompt_tokens=0)
        self.assertEqual(collective_prefill_phase(requests),
                         {"prefill_s": 5, "prefill_tokens": 100, "prompt_tokens": 100, "tps": 20})
        for key, bad in (("ttft_s", 0), ("start_offset_s", float('nan')),
                         ("prompt_tokens", True), ("prompt_tokens", None)):
            invalid = copy.deepcopy(requests)
            invalid[1][key] = bad
            self.assertIsNone(collective_prefill_phase(invalid))

    def test_missing_ttft_or_start_offset_never_invents_group_prefill(self):
        for key in ("ttft_s", "start_offset_s"):
            with self.subTest(missing=key):
                row = copy.deepcopy(self.row)
                row["requests"][1][key] = None
                self.assertNotIn("prefill_tps", collective_row_metrics(row))

    def test_legacy_aggregate_survives_when_request_timing_unavailable(self):
        row = {"status": "ok", "concurrency": 2, "aggregate_completion_tps": 17}
        enrich_result_row(row)
        self.assertEqual(row["aggregate_completion_tps"], 17)


if __name__ == "__main__":
    unittest.main()
