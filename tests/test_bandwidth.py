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
            self.assertEqual(restored['saved_capture']['gpu_path_match'], 'unverified_legacy_single_gpu_capture')
            self.assertIn('unverified', restored['saved_capture']['gpu_path_match_note'])
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
             patch.object(bandwidth.power, 'list_gpus', return_value=[]), \
             patch.object(bandwidth, "cuda_probe", return_value={"status":"ok", "bytes_per_second":123}):
            profile = bandwidth._worker("ignored", 0)
        self.assertEqual(profile["storage_to_ram"]["status"], "unavailable")
        self.assertEqual(profile["ram_to_vram"]["bytes_per_second"], 123)
        self.assertNotIn("secret", json.dumps(profile))

    def test_each_gpu_is_measured_once_and_selected_failure_is_not_replaced(self):
        sensors = [{'index': 5, 'name':'Second GPU', 'vram_total_mb':16384, 'pci_bus_id':'private-bus-2'},
                   {'index': 0, 'name':'First GPU', 'vram_total_mb':16384, 'pci_bus_id':'private-bus-1'}]
        measured = bandwidth._summary([{'bytes':1000,'elapsed_s':1}], method='fixture')
        def probe(index):
            return measured if index == 0 else bandwidth._unavailable('Insufficient free VRAM')
        with patch.object(bandwidth.power, 'list_gpus', return_value=sensors), \
             patch.object(bandwidth, 'cuda_probe', side_effect=probe) as cuda, \
             patch.object(bandwidth, 'storage_probe', return_value=measured), \
             patch.object(bandwidth, 'memory_probe', return_value=measured):
            profile = bandwidth._worker('ignored', 5)
        self.assertEqual([call.args[0] for call in cuda.call_args_list], [0, 5])
        self.assertEqual([item['gpu_name'] for item in profile['ram_to_vram_devices']], ['First GPU','Second GPU'])
        self.assertEqual(profile['ram_to_vram']['gpu_sensor_index'], 5)
        self.assertEqual(profile['ram_to_vram']['status'], 'unavailable')
        self.assertNotIn('bytes_per_second', profile['ram_to_vram'])
        self.assertIsNone(profile['ram_to_vram']['physical_slot'])
        self.assertNotIn('private-bus', json.dumps(profile))
        profile['ram_to_vram']['reason'] = 'changed alias'
        self.assertEqual(profile['ram_to_vram_devices'][1]['reason'], 'Insufficient free VRAM')

    def test_fresh_cache_rejects_cpu_ram_gpu_or_path_changes(self):
        machine, _ = self.matching_capture_fixture()
        machine['gpus'][0].update(sensor_index=0, pcie={'width_current':8, 'generation_max_reported':3,'width_max_reported':16})
        profile = {'ram_to_vram_devices':[{'gpu_sensor_index':0,'gpu_name':'RTX 5060 Ti','gpu_memory_bytes':16*1024**3}]}
        sensors = [{'index':0,'name':'RTX 5060 Ti','pci_bus_id':'0000:01:00.0'}]
        profile['gpu_path_fingerprint'] = bandwidth._gpu_path_fingerprint(sensors)
        with patch.object(bandwidth, '_latest', profile), \
             patch.object(bandwidth, '_latest_hardware_key', bandwidth._capture_hardware_key(machine)):
            self.assertEqual(bandwidth.latest(machine, sensors=sensors), profile)
            for change in ('cpu','ram','clock','gpu','path','index','driver'):
                current = copy.deepcopy(machine)
                if change == 'cpu': current['cpu']['name'] = 'Different CPU'
                elif change == 'ram': current['memory']['installed_capacity_bytes'] //= 2
                elif change == 'clock': current['memory']['configured_clock_mhz_reported'] = [3200]
                elif change == 'gpu': current['gpus'].append(dict(current['gpus'][0],name='Additional GPU',sensor_index=1))
                elif change == 'path': current['gpus'][0]['pcie']['width_current'] = 16
                elif change == 'index': current['gpus'][0]['sensor_index'] = 2
                elif change == 'driver': current['gpus'][0]['driver'] = '2'
                with self.subTest(change=change): self.assertIsNone(bandwidth.latest(current, sensors=sensors))

    def test_gpu_bus_change_invalidates_cache_even_when_indices_and_link_widths_match(self):
        machine, _ = self.matching_capture_fixture()
        machine['gpus'][0]['sensor_index'] = 0
        machine['gpus'].append(dict(machine['gpus'][0], name='RTX 5070 Ti',sensor_index=1))
        sensors = [{'index':0,'name':'RTX 5060 Ti','pci_bus_id':'00000000:01:00.0'},
                   {'index':1,'name':'RTX 5070 Ti','pci_bus_id':'00000000:02:00.0'}]
        profile = {'ram_to_vram_devices':[{'gpu_sensor_index':card['sensor_index'], 'gpu_name':card['name'],
                    'gpu_memory_bytes':card['memory_bytes']} for card in machine['gpus']],
                   'gpu_path_fingerprint':bandwidth._gpu_path_fingerprint(sensors)}
        moved = copy.deepcopy(sensors); moved[1]['pci_bus_id'] = '00000000:04:00.0'
        with patch.object(bandwidth, '_latest', profile), \
             patch.object(bandwidth, '_latest_hardware_key', bandwidth._capture_hardware_key(machine)):
            self.assertEqual(bandwidth.latest(machine,sensors=sensors), profile)
            self.assertIsNone(bandwidth.latest(machine,sensors=moved))
            self.assertIsNone(bandwidth.latest(machine,sensors=[]), 'A failed mapping check is unknown, not a match')
            del profile['gpu_path_fingerprint']
            self.assertIsNone(bandwidth.latest(machine,sensors=sensors), 'Old live captures cannot verify the PCI bus path')

    def test_saved_multi_gpu_capture_requires_matching_topology_fingerprint(self):
        from wattever import store
        machine, run = self.matching_capture_fixture()
        machine['gpus'].append(dict(machine['gpus'][0],name='RTX 5070 Ti'))
        for snapshot in [run['hardware'],run['hardware_measurement_history'][0]['hardware_snapshot'],run['sessions'][0]['hardware']]:
            snapshot['gpus'] = copy.deepcopy(machine['gpus'])
        sensors = [{'index':0,'name':'RTX 5060 Ti','pci_bus_id':'0000:01:00.0'},
                   {'index':1,'name':'RTX 5070 Ti','pci_bus_id':'0000:02:00.0'}]
        fingerprint = bandwidth._gpu_path_fingerprint(sensors)
        with tempfile.TemporaryDirectory() as directory, patch.object(store, 'RUNS_DIR', directory):
            path = Path(directory) / 'saved.json'
            path.write_text(json.dumps(run), encoding='utf-8')
            self.assertIsNone(bandwidth.saved_capture(machine,sensors=sensors), 'Legacy multi-GPU topology was not captured')
            for profile in [run['hardware']['transfer_bandwidth'],run['hardware_measurement_history'][0]['transfer_bandwidth'],run['sessions'][0]['transfer_bandwidth']]:
                profile['gpu_path_fingerprint'] = fingerprint
            path.write_text(json.dumps(run), encoding='utf-8')
            restored = bandwidth.saved_capture(machine,sensors=sensors)
            self.assertEqual(restored['saved_capture']['gpu_path_match'], 'matched_gpu_bus_topology_fingerprint')
            moved = copy.deepcopy(sensors); moved[1]['pci_bus_id'] = '0000:03:00.0'
            self.assertIsNone(bandwidth.saved_capture(machine,sensors=moved))
            self.assertIsNone(bandwidth.saved_capture(machine,sensors=[]))

    def test_topology_fingerprint_is_order_independent_normalized_and_nonidentifying(self):
        sensors = [{'index':1,'name':'GPU B','pci_bus_id':'00000000:41:00.0'},
                   {'index':0,'name':'GPU A','pci_bus_id':'00000000:01:00.0'}]
        compact = [dict(item,pci_bus_id=item['pci_bus_id'][4:]) for item in reversed(sensors)]
        fingerprint = bandwidth._gpu_path_fingerprint(sensors)
        self.assertRegex(fingerprint,r'^[0-9a-f]{64}$')
        self.assertEqual(fingerprint, bandwidth._gpu_path_fingerprint(compact))
        self.assertIsNone(bandwidth._gpu_path_fingerprint([sensors[0],dict(sensors[1],pci_bus_id=None)]))
        with patch.object(bandwidth.power,'list_gpus',return_value=sensors), \
             patch.object(bandwidth,'cuda_probe',return_value=bandwidth._unavailable('fixture')), \
             patch.object(bandwidth,'memory_probe',return_value=bandwidth._unavailable('fixture')), \
             patch.object(bandwidth,'storage_probe',return_value=bandwidth._unavailable('fixture')):
            profile = bandwidth._worker('ignored',0)
        self.assertEqual(profile['gpu_path_fingerprint'],fingerprint)
        text = json.dumps(profile)
        self.assertNotIn('00000000:',text)
        self.assertNotIn('41:00.0',text)

    def test_fresh_unified_memory_capture_keeps_unknown_clocks_without_inventing_them(self):
        machine, _ = self.matching_capture_fixture()
        machine['memory']['configured_clock_mhz_reported'] = []
        machine['gpus'] = [{'name':'Apple GPU','memory_bytes':16*1024**3,'driver':None,
                            'memory_architecture':'unified','sensor_index':0}]
        profile = {'ram_to_vram_devices':[]}
        self.assertIsNone(bandwidth._capture_hardware_key(machine), 'Historical restoration still requires reported clocks and driver')
        with patch.object(bandwidth, '_latest', profile), \
             patch.object(bandwidth, '_latest_hardware_key', bandwidth._capture_hardware_key(machine, require_clocks=False)):
            self.assertEqual(bandwidth.latest(machine), profile)

    def test_cuda_probe_maps_sensor_by_bus_and_releases_fake_context(self):
        import ctypes as ct
        from types import SimpleNamespace
        calls, allocations = [], {}
        def allocate(output, size):
            buffer = ct.create_string_buffer(size)
            address = ct.addressof(buffer); allocations[address] = buffer
            output._obj.value = address
            return 0
        def release(pointer):
            allocations.pop(pointer.value); return 0
        def by_bus(output, bus):
            calls.append(('bus',bus)); output._obj.value=7; return 0
        def create(output, flags, device):
            calls.append(('context',device.value)); output._obj.value=99; return 0
        def destroy(context): calls.append(('destroy',context.value)); return 0
        def get_info(free, total): free._obj.value=2**30; total._obj.value=2**34; return 0
        def name(buffer, length, device): ct.memmove(buffer,b'Fake CUDA GPU\0',14); return 0
        def copy_to(target, source, size): ct.memmove(target.value,source.value,size); return 0
        def copy_from(target, source, size): ct.memmove(target,source.value,size); return 0
        driver = SimpleNamespace(cuInit=lambda flags:0, cuDeviceGetByPCIBusId=by_bus, cuDeviceGetName=name,
            cuCtxCreate_v2=create, cuCtxDestroy_v2=destroy, cuMemGetInfo_v2=get_info,
            cuMemAllocHost_v2=allocate, cuMemFreeHost=release, cuMemAlloc_v2=allocate, cuMemFree_v2=release,
            cuMemcpyHtoD_v2=copy_to, cuMemcpyDtoH_v2=copy_from, cuCtxSynchronize=lambda:0)
        sensor = {'index':5,'name':'Fake CUDA GPU','vram_total_mb':16384,'pci_bus_id':'0000:41:00.0'}
        from wattever import hardware
        before, during = hardware._pcie(1,8,3,16), hardware._pcie(3,8,3,16)
        with patch.object(bandwidth.pu, 'IS_WINDOWS', True), \
             patch.object(ct, 'WinDLL', return_value=driver, create=True), \
             patch.object(bandwidth.pu, 'run', return_value=SimpleNamespace(returncode=0,stdout='1024')), \
             patch.object(bandwidth.power, 'list_gpus', return_value=[sensor]), \
             patch.object(hardware, '_nvidia_pcie', return_value=before), \
             patch.object(bandwidth, '_pcie_during_copies', return_value=during), \
             patch.object(bandwidth, 'MIB', 64):
            measured = bandwidth.cuda_probe(5)
        self.assertEqual(calls, [('bus',b'0000:41:00.0'),('context',7),('destroy',99)])
        self.assertEqual(allocations, {}, 'All fake allocations must be released')
        self.assertEqual(measured['gpu_sensor_index'], 5)
        self.assertEqual(measured['pcie_during']['width_current'], 8)
        self.assertEqual(measured['pcie_during']['generation_current'], 3)
        self.assertEqual(len(measured['samples']), 3)
        self.assertTrue(measured['verified_readback'])
        self.assertNotIn('0000:41', json.dumps(measured))

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

    def test_reboot_page_change_requires_matching_installed_hardware(self):
        machine, _ = self.matching_capture_fixture()
        machine.update(motherboard={'manufacturer':'Test','model':'Board'}, os={'name':'Windows','release':'10'})
        run={'classification':{'hardware_class':'old'},'hardware':copy.deepcopy(machine),
             'gpu':{'name':'RTX 5060 Ti','driver':'1','vram_total_mb':16000},'results':[{'decode_tps':42}],
             'sessions':[{'id':'original','hardware':copy.deepcopy(machine)}]}
        current={'classification':{'hardware_class':'new'},'hardware':copy.deepcopy(machine),'gpu':copy.deepcopy(run['gpu'])}
        current['hardware']['system']['memory_bytes']-=4096
        original=copy.deepcopy(run)
        bandwidth.backfill(run,current,{'captured_at':'2026-11-01T19:00:00Z'})
        self.assertEqual(run['hardware']['transfer_bandwidth']['hardware_match']['usable_ram_delta_bytes'],-4096)
        self.assertEqual(run['classification'],original['classification'])
        self.assertEqual(run['results'],original['results'])
        self.assertEqual(run['sessions'],original['sessions'])
        same_class=copy.deepcopy(current)
        same_class['classification']=copy.deepcopy(original['classification'])
        same_class['hardware']['memory']['configured_clock_mhz_reported']=[3200]
        with self.assertRaisesRegex(ValueError,'Hardware differs'):
            bandwidth.backfill(copy.deepcopy(original),same_class,{'captured_at':'2026-11-01T19:00:00Z'})
        for change in ('board','os','ram','clock','gpu','driver','cpu','large_delta','not_page_aligned','missing_installed_ram'):
            changed=copy.deepcopy(current)
            if change=='board':changed['hardware']['motherboard']['model']='Other'
            if change=='os':changed['hardware']['os']['release']='11'
            if change=='ram':changed['hardware']['memory']['installed_capacity_bytes']//=2
            if change=='clock':changed['hardware']['memory']['configured_clock_mhz_reported']=[3200]
            if change=='gpu':changed['hardware']['gpus'][0]['name']='RTX 5070 Ti'
            if change=='driver':changed['hardware']['gpus'][0]['driver']='2'
            if change=='cpu':changed['hardware']['cpu']['name']='Other'
            if change=='large_delta':changed['hardware']['system']['memory_bytes']-=1024**2
            if change=='not_page_aligned':changed['hardware']['system']['memory_bytes']+=1
            if change=='missing_installed_ram':del changed['hardware']['memory']['installed_capacity_bytes']
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'Hardware differs'):
                bandwidth.backfill(copy.deepcopy(original),changed,{'captured_at':'2026-11-01T19:00:00Z'})

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
