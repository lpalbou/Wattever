import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wattever import hardware, memory_topology as mt


def device(handle, label, size=16384):
    raw = bytearray(18)
    raw[0:4] = bytes((17, 18, handle, 0))
    raw[12:14] = size.to_bytes(2, 'little')
    raw[16] = 1
    return bytes(raw) + label.encode() + b'\0private-serial\0\0'


def channel(handle, members):
    raw = bytearray((37, 7 + 3 * len(members), handle, 0, 3, 255, len(members)))
    for member in members:
        raw.extend((1, member, 0))
    return bytes(raw) + b'\0\0'


def snapshot():
    return {'gpu': {'name': 'RTX 5060 Ti', 'driver': '1', 'vram_total_mb': 16000},
            'hardware': {'cpu': {'name': 'CPU'}, 'motherboard': {'model': 'Board'},
                'memory': {'installed_capacity_bytes': 64 * 1024**3, 'module_count': 4,
                           'configured_clock_mhz_reported': [2133]}}}


class MemoryTopologyTests(unittest.TestCase):
    def test_explicit_firmware_slots_are_populated_channels_not_active_mode(self):
        data = b''.join(device(i, name) for i, name in enumerate(['DIMM_A1', 'DIMM_A2', 'DIMM_B1', 'DIMM_B2'], 1))
        value = mt.parse_smbios(data, 4)
        self.assertEqual(value['channel_count'], 2)
        self.assertIsNone(value['active_channel_count'])
        self.assertEqual(value['status'], 'inferred')
        self.assertEqual(value['source'], 'SMBIOS Type 17')
        self.assertNotIn('private', json.dumps(value))
        self.assertNotIn('DIMM_A1', json.dumps(value))

    def test_dimm_count_rank_and_unknown_labels_do_not_establish_channels(self):
        for label in ('DIMM0', 'BANK 0', 'Channel unknown', 'DIMM_1'):
            self.assertIsNone(mt.parse_smbios(device(1, label), 1)['channel_count'])
        self.assertIsNone(mt.parse_smbios(device(1, 'DIMM_A1'), 2)['channel_count'])
        for invalid in (b'', b'\x11\xff\0\0', b'\x11\x02\0\0', device(1, 'DIMM_A1')[:-1]):
            self.assertIsNone(mt.parse_smbios(invalid)['channel_count'])

    def test_type37_uses_populated_device_handles_not_maximum_supported(self):
        data = device(1, 'Slot1') + device(2, 'Slot2') + channel(3, [1, 2]) + channel(4, [])
        value = mt.parse_smbios(data, 2)
        self.assertEqual(value['channel_count'], 1)
        self.assertEqual(value['status'], 'reported')
        self.assertIsNone(value['active_channel_count'])
        self.assertEqual(value['source'], 'SMBIOS Type 37')

    def test_edac_counts_channels_per_controller_not_per_dimm(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            for index, (controller, location) in enumerate([(0, 'channel 0 slot 0'), (0, 'channel 0 slot 1'), (1, 'channel 0 slot 0')]):
                path = Path(root) / ('mc%d' % controller) / ('dimm%d' % index)
                path.mkdir(parents=True)
                (path / 'size').write_text('16384')
                (path / 'dimm_location').write_text(location)
                paths.append(str(path))
            with patch.object(mt.glob, 'glob', return_value=paths):
                self.assertEqual(mt._linux(3)['populated_channel_count'], 2)
                self.assertIsNone(mt._linux(4)['channel_count'])

    def test_builtin_probe_failure_has_no_external_fallback(self):
        with patch.object(mt.pu, 'IS_WINDOWS', True), patch.object(mt.ctypes, 'windll', create=True) as native:
            native.kernel32.GetSystemFirmwareTable.return_value = 0
            self.assertEqual(mt.capture(4)['status'], 'unavailable')

    def test_backfill_preserves_rows_and_sessions_and_marks_later_capture(self):
        current = snapshot()
        run = dict(copy.deepcopy(current), id='run', results=[{'status': 'ok'}], sessions=[{'id': 'original'}],
                   hardware_follow_ups=[{'kind': 'memory_topology', 'status': 'pending'}])
        original = copy.deepcopy(run)
        topology = mt.parse_smbios(b''.join(device(i, 'DIMM_%s1' % c) for i, c in enumerate('ABCD', 1)), 4)
        value = mt.backfill(run, current, topology)
        self.assertEqual(value['channel_count'], 4)
        self.assertFalse(value['historical_inference_conditions_verified'])
        self.assertEqual(value['provenance'], 'detected_later_on_matching_hardware')
        self.assertEqual(run['results'], original['results'])
        self.assertEqual(run['sessions'], original['sessions'])
        self.assertEqual(run['hardware_follow_ups'][0]['status'], 'completed')

    def test_hardware_changes_and_unavailable_detection_preserve_saved_metadata(self):
        for target, key, value in [('gpu', 'name', 'RTX 5070 Ti'), ('cpu', 'name', 'Other'),
                                    ('memory', 'configured_clock_mhz_reported', [3200]),
                                    ('memory', 'installed_capacity_bytes', 128 * 1024**3),
                                    ('motherboard', 'model', 'Other')]:
            run, current = snapshot(), snapshot()
            (current['gpu'] if target == 'gpu' else current['hardware'][target])[key] = value
            original = copy.deepcopy(run)
            with self.assertRaises(ValueError): mt.backfill(run, current, {'channel_count': 4})
            self.assertEqual(run, original)
        with self.assertRaises(ValueError): mt.backfill(snapshot(), snapshot(), mt.unavailable())

    def test_reconnect_reminder_only_for_present_gpu_and_pending_request(self):
        run = dict(snapshot(), id='run', hardware_follow_ups=[{'kind': 'memory_topology', 'status': 'pending'}])
        self.assertEqual(len(mt.pending([run], run['gpu'])), 1)
        self.assertEqual(mt.pending([run], {'name': 'RTX 5070 Ti'}), [])
        self.assertEqual(mt.pending([run], [{'name':'Other','index':0},dict(run['gpu'],index=1)])[0]['gpu_index'], 1)
        run['hardware_follow_ups'][0]['status'] = 'completed'
        self.assertEqual(mt.pending([run], run['gpu']), [])


if __name__ == '__main__': unittest.main()
