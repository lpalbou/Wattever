import unittest
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from wattever import hardware, power


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.info_patch = patch.object(power, 'gpu_info', return_value={})
        self.equipment_patch = patch.object(hardware, '_equipment', return_value=({}, {}, []))
        self.info_patch.start()
        self.equipment_patch.start()
        self.memory_patch = patch.object(hardware, '_memory_inventory', return_value={})
        self.pcie_patch = patch.object(hardware, '_nvidia_pcie', return_value=hardware._pcie(None, None))
        self.memory_patch.start()
        self.pcie_patch.start()
        self.addCleanup(self.info_patch.stop)
        self.addCleanup(self.equipment_patch.stop)
        self.addCleanup(self.memory_patch.stop)
        self.addCleanup(self.pcie_patch.stop)

    def test_pcie_ceiling_is_not_measured_bandwidth(self):
        link = hardware._pcie(3, 8, 5, 16, source='test')
        self.assertAlmostEqual(link['link_encoding_ceiling_bytes_per_second'] / 1e9, 7.8769230769)
        self.assertIsNone(link['measured_host_to_device_bytes_per_second'])
        self.assertEqual(link['generation_max_reported'], 5)
        self.assertIn('idle', link['note'])
        self.assertIsNone(hardware._pcie(6, 16)['link_encoding_ceiling_bytes_per_second'])
        self.assertIsNone(hardware._pcie('[N/A]', 8)['link_encoding_ceiling_bytes_per_second'])

    def test_nvidia_link_probe_bounded_and_private_fields_never_queried(self):
        self.pcie_patch.stop()
        with patch.object(hardware.pu, 'run', return_value=SimpleNamespace(returncode=0, stdout='1, 8, 3, 16\n')) as run:
            link = hardware._nvidia_pcie(0)
        self.assertEqual(link['generation_current'], 1)
        self.assertEqual(link['generation_max_reported'], 3)
        self.assertEqual(run.call_args.kwargs['timeout'], 5)
        command = ' '.join(run.call_args.args[0])
        for private in ('uuid', 'serial', 'pci.bus_id'):
            self.assertNotIn(private, command)
        with patch.object(hardware.pu, 'run', side_effect=subprocess.TimeoutExpired('nvidia-smi', 5)):
            self.assertIsNone(hardware._nvidia_pcie(0)['generation_current'])
        self.assertIsNone(hardware._nvidia_pcie(None)['generation_current'])

    def test_memory_allowlist_does_not_infer_channels_or_bandwidth(self):
        self.memory_patch.stop()
        raw=[{'Capacity': str(16 * 1024**3), 'Speed': 3200, 'ConfiguredClockSpeed': 2933,
              'SMBIOSMemoryType': 26, 'DataWidth': 64, 'TotalWidth': 72,
              'SerialNumber':'secret', 'DeviceLocator':'private-slot', 'BankLabel':'private-bank'}] * 2
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware.pu, 'IS_MAC', False), patch.object(hardware, '_json_command', return_value=raw) as command:
            memory=hardware._memory_inventory()
        self.assertEqual(memory['installed_capacity_bytes'], 32 * 1024**3)
        self.assertEqual(memory['module_count'], 2)
        self.assertEqual(memory['configured_clock_mhz_reported'], [2933])
        self.assertIsNone(memory['channel_count'])
        self.assertIsNone(memory['measured_bandwidth_gb_s'])
        self.assertNotIn('secret', json.dumps(memory))
        self.assertNotIn('private', json.dumps(memory))
        for forbidden in ('SerialNumber', 'DeviceLocator', 'BankLabel'):
            self.assertNotIn(forbidden, ' '.join(command.call_args.args[0]))

    def test_no_fabricated_arithmetic_peaks(self):
        caps=hardware._capabilities({'compute_cap':'12.0', 'power_limit_w': 180, 'uuid':'secret', 'name':'GPU'})
        self.assertEqual(caps['compute_capability_reported'], '12.0')
        self.assertEqual(caps['compute_peaks'], [])
        self.assertIsNone(caps['measured_memory_bandwidth_gb_s'])
        self.assertNotIn('secret', json.dumps(caps))
        self.assertIsNone(hardware._capabilities({'power_limit_w':float('inf')})['power_limit_w_reported'])

    def test_windows_storage_types_match_devices_without_publishing_identifiers(self):
        self.equipment_patch.stop()
        data = {'storage': [{'Index': 1, 'Model': 'SSD', 'InterfaceType': 'IDE'},
                            {'Index': 2, 'Model': 'Disk', 'InterfaceType': 'IDE'},
                            {'Index': 3, 'Model': 'Unknown', 'InterfaceType': 'SATA'}],
                'physical_storage': [{'DeviceId': '2', 'MediaType': 3, 'BusType': 11},
                                     {'DeviceId': '1', 'MediaType': 'SSD', 'BusType': 17, 'SerialNumber': 'private'}]}
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware, '_json_command', return_value=data):
            _, _, disks = hardware._equipment()
        self.assertEqual((disks[0]['media_type'], disks[0]['bus_type']), ('SSD', 'NVMe'))
        self.assertEqual((disks[1]['media_type'], disks[1]['bus_type']), ('HDD', 'SATA'))
        self.assertNotIn('media_type', disks[2])
        self.assertNotIn('private', json.dumps(disks))
        self.assertNotIn('DeviceId', json.dumps(disks))

    def test_inventory_explicitly_describes_client_not_unknown_remote_server(self):
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware, '_windows', return_value=({}, {}, [])), patch.object(power, 'list_gpus', return_value=[]):
            data=hardware.inventory()
        self.assertEqual(data['collection']['scope'], 'benchmark_client')
        self.assertFalse(data['collection']['inference_server_hardware_verified'])
        self.assertEqual(data['metadata_schema_version'], 1)

    def test_windows_equipment_selective_fields(self):
        self.equipment_patch.stop()
        data = {'board': {'Manufacturer': 'Maker', 'Product': 'Board', 'SerialNumber': 'private'},
                'firmware': {'Manufacturer': 'Vendor', 'SMBIOSBIOSVersion': '1'},
                'storage': [{'Model': 'Disk', 'Size': '4096', 'InterfaceType': 'SATA', 'SerialNumber': 'private'}]}
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware, '_json_command', return_value=data):
            board, firmware, storage = hardware._equipment()
        self.assertEqual(board, {'manufacturer': 'Maker', 'model': 'Board'})
        self.assertEqual(storage, [{'model': 'Disk', 'capacity_bytes': 4096, 'interface': 'SATA'}])
        self.assertNotIn('private', str((board, firmware, storage)))

    def test_duplicate_gpu_names_match_sensors_without_private_identifiers(self):
        cpu = {'name': 'CPU', 'physical_cores': 4, 'logical_cores': 8}
        system = {'manufacturer': 'Maker', 'model': 'Model', 'memory_bytes': 1024}
        gpus = [{'name': 'GPU', 'vendor': None, 'memory_bytes': None, 'driver': None},
                {'name': 'GPU', 'vendor': None, 'memory_bytes': None, 'driver': None},
                {'name': 'Other', 'vendor': None, 'memory_bytes': None, 'driver': None}]
        sensors = [{'index': 0, 'name': 'GPU', 'vram_total_mb': 8192, 'uuid': 'secret'},
                   {'index': 1, 'name': 'GPU', 'vram_total_mb': 16384, 'uuid': 'secret2'}]
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware, '_windows', return_value=(cpu, system, gpus)), patch.object(power, 'list_gpus', return_value=sensors):
            result = hardware.inventory()
        self.assertEqual([g['sensor_index'] for g in result['gpus']], [0, 1, None])
        self.assertEqual(result['gpus'][1]['memory_bytes'], 16384 * 1024 * 1024)
        self.assertNotIn('secret', str(result))

    def test_unmatched_sensor_is_retained(self):
        with patch.object(hardware.pu, 'IS_WINDOWS', True), patch.object(hardware, '_windows', return_value=({}, {}, [])), patch.object(power, 'list_gpus', return_value=[{'index': 2, 'name': 'NVIDIA', 'vram_total_mb': None}]):
            self.assertEqual(hardware.inventory()['gpus'][0]['sensor_index'], 2)

    def test_linux_cpu_delta_and_unknown_estimate(self):
        with patch.object(power, 'IS_WINDOWS', False), patch.object(power, 'IS_MAC', False), patch.object(power, 'IS_LINUX', True), patch.object(power, 'linux_cpu_times', side_effect=[(100, 200), (120, 300)]):
            meter = power._CpuMeter()
            self.assertIsNone(meter.sample())
            self.assertAlmostEqual(meter.sample(), 0.8)
        self.assertIsNone(power.PowerSampler()._system_w(50, None))

    def test_pci_bus_matches_different_names(self):
        gpu = {'name': 'AD106 [GeForce RTX]', 'vendor': 'NVIDIA', 'memory_bytes': None, 'driver': None, '_pci': '0000:01:00.0'}
        sensor = {'index': 0, 'name': 'NVIDIA GeForce RTX', 'vram_total_mb': 8192, 'pci_bus_id': '00000000:01:00.0'}
        with patch.object(hardware.pu, 'IS_WINDOWS', False), patch.object(hardware.pu, 'IS_MAC', False), patch.object(hardware.pu, 'IS_LINUX', True), patch.object(hardware, '_linux', return_value=({}, {}, [gpu])), patch.object(power, 'list_gpus', return_value=[sensor]), patch.object(power, 'gpu_info', return_value={'vendor': 'nvidia'}):
            result = hardware.inventory()['gpus']
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['sensor_index'], 0)
        self.assertNotIn('_pci', result[0])

    def test_invalid_mac_index_is_not_silently_redirected(self):
        with patch.object(power, 'IS_MAC', True), patch.object(power, '_nvidia_gpu_info', return_value={}), patch.object(power, '_mac_gpu_info') as mac:
            self.assertEqual(power.gpu_info(1), {})
            mac.assert_not_called()


if __name__ == '__main__':
    unittest.main()
