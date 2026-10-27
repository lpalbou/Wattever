import unittest
from unittest.mock import patch
from wattever import hardware, power


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.info_patch = patch.object(power, 'gpu_info', return_value={})
        self.equipment_patch = patch.object(hardware, '_equipment', return_value=({}, {}, []))
        self.info_patch.start()
        self.equipment_patch.start()
        self.addCleanup(self.info_patch.stop)
        self.addCleanup(self.equipment_patch.stop)

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
        with patch.object(power, 'IS_WINDOWS', False), patch.object(power, 'IS_LINUX', True), patch.object(power, 'linux_cpu_times', side_effect=[(100, 200), (120, 300)]):
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
