"""Historical evidence and incomplete groups must survive the CSV path."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wattever import store
from wattever.collective_metrics import enrich_result_row


class CsvProvenanceRegression(unittest.TestCase):
    def test_single_csv_uses_successful_request_count_and_retains_recorded_aggregate(self):
        row = {"status": "ok", "concurrency": 1, "wall_s": 4,
               "completion_tokens": 99, "aggregate_completion_tokens": 777,
               "requests": [{"status": "ok", "completion_tokens": 12}]}
        before = json.dumps(row, sort_keys=True)
        def export():
            return next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        value = export()
        self.assertEqual(value["aggregate_completion_tokens"], "12")
        self.assertEqual(float(value["group_output_tps"]), 3)
        self.assertEqual(value["recorded_aggregate_completion_tokens"], "777")
        self.assertEqual(json.dumps(row, sort_keys=True), before)
        row["requests"][0]["completion_tokens"] = 0
        self.assertEqual(export()["aggregate_completion_tokens"], "0")
        self.assertEqual(float(export()["group_output_tps"]), 0)
        for count in (None, -1, True, 1.5, float("nan"), 2**53):
            with self.subTest(count=count):
                row["requests"][0]["completion_tokens"] = count
                self.assertEqual(export()["aggregate_completion_tokens"], "")
                self.assertEqual(export()["group_output_tps"], "")

    def test_single_csv_failed_or_inconsistent_requests_have_no_output_rate(self):
        row = {"status": "failed", "concurrency": 1, "wall_s": 4,
               "completion_tokens": 12, "aggregate_completion_tokens": 777}
        def export():
            return next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        for status in ("failed", "cancelled", "skipped"):
            with self.subTest(status=status):
                row["status"] = status
                self.assertEqual(export()["group_output_tps"], "")
                self.assertEqual(export()["aggregate_completion_tokens"], "")
                self.assertEqual(export()["recorded_aggregate_completion_tokens"], "777")
        row["status"] = "ok"
        self.assertEqual(export()["aggregate_completion_tokens"], "12")
        self.assertEqual(float(export()["group_output_tps"]), 3)
        for requests in ([{"status": "failed", "completion_tokens": 12}],
                         [{"status": "ok", "completion_tokens": 12}, {"status": "ok", "completion_tokens": 12}],
                         [None]):
            with self.subTest(requests=requests):
                row["requests"] = requests
                self.assertEqual(export()["aggregate_completion_tokens"], "")
                self.assertEqual(export()["group_output_tps"], "")

    def test_only_explicit_boundary_version_identifies_new_decode_duration(self):
        row = {"status": "ok", "concurrency": 1, "ttft_s": 2, "decode_s": 3,
               "wall_s": 10, "prompt_tokens": 100, "decode_token_count": 6,
               "timing_scope": {"decode_end": "unverified description"}}
        def sources():
            value = next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
            return json.loads(value["derived_metric_sources_json"])
        self.assertIn("approximate", sources()["decode_tps"])
        row["timing_scope"].update(phase_protocol="observed-output-phases-v2",
                                   decode_end_boundary="last_observed_nonempty_output")
        self.assertNotIn("approximate", sources()["decode_tps"])
        row["timing_scope"]["decode_end_boundary"] = "stream_eof"
        self.assertIn("approximate", sources()["decode_tps"])

    def test_mixed_group_cannot_claim_new_decode_boundary(self):
        marker = {"phase_protocol": "observed-output-phases-v2",
                  "decode_end_boundary": "last_observed_nonempty_output"}
        request = {"status": "ok", "start_offset_s": 0, "ttft_s": 1,
                   "decode_s": 2, "decode_token_count": 4, "timing_scope": marker}
        row = {"status": "ok", "concurrency": 2, "wall_s": 10,
               "requests": [request, dict(request, start_offset_s=5, timing_scope={})]}
        def source():
            value = next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
            return json.loads(value["derived_metric_sources_json"])["decode_tps"]
        self.assertIn("approximate", source())
        row["requests"][1]["timing_scope"] = marker
        self.assertNotIn("approximate", source())

    def test_single_csv_derives_observed_phase_and_keeps_original_rates(self):
        row = {"status": "ok", "concurrency": 1, "wall_s": 20, "ttft_s": 2,
               "decode_s": 3, "prompt_tokens": 100, "decode_token_count": 6,
               "prefill_tps": 999, "decode_tps": 888}
        def export():
            return next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        value = export()
        self.assertEqual(float(value["prefill_tps"]), 50)
        self.assertEqual(float(value["decode_tps"]), 2)
        self.assertEqual(value["recorded_prefill_tps"], "999")
        self.assertEqual(value["recorded_decode_tps"], "888")
        self.assertIn("historical timing", json.loads(value["derived_metric_sources_json"])["decode_tps"])
        row["decode_token_count"] = None
        row["ttft_s"] = None
        value = export()
        self.assertEqual(value["prefill_tps"], "")
        self.assertEqual(value["decode_tps"], "")
        self.assertEqual(value["recorded_decode_tps"], "888")

    def test_explicit_enrichment_quarantines_legacy_wall_decode_without_evidence(self):
        source = "group_wall_clock; decode is sum(completion_tokens)/group_wall_s including queue and prefill"
        row = {"status": "ok", "concurrency": 2, "decode_tps": 50,
               "metric_sources": {"decode_tps": source}, "requests": []}
        self.assertTrue(enrich_result_row(row))
        self.assertEqual(row["recorded_decode_tps"], 50)
        self.assertEqual(row["recorded_metric_sources"]["decode_tps"], source)
        self.assertIsNone(row["decode_tps"])
        self.assertIn("unavailable", row["metric_sources"]["decode_tps"])
        self.assertFalse(enrich_result_row(row))

    def test_json_export_derives_on_public_copy_and_preserves_raw_and_source(self):
        source = "group_wall_clock; decode is sum(completion_tokens)/group_wall_s"
        requests = [{"status": "ok", "start_offset_s": 0, "ttft_s": 2,
                     "decode_s": 3, "decode_token_count": 6, "prompt_tokens": 100, "completion_tokens": 7},
                    {"status": "ok", "start_offset_s": 10, "ttft_s": 2,
                     "decode_s": 3, "decode_token_count": 9, "prompt_tokens": 100, "completion_tokens": 10}]
        row = {"status": "ok", "concurrency": 2, "wall_s": 20, "decode_tps": 9,
               "metric_sources": {"decode_tps": source}, "requests": requests}
        run = {"id": "case", "results": [row]}
        before = json.dumps(run, sort_keys=True)
        exported = store.export_projection(run)["results"][0]
        self.assertEqual(exported["decode_tps"], 15 / 6)
        self.assertEqual(exported["recorded_decode_tps"], 9)
        self.assertEqual(exported["recorded_metric_sources"]["decode_tps"], source)
        self.assertEqual(exported["derived_metrics"]["decode_tps"], 15 / 6)
        self.assertIn("historical timing may include trailing transport", exported["derived_metrics"]["metric_sources"]["decode_tps"])
        self.assertEqual(json.dumps(run, sort_keys=True), before)

    def test_phase_columns_and_sources_preserve_historical_request_evidence(self):
        requests = [
            {"status": "ok", "prompt_tokens": 100, "completion_tokens": 11,
             "start_offset_s": 0, "ttft_s": 2, "decode_s": 3, "decode_token_count": 10},
            {"status": "ok", "prompt_tokens": 200, "completion_tokens": 21,
             "start_offset_s": 10, "ttft_s": 2, "decode_s": 4, "decode_token_count": 20},
        ]
        row = {"status": "ok", "concurrency": 2, "wall_s": 20, "requests": requests,
               "prefill_tps": 999, "decode_tps": 888, "metric_sources": {"decode_tps": "old"}}
        before = json.dumps(row, sort_keys=True)
        exported = next(csv.DictReader(io.StringIO(store.to_csv([{"results": [row]}]))))
        self.assertEqual(float(exported["prefill_s"]), 4)
        self.assertEqual(int(exported["prefill_tokens"]), 300)
        self.assertEqual(float(exported["decode_s"]), 7)
        self.assertEqual(int(exported["decode_tokens"]), 30)
        self.assertEqual(json.loads(exported["request_metrics_json"]), requests)
        self.assertEqual(json.loads(exported["metric_sources_json"]), {"decode_tps": "old"})
        sources = json.loads(exported["derived_metric_sources_json"])
        self.assertIn("historical timing may include trailing transport", sources["decode_tps"])
        self.assertIn("client_ttft_prefill_proxy", sources["prefill_tps"])
        self.assertEqual(json.dumps(row, sort_keys=True), before)

    def test_successful_group_missing_a_request_has_no_derived_rate(self):
        row = {"status": "ok", "concurrency": 2, "wall_s": 10, "requests": [
            {"status": "ok", "completion_tokens": 100, "prompt_tokens": 100,
             "start_offset_s": 0, "ttft_s": 1}]}
        exported = next(csv.DictReader(io.StringIO(store.to_csv([{"id": "case", "results": [row]}]))))
        self.assertEqual(exported["prefill_tps"], "")
        self.assertEqual(exported["decode_tps"], "")

    def test_metadata_edit_preserves_recorded_historical_metrics(self):
        row = {"status": "ok", "concurrency": 2, "wall_s": 10,
               "decode_tps": 50, "prefill_tps": 999, "requests": [
                   {"status": "ok", "completion_tokens": 10, "prompt_tokens": 100, "ttft_s": 3},
                   {"status": "ok", "completion_tokens": 10, "prompt_tokens": 100, "ttft_s": 2}]}
        run = {"id": "case", "gpu": {}, "host": {}, "config": {}, "results": [row]}
        with tempfile.TemporaryDirectory() as folder, patch.object(store, "RUNS_DIR", folder):
            path = Path(folder) / "case.json"
            path.write_text(json.dumps(run), encoding="utf-8")
            loaded = store.load("case")
            exported = next(csv.DictReader(io.StringIO(store.to_csv([loaded]))))
            self.assertEqual(exported["recorded_decode_tps"], "50")
            store.set_meta("case", label="new label")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["results"], [row])


if __name__ == "__main__":
    unittest.main()
