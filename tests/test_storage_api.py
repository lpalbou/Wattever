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
                    self.assertEqual(json.load(response), {"application": "Wattever", "version": __version__})
                with urllib.request.urlopen(base + "/download") as response:
                    self.assertEqual(json.load(response), self.run)
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
