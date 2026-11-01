import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wattever import bandwidth


class BandwidthTests(unittest.TestCase):
    def test_summary_uses_completed_bytes_and_median_rate(self):
        result = bandwidth._summary([{"bytes": 100, "elapsed_s": 1},
                                     {"bytes": 100, "elapsed_s": 4},
                                     {"bytes": 100, "elapsed_s": 2}])
        self.assertEqual(result["bytes_per_second"], 50)
        self.assertEqual(result["statistic"], "median")
        with self.assertRaises(ValueError):
            bandwidth._summary([{"bytes": 100, "elapsed_s": 0}])

    def test_temporary_storage_file_removed_on_read_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bandwidth, "FILE_BYTES", 4096), \
             patch.object(bandwidth, "BLOCK_BYTES", 4096), \
             patch.object(bandwidth.pu, "IS_WINDOWS", True), \
             patch.object(bandwidth, "_windows_reads", side_effect=OSError("private path")):
            with self.assertRaises(OSError):
                bandwidth.storage_probe(directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_worker_records_independent_failures_without_paths(self):
        with patch.object(bandwidth, "storage_probe", side_effect=OSError("C:/secret/weights")), \
             patch.object(bandwidth, "memory_probe", return_value={"status":"ok", "bytes_per_second":456}), \
             patch.object(bandwidth, "cuda_probe", return_value={"status":"ok", "bytes_per_second":123}):
            profile = bandwidth._worker("ignored", 0)
        self.assertEqual(profile["storage_to_ram"]["status"], "unavailable")
        self.assertEqual(profile["ram_to_vram"]["bytes_per_second"], 123)
        self.assertNotIn("secret", json.dumps(profile))

    def test_timeout_cleans_worker_scratch_and_reports_unavailable(self):
        def timeout(args, **kwargs):
            (Path(args[-2]) / "wattever-bandwidth-owned.bin").write_bytes(b"data")
            raise subprocess.TimeoutExpired(args, 75)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bandwidth.subprocess, "run", side_effect=timeout):
            result = bandwidth.measure(directory=directory)
            self.assertEqual(result["ram_to_vram"]["status"], "unavailable")
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_later_backfill_preserves_measurements_and_original_sessions(self):
        original = {"classification":{"hardware_class":"same"},
                    "gpu":{"name":"GPU", "vram_total_mb":16000, "driver":"1"},
                    "results":[{"status":"ok", "prefill_tps":123}],
                    "sessions":[{"id":"old"}], "active_session_id":"old"}
        run = copy.deepcopy(original)
        profile = {"captured_at":"later", "storage_to_ram":{"status":"ok"}}
        value = bandwidth.backfill(run, original, profile)
        self.assertEqual(run["results"], original["results"])
        self.assertEqual(run["sessions"], original["sessions"])
        self.assertEqual(value["provenance"], "measured_later_on_matching_hardware")
        self.assertNotIn("provenance", profile)
        self.assertEqual(run['hardware_measurement_history'][0]['transfer_bandwidth'],value)
        different = copy.deepcopy(original)
        different["gpu"]["driver"] = "2"
        with self.assertRaisesRegex(ValueError, "driver differs"):
            bandwidth.backfill(run, different, profile)
        different["classification"]["hardware_class"] = "other"
        with self.assertRaisesRegex(ValueError, "Hardware differs"):
            bandwidth.backfill(run, different, profile)

    def test_validation_rejects_inconsistent_rates_and_unavailable_numbers(self):
        from wattever.validation import validate_transfer_bandwidth
        sample = bandwidth._summary([{"bytes":100, "elapsed_s":2}], method="fake")
        profile = {"schema_version":1, "protocol":"wattever-transfer-v1", "unit":"bytes_per_second",
                   "scope":"benchmark_client", "captured_at":"2026-10-31T22:00:00Z",
                   "storage_to_ram":sample, "ram_to_vram":{"status":"unavailable","reason":"No CUDA"}}
        self.assertEqual(validate_transfer_bandwidth(profile), [])
        profile['storage_to_ram']['bytes_per_second'] = 500
        self.assertTrue(validate_transfer_bandwidth(profile))
        profile['storage_to_ram']['bytes_per_second'] = 50
        profile['ram_to_vram']['bytes_per_second'] = 0
        self.assertTrue(validate_transfer_bandwidth(profile))

    def test_private_folder_does_not_leak_into_public_configuration(self):
        from wattever import endpoints
        self.assertNotIn("storage_probe_directory", endpoints.public_config({
            "storage_probe_directory":"C:/private/models", "provider":"lmstudio"}))
        self.assertNotIn("storage_probe_directories", endpoints.public_config({
            "storage_probe_directories":["F:/private/models"], "provider":"lmstudio"}))

    def test_native_ram_copy_completes_and_releases_buffers(self):
        result = bandwidth.memory_probe(workers=2, buffer_bytes=4096, copies=2)
        self.assertEqual(result["working_set_bytes"], 16384)
        self.assertTrue(result["verified_readback"])
        self.assertEqual([s["bytes"] for s in result["samples"]], [16384] * 3)

    def test_multiple_volumes_keep_independent_samples_and_cleanup(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            def worker(args, **kwargs):
                from types import SimpleNamespace
                extras=json.loads(args[-1])
                self.assertEqual(len(extras),1)
                self.assertEqual(Path(extras[0]).parent,Path(second).resolve())
                return SimpleNamespace(returncode=0,stdout=json.dumps({"protocol":"wattever-transfer-v1"}))
            with patch.object(bandwidth.subprocess,'run',side_effect=worker):
                bandwidth.measure(directory=first,directories=[first,second,second])
            self.assertEqual(list(Path(first).iterdir()),[])
            self.assertEqual(list(Path(second).iterdir()),[])


if __name__ == "__main__":
    unittest.main()
