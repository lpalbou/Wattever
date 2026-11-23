import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from wattever import __version__, hardware, server, store
from wattever.validation import validate_run
from wattever.privacy import public_projection


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for name, value in (("RUNS_DIR", self.temp.name + "/runs"),
                            ("TRASH_DIR", self.temp.name + "/trash")):
            patcher = patch.object(store, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.run = {"id": "sample", "created_at": "2026-10-26T12:00:00+00:00",
                    "status": "completed", "gpu": {"name": "GPU", "uuid": "private-id"},
                    "host": {"platform": "windows"}, "config": {"models": ["model"],
                    "sizes": [512], "concurrency_levels": [1]}, "corpus": {},
                    "results": [{"model": "model", "target_tokens": 512, "concurrency": 1,
                                 "status": "ok", "decode_tps": 10}]}

    def test_summary_separates_failures_from_unattempted_cases(self):
        self.run['results'] = [dict(self.run['results'][0], status=status)
                               for status in ('ok', 'ok', 'failed', 'skipped', 'cancelled')]
        summary = store.summarise(self.run)
        self.assertEqual([summary[key] for key in ('n_results', 'n_ok', 'n_failed', 'n_skipped', 'n_cancelled')],
                         [5, 2, 1, 1, 1])

    def test_save_retries_transient_permission_error(self):
        store.save(self.run)
        updated = copy.deepcopy(self.run)
        updated["label"] = "updated"
        replace = store.os.replace
        calls = []
        def transient(src, dst):
            calls.append((src, dst))
            if len(calls) == 1:
                raise PermissionError("temporarily locked")
            return replace(src, dst)
        with patch.object(store.os, "replace", side_effect=transient), patch.object(store.time, "sleep") as sleep:
            store.save(updated)
        self.assertEqual(store.load("sample"), updated)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(0.025)
        self.assertEqual(list(Path(store.RUNS_DIR).glob("*.tmp")), [])

    def test_storage_experiment_validates_frozen_measurements(self):
        self.run['storage_comparison']={'protocol':'wattever-storage-comparison-v1',
            'baseline_label':'Samsung','alternative_label':'NVMe','rows':[],
            'baseline_results':copy.deepcopy(self.run['results']),
            'alternate_results':copy.deepcopy(self.run['results'])}
        self.assertEqual(validate_run(self.run),[])
        self.run['storage_comparison']['alternate_results'][0]['model']=None
        self.assertTrue(any('storage_comparison.alternate_results[0].model' in error for error in validate_run(self.run)))

    def test_save_permanent_permission_error_preserves_original(self):
        store.save(self.run)
        original = Path(store.path_for("sample")).read_bytes()
        updated = copy.deepcopy(self.run)
        updated["label"] = "unsaved"
        with patch.object(store.os, "replace", side_effect=PermissionError("locked")) as replace, patch.object(store.time, "sleep") as sleep:
            with self.assertRaises(PermissionError):
                store.save(updated)
        self.assertEqual(replace.call_count, 7)
        self.assertEqual(sleep.call_count, 6)
        self.assertEqual(Path(store.path_for("sample")).read_bytes(), original)
        self.assertEqual(list(Path(store.RUNS_DIR).glob("*.tmp")), [])

    def test_save_does_not_retry_other_errors(self):
        with patch.object(store.os, "replace", side_effect=OSError("other error")) as replace, patch.object(store.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                store.save(self.run)
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(list(Path(store.RUNS_DIR).glob("*.tmp")), [])

    def test_portable_metadata_classification_and_validation(self):
        machine = {"os": {"name": "Windows", "release": "11", "architecture": "AMD64"},
                   "cpu": {"name": "CPU", "physical_cores": 8, "logical_cores": 16},
                   "system": {"manufacturer": "Board", "model": "X", "memory_bytes": 32000000000},
                   "gpus": [{"name": "B", "vendor": "nvidia", "memory_bytes": 16000000000,
                             "driver": "1", "sensor_index": 0},
                            {"name": "A", "vendor": "nvidia", "memory_bytes": 8000000000,
                             "driver": "1", "sensor_index": 1}]}
        with patch.object(hardware, "inventory", return_value=machine):
            store.prepare_run(self.run)
        self.assertNotIn("uuid", self.run["gpu"])
        self.assertEqual(validate_run(self.run), [])
        self.assertEqual(self.run["measurement_scope"]["runtime_gpu_placement_source"], "unknown")
        self.assertEqual(self.run["measurement_scope"]["phase_protocol"], "observed-output-phases-v2")
        reordered = copy.deepcopy(self.run)
        machine["gpus"].reverse()
        with patch.object(hardware, "inventory", return_value=machine):
            store.prepare_run(reordered)
        self.assertEqual(reordered["classification"], self.run["classification"])
        store.save(self.run)
        self.assertEqual(store.load("sample"), self.run)
        self.assertEqual(store.summarise(self.run)["concurrency_levels"], [1])

    def test_malformed_neighbors_do_not_hide_valid_runs(self):
        store.save(self.run)
        for filename, value in (("invalid name.json", "{}"), ("array.json", "[]"),
                                ("broken.json", "{"), ("rows.json", '{"results":[null]}')):
            Path(store.RUNS_DIR, filename).write_text(value, encoding="utf-8")
        self.assertEqual([r["id"] for r in store.list_runs()], ["sample"])
        with self.assertRaises(ValueError):
            store.path_for("../escape")
        self.assertNotEqual(store.new_run_id("GPU"), store.new_run_id("GPU"))

    def test_download_and_active_run_deletion_guard(self):
        store.save(self.run)
        bench = SimpleNamespace(running=True, run=self.run, _start_lock=threading.Lock())
        with patch.object(server, "BENCH", bench):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:%d/api/runs/sample" % httpd.server_port
                with urllib.request.urlopen("http://127.0.0.1:%d/api/version" % httpd.server_port) as response:
                    information = json.load(response)
                    self.assertEqual(information["application"], "Wattever")
                    self.assertEqual(information["version"], __version__)
                    self.assertEqual(information["build_id"], server.BUILD_ID)
                with urllib.request.urlopen(base + "/download") as response:
                    self.assertEqual(json.load(response), public_projection(self.run))
                    self.assertIn('filename="sample.json"', response.headers["Content-Disposition"])
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    urllib.request.urlopen(urllib.request.Request(base, method="DELETE"))
                self.assertEqual(rejected.exception.code, 409)
                self.assertTrue(Path(store.path_for("sample")).is_file())
                bench.running = False
                with urllib.request.urlopen(urllib.request.Request(base, method="DELETE")) as response:
                    self.assertTrue(json.load(response)["deleted"])
                self.assertEqual(store.list_runs(), [])
                self.assertEqual(len(list(Path(store.TRASH_DIR).glob("*.json"))), 1)
                self.assertIsNone(bench.run)
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()

    def test_rename_validates_and_reaches_a_running_benchmark(self):
        self.run.update(label="Local endpoint", notes="generated")
        store.save(self.run)
        for bad in ({"label": ""}, {"label": "   "}, {"label": "x" * 161}, {"label": 7}, {"notes": "x" * 5001}, {"notes": ["a"]}):
            with self.assertRaises(ValueError):
                store.set_meta("sample", **bad)
        self.assertEqual(store.load("sample")["label"], "Local endpoint")
        saved = store.set_meta("sample", label="  vLLM 188k  ", notes=" two cards \n")
        self.assertEqual((saved["label"], saved["notes"]), ("vLLM 188k", "two cards"))
        self.assertEqual(store.set_meta("sample", notes="")["label"], "vLLM 188k", "an omitted field is kept")
        self.assertIsNone(store.set_meta("missing", label="x"))

        # The benchmark thread owns a running record: the edit goes to its copy,
        # is served at once and reaches the file with that thread's next write.
        live = copy.deepcopy(store.load("sample"))
        live["status"] = "running"
        store.save(live)
        bench = SimpleNamespace(running=True, run=live, _start_lock=threading.Lock())
        with patch.object(server, "BENCH", bench):
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:%d" % httpd.server_port
                def post(body):
                    return urllib.request.urlopen(urllib.request.Request(
                        base + "/api/runs/sample/meta", data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json", "Origin": base}, method="POST"))
                with post({"label": "Strata 2 cards", "notes": "live edit"}) as response:
                    self.assertEqual(json.load(response)["label"], "Strata 2 cards")
                self.assertEqual((live["label"], live["notes"]), ("Strata 2 cards", "live edit"))
                self.assertEqual(store.load("sample")["label"], "vLLM 188k", "the helper does not write beside the benchmark thread")
                with urllib.request.urlopen(base + "/api/runs") as response:
                    listed = json.load(response)["runs"][0]
                self.assertEqual((listed["label"], listed["notes"], listed["status"]), ("Strata 2 cards", "live edit", "running"))
                with urllib.request.urlopen(base + "/api/runs/sample") as response:
                    self.assertEqual(json.load(response)["label"], "Strata 2 cards")
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    post({"label": ""})
                self.assertEqual(rejected.exception.code, 400)
                self.assertEqual(live["label"], "Strata 2 cards")
                store.save(live)                     # the benchmark thread's next write
                bench.running = False
                with post({"notes": "after the run"}):
                    pass
                self.assertEqual((store.load("sample")["label"], store.load("sample")["notes"]), ("Strata 2 cards", "after the run"))
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join()

    def test_summary_carries_the_recorded_client_of_an_endpoint_benchmark(self):
        client = {"hardware": {"gpus": [{"name": "GPU"}], "cpu": {}}, "host": {}, "gpu": {"name": "GPU"}}
        self.run.update(provider={"kind": "openai", "inference_location": "loopback"}, measurement_client=client)
        summary = store.summarise(self.run)
        self.assertEqual(summary["provider"]["inference_location"], "loopback")
        self.assertEqual(summary["measurement_client"], client)
        del self.run["provider"], self.run["measurement_client"]
        self.assertEqual((store.summarise(self.run)["provider"], store.summarise(self.run)["measurement_client"]), ({}, None))

    def test_json_downloads_preserve_legacy_wall_rate_as_recorded_evidence(self):
        source = "group_wall_clock; decode is sum(completion_tokens)/group_wall_s"
        self.run["results"] = [{"model": "model", "target_tokens": 512, "concurrency": 2,
                                "status": "ok", "decode_tps": 12, "metric_sources": {"decode_tps": source},
                                "requests": [{"status": "ok"}, {"status": "ok"}]}]
        store.save(self.run)
        before = Path(store.path_for("sample")).read_bytes()
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:%d" % httpd.server_port
            for path in ("/api/runs/sample/download", "/api/export.json"):
                with self.subTest(path=path), urllib.request.urlopen(base + path) as response:
                    body = json.load(response)
                    run = body["runs"][0] if "runs" in body else body
                    row = run["results"][0]
                    self.assertIsNone(row["decode_tps"])
                    self.assertEqual(row["recorded_decode_tps"], 12)
                    self.assertEqual(row["recorded_metric_sources"]["decode_tps"], source)
                    self.assertIsNone(row["derived_metrics"]["decode_tps"])
            self.assertEqual(Path(store.path_for("sample")).read_bytes(), before)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

    def test_validator_rejects_incomplete_groups_and_nonfinite_metrics(self):
        self.run["results"] = [{"model": "m", "status": "ok", "concurrency": 2,
                               "aggregate_completion_tps": float("nan")}]
        errors = validate_run(self.run)
        self.assertTrue(any("one entry" in e for e in errors))
        self.assertTrue(any("non-finite" in e for e in errors))

    def test_validator_handles_malformed_v2_metadata(self):
        self.run.update(schema_version=2, hardware="bad", corpus="bad", application={})
        errors = validate_run(self.run)
        self.assertTrue(any("hardware must be" in e for e in errors))
        self.assertTrue(any("sha256" in e for e in errors))
        self.assertTrue(any("protocol_version" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
