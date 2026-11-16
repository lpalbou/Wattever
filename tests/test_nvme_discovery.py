import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wattever import bandwidth


class NvmeDiscoveryTests(unittest.TestCase):
    def test_windows_discovers_drive_letter_and_flags_readonly(self):
        disks = [{'model': 'Fast disk', 'partitions': [
            {'letter': 'D', 'filesystem': 'NTFS', 'driveType': 'Fixed'},
            {'letter': 'E', 'filesystem': 'NTFS', 'driveType': 'Fixed', 'readonly': True},
            {'letter': '', 'filesystem': 'NTFS', 'driveType': 'Fixed'}]}]
        with patch.object(bandwidth, '_json_readonly', return_value=disks):
            result = bandwidth._nvme_discover_windows()
        self.assertEqual(result[0]['volumes'][0]['path'], 'D:\\')
        self.assertEqual(result[0]['volumes'][0]['volume'], 'D:')
        self.assertIsNone(result[0]['volumes'][0]['reason'])
        self.assertIn('read-only', result[0]['volumes'][1]['reason'])
        self.assertEqual(len(result[0]['volumes']), 2)

    def test_linux_only_maps_mounted_nvme_not_sata_or_layered_storage(self):
        data = {'blockdevices': [
            {'name': 'sda', 'type': 'disk', 'tran': 'sata', 'mountpoints': ['/'], 'fstype': 'ext4'},
            {'name': 'nvme0n1', 'type': 'disk', 'tran': 'nvme', 'model': 'Fast', 'children': [
                {'type': 'part', 'fstype': 'ext4', 'mountpoints': ['/mnt/private']},
                {'type': 'crypt', 'fstype': 'ext4', 'mountpoints': ['/mnt/ambiguous']}]},
            {'name': 'nvme1n1', 'type': 'disk', 'tran': 'nvme', 'children': []}]}
        with patch.object(bandwidth, '_json_readonly', return_value=data):
            found = bandwidth._nvme_discover_linux()
        self.assertEqual(len(found), 2)
        self.assertEqual([v['path'] for v in found[0]['volumes']], ['/mnt/private'])
        self.assertEqual(found[1]['volumes'], [])

    def test_primary_duplicate_unmounted_and_readonly_are_isolated(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second, ExitStack() as stack:
            disks = [{'model': 'Primary', 'volumes': [{'path': first, 'volume': 'D:', 'reason': None}]},
                     {'model': 'Unmapped', 'volumes': []},
                     {'model': 'Locked', 'volumes': [{'path': second, 'volume': 'E:', 'reason': 'Volume is read-only'}]}]
            with patch.object(bandwidth, 'discover_nvme', return_value=(disks, None)), \
                 patch.object(bandwidth, '_directory_issue', return_value=None), \
                 patch.object(bandwidth, '_volume_key', side_effect=lambda p: ('dev', os.path.normcase(str(Path(p).resolve())))):
                plan = bandwidth._storage_plan(first, [first], stack)
            self.assertEqual(plan['primary']['volume'], 'D:')
            self.assertEqual(len(plan['items']), 2)
            self.assertTrue(all(item['reason'] for item in plan['items']))
            self.assertNotIn(first, json.dumps(plan['discovery']))

    def test_macos_does_not_assign_an_unmapped_apfs_volume(self):
        replies = [
            {'AllDisksAndPartitions': [{'DeviceIdentifier': 'disk0', 'Partitions': [{'DeviceIdentifier': 'disk0s2'}]},
                                       {'DeviceIdentifier': 'disk3', 'Partitions': []}]},
            {'BusProtocol': 'NVMe', 'MediaName': 'Internal'},
            {'BusProtocol': 'NVMe', 'Internal': True, 'WritableVolume': False},
            {'BusProtocol': 'Virtual Interface'}]
        responses = [SimpleNamespace(returncode=0, stdout=plistlib.dumps(item).decode()) for item in replies]
        with patch.object(bandwidth.pu, 'run', side_effect=responses):
            found = bandwidth._nvme_discover_mac()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['volumes'], [])

    def test_failed_volume_does_not_abort_other_transfers_or_leak_path(self):
        plan = {'primary': None, 'discovery': {'nvme_detected': 2, 'status': 'ok'}, 'items': [
            {'path': 'private', 'metadata': {'volume': 'D:', 'bus_type': 'NVMe', 'device_model': 'A'}, 'reason': None},
            {'path': None, 'metadata': {'volume': 'NVMe volume', 'bus_type': 'NVMe', 'device_model': 'B'}, 'reason': 'No safely mapped volume'}]}
        with patch.object(bandwidth, 'storage_probe', return_value=bandwidth._unavailable('fixture')), \
             patch.object(bandwidth, 'memory_probe', return_value=bandwidth._unavailable('fixture')), \
             patch.object(bandwidth, 'cuda_probe', return_value=bandwidth._unavailable('fixture')), \
             patch.object(bandwidth.power, 'list_gpus', return_value=[]), \
             patch.object(bandwidth.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 10)):
            profile = bandwidth._worker('scratch', 0, plan)
        self.assertEqual(len(profile['storage_volumes']), 2)
        self.assertTrue(all(item['status'] == 'unavailable' for item in profile['storage_volumes']))
        self.assertNotIn('private', json.dumps(profile))
        self.assertEqual(profile['storage_discovery']['nvme_measured'], 0)

    def test_legacy_coverage_is_incomplete_and_duplicate_models_not_identity(self):
        machine = {'storage': [{'bus_type': 'NVMe', 'model': 'same'}, {'bus_type': 'NVMe', 'model': 'same'}]}
        self.assertEqual(bandwidth.storage_coverage(machine, {'storage_to_ram': {'status': 'ok'}})['status'], 'incomplete')
        profile = {'storage_discovery': {'nvme_measured': 2}}
        self.assertEqual(bandwidth.storage_coverage(machine, profile)['status'], 'unverified')


if __name__ == '__main__':
    unittest.main()
