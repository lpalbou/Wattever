import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from wattever import bandwidth


class BandwidthTests(unittest.TestCase):
    def matching_capture_fixture(self):
        machine = {'cpu': {'name': 'Test CPU', 'physical_cores': 6, 'logical_cores': 12},
                   'system': {'memory_bytes': 64 * 1024 ** 3},
                   'memory': {'installed_capacity_bytes': 64 * 1024 ** 3, 'module_count': 4,
                              'configured_clock_mhz_reported': [2133]},
                   'gpus': [{'name': 'RTX 5060 Ti', 'memory_bytes': 16 * 1024 ** 3, 'driver': '1'}]}
        def profile(hour, rate):
            return {'schema_version': 1, 'protocol': 'wattever-transfer-v1', 'unit': 'bytes_per_second',
                    'scope': 'benchmark_client', 'captured_at': '2026-11-01T%02d:00:00+00:00' % hour,
                    'storage_to_ram': bandwidth._summary([{'bytes': rate, 'elapsed_s': 1}], method='fixture'),
                    'ram_to_vram': bandwidth._summary([{'bytes': rate * 10, 'elapsed_s': 1}], method='fixture', gpu_name='RTX 5060 Ti')}
        independent, session, benchmark = profile(1, 100), profile(3, 300), profile(2, 200)
        independent['provenance'] = 'measured_later_on_matching_hardware'
        run = {'id': 'saved', 'created_at': '2026-11-01T00:00:00Z', 'config': {}, 'gpu': {}, 'host': {}, 'results': [],
               'hardware': dict(copy.deepcopy(machine), transfer_bandwidth=benchmark),
               'hardware_measurement_history': [{'kind': 'transfer_bandwidth', 'hardware_snapshot': copy.deepcopy(machine),
                                                 'transfer_bandwidth': independent}],
               'sessions': [{'id': 'loaded-session', 'hardware': copy.deepcopy(machine), 'transfer_bandwidth': session}]}
        return machine, run

    def test_saved_capture_restores_independent_probe_without_native_work_or_writes(self):
        from wattever import store
        machine, run = self.matching_capture_fixture()
        with tempfile.TemporaryDirectory() as directory, patch.object(store, 'RUNS_DIR', directory):
            path = Path(directory) / 'saved.json'
            path.write_text(json.dumps(run), encoding='utf-8')
            original = path.read_bytes()
            with patch.object(bandwidth, '_latest', None), patch.object(bandwidth, 'measure') as measure, \
                 patch.object(bandwidth.subprocess, 'run') as native, patch.object(store, 'save') as save:
                restored = bandwidth.saved_capture(machine, {'name': 'RTX 5060 Ti'})
                self.assertIsNone(bandwidth.latest(), 'Reading history must not turn a saved capture into a fresh probe')
                measure.assert_not_called(); native.assert_not_called(); save.assert_not_called()
            self.assertEqual(restored['storage_to_ram']['bytes_per_second'], 100)
            self.assertEqual(restored['captured_at'], '2026-11-01T01:00:00+00:00')
            self.assertEqual(restored['provenance'], 'measured_later_on_matching_hardware')
            self.assertEqual(restored['source'], 'saved_capture')
            self.assertEqual(restored['saved_capture']['capture_kind'], 'independent')
            self.assertEqual(restored['saved_capture']['source_run_id'], 'saved')
            self.assertFalse(restored['saved_capture']['measured_this_helper_session'])
            restored['storage_to_ram']['bytes_per_second'] = 999
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_saved_capture_rejects_card_driver_cpu_ram_and_clock_changes(self):
        from wattever import store
        machine, run = self.matching_capture_fixture()
        with tempfile.TemporaryDirectory() as directory, patch.object(store, 'RUNS_DIR', directory):
            (Path(directory) / 'saved.json').write_text(json.dumps(run), encoding='utf-8')
            alternatives = []
            for field, value in [('name', 'RTX 5070 Ti'), ('driver', '2'), ('memory_bytes', 8 * 1024 ** 3)]:
                changed = copy.deepcopy(machine); changed['gpus'][0][field] = value; alternatives.append(changed)
            for field, value in [('installed_capacity_bytes', 32 * 1024 ** 3), ('module_count', 2),
                                 ('configured_clock_mhz_reported', [3200]), ('configured_clock_mhz_reported', [])]:
                changed = copy.deepcopy(machine); changed['memory'][field] = value; alternatives.append(changed)
            changed = copy.deepcopy(machine); changed['cpu']['name'] = 'Other CPU'; alternatives.append(changed)
            changed = copy.deepcopy(machine); changed['gpus'].append(dict(changed['gpus'][0], name='RTX 5070 Ti')); alternatives.append(changed)
            for current in alternatives:
                with self.subTest(current=current):
                    self.assertIsNone(bandwidth.saved_capture(current))
            self.assertIsNone(bandwidth.saved_capture(machine, {'name': 'RTX 5070 Ti'}))

    def test_saved_capture_uses_newest_matching_independent_capture(self):
        from wattever import store
        machine, run = self.matching_capture_fixture()
        newer = copy.deepcopy(run['hardware_measurement_history'][0])
        newer['transfer_bandwidth']['captured_at'] = '2026-11-01T02:00:00+00:00'
        run['hardware_measurement_history'].append(newer)
        wrong_card = copy.deepcopy(run); wrong_card['id'] = 'wrong-card'
        wrong_card['hardware']['gpus'][0]['name'] = 'RTX 5070 Ti'
        wrong_card['hardware_measurement_history'] = []; wrong_card['sessions'] = []
        wrong_card['hardware']['transfer_bandwidth']['captured_at'] = '2026-11-01T23:00:00+00:00'
        with tempfile.TemporaryDirectory() as directory, patch.object(store, 'RUNS_DIR', directory):
            for item in (run, wrong_card):
                (Path(directory) / (item['id'] + '.json')).write_text(json.dumps(item), encoding='utf-8')
            restored = bandwidth.saved_capture(machine)
            self.assertEqual(restored['captured_at'], '2026-11-01T02:00:00+00:00')
            self.assertEqual(restored['saved_capture']['source_run_id'], 'saved')
            self.assertEqual(restored['saved_capture']['capture_kind'], 'independent')

    def test_environment_prefers_a_fresh_capture_to_saved_history(self):
        from types import SimpleNamespace
        from wattever import server
        machine, run = self.matching_capture_fixture()
        fresh = run['sessions'][0]['transfer_bandwidth']
        cli = SimpleNamespace(available=True, exe='fake', server_running=lambda: False, version=lambda: 'test')
        handler = SimpleNamespace(models_payload=lambda: [])
        with patch.object(server.hardware, 'inventory', return_value=machine), \
             patch.object(server.power, 'gpu_info', return_value={'name': 'RTX 5060 Ti'}), \
             patch.object(server.power, 'list_gpus', return_value=[]), patch.object(server.lmstudio, 'LmsCli', return_value=cli), \
             patch.object(server.store, 'host_info', return_value={}), patch.object(server.store, 'load_config', return_value={}), \
             patch.object(server.corpus, 'master_stats', return_value={}), patch.object(bandwidth, 'latest', return_value=fresh), \
             patch.object(bandwidth, 'saved_capture') as saved:
            payload = server.Handler.env_payload(handler)
            self.assertEqual(payload['hardware']['transfer_bandwidth'], fresh)
            saved.assert_not_called()

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

    def test_session_transfer_capture_updates_only_its_engine_group(self):
        run={'active_session_id':'new','sessions':[{'id':'new','engine_group_id':'strata'}],
             'engine_groups':[{'id':'lm','hardware':{'transfer_bandwidth':{'captured_at':'old'}}},
                              {'id':'strata','hardware':{}}]}
        bandwidth.attach(run,{'captured_at':'new','storage_to_ram':{'volume':'F:'}})
        self.assertEqual(run['engine_groups'][0]['hardware']['transfer_bandwidth']['captured_at'],'old')
        self.assertEqual(run['engine_groups'][1]['hardware']['transfer_bandwidth']['storage_to_ram']['volume'],'F:')

    def test_csv_uses_the_execution_session_ram_clock(self):
        import csv,io
        from wattever import store
        run={'id':'sample','hardware':{'memory':{'configured_clock_mhz_reported':[2133]}},
             'sessions':[{'id':'new','hardware':{'memory':{'configured_clock_mhz_reported':[3200]}}}],
             'results':[{'model':'m','target_tokens':512,'concurrency':1,'status':'ok','session_id':'new'}]}
        row=next(csv.DictReader(io.StringIO(store.to_csv([run]))))
        self.assertEqual(row['ram_configured_clock_mhz_reported'],'[3200]')

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
