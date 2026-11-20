"""Optional Windows CPU sensor setup, with fake downloads and a fake UAC prompt only.

No driver is installed, no elevation is requested and nothing is downloaded.
"""
import io
import json
import os
import re
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from wattever import cpu_sensor_setup as setup
from wattever import windows_cpu_temperature as w

SCRIPTS = setup.SCRIPTS


def read_script(name):
    with open(os.path.join(SCRIPTS, name), encoding='utf-8') as fh:
        return fh.read()


def fake_payloads(intel=b'intel-module', amd=b'amd-module', installer=b'installer'):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('IntelMSR.bin', intel)
        z.writestr('AMDFamily17.bin', amd)
        z.writestr('COPYING', b'license')
    return installer, archive.getvalue()


class FakeEnvironment:
    """Pins: fake payloads with their own hashes, so the real verification code runs."""

    def __init__(self, **payloads):
        self.installer, self.archive = fake_payloads(**payloads)
        self.patches = [
            mock.patch.dict(setup.PAWNIO_SETUP, sha256=setup.sha256(self.installer)),
            mock.patch.dict(setup.PAWNIO_MODULES, sha256=setup.sha256(self.archive)),
            mock.patch.dict(setup.MODULES, {'IntelMSR.bin': setup.sha256(b'intel-module'),
                                            'AMDFamily17.bin': setup.sha256(b'amd-module')}),
        ]

    def fetch(self, spec):
        data = self.installer if spec is setup.PAWNIO_SETUP else self.archive
        return setup.download(spec, opener=FakeOpener(data))

    def __enter__(self):
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class FakeOpener:
    def __init__(self, data):
        self.data, self.requests = data, []

    def open(self, request, timeout=None):
        self.requests.append(request.full_url)
        return FakeResponse(self.data)


class PinnedReleases(unittest.TestCase):
    def test_hashes_in_python_and_elevated_script_agree(self):
        script = read_script('setup.ps1')
        pinned = dict(re.findall(r"'([A-Za-z0-9_.]+)'\s*=\s*'([0-9a-f]{64})'", script))
        self.assertEqual(pinned, {'PawnIO_setup.exe': setup.PAWNIO_SETUP['sha256'], **setup.MODULES})

    def test_downloads_are_pinned_release_assets_over_https(self):
        for spec in (setup.PAWNIO_SETUP, setup.PAWNIO_MODULES):
            self.assertRegex(spec['url'], r'^https://github\.com/namazso/PawnIO\.[A-Za-z]+/releases/download/[0-9.]+/')
            self.assertNotIn('latest', spec['url'])

    def test_download_rejects_a_changed_file_and_an_oversized_one(self):
        spec = dict(url='https://example.invalid/a', sha256=setup.sha256(b'good'), max_bytes=10)
        self.assertEqual(setup.download(spec, FakeOpener(b'good')), b'good')
        with self.assertRaisesRegex(RuntimeError, 'verification'):
            setup.download(spec, FakeOpener(b'evil'))
        with self.assertRaisesRegex(RuntimeError, 'larger'):
            setup.download(spec, FakeOpener(b'x' * 11))


class SensorScripts(unittest.TestCase):
    def test_sensor_only_reads_thermal_registers(self):
        sensor = read_script('cpu-sensor.ps1')
        self.assertNotIn('ioctl_write', sensor)
        calls = set(re.findall(r'Call\("(\w+)", (0x[0-9A-Fa-f]+)\)', sensor))
        self.assertEqual(calls, {('ioctl_read_msr', '0x1A2'), ('ioctl_read_msr', '0x1B1'), ('ioctl_read_smn', '0x00059800')})

    def test_system_task_runs_only_files_from_program_files(self):
        script = read_script('setup.ps1')
        self.assertIn("$Install = Join-Path $env:ProgramFiles 'Wattever\\CpuSensor'", script)
        self.assertIn('$script = Join-Path $Install', script)
        self.assertIn("-UserId 'S-1-5-18'", script)
        # Copies are verified after they reach the admin-only folder, before anything runs.
        self.assertLess(script.index('Copy-Item'), script.index('Get-FileHash'))
        self.assertLess(script.index('Get-FileHash'), script.index("'PawnIO_setup.exe') -ArgumentList"))
        # Users may read the readings folder but never write it.
        self.assertIn("'*S-1-5-32-545:(OI)(CI)RX'", script)
        self.assertIn('/inheritance:r', script)

    def test_uninstall_removes_pawnio_only_when_wattever_installed_it(self):
        script = read_script('setup.ps1')
        self.assertIn('$removeDriver = Test-Path -LiteralPath $Marker', script)
        self.assertIn('if (-not $version) { Set-Content -LiteralPath $Marker', script)


class Staging(unittest.TestCase):
    def test_stage_writes_verified_files_and_the_sensor_hash(self):
        with FakeEnvironment() as env, tempfile.TemporaryDirectory() as folder:
            setup.stage(folder, env.fetch)
            self.assertEqual(sorted(os.listdir(folder)), ['AMDFamily17.bin', 'IntelMSR.bin', 'PawnIO_setup.exe',
                                                          'cpu-sensor.ps1', 'cpu-sensor.ps1.sha256', 'setup.ps1'])
            with open(os.path.join(folder, 'cpu-sensor.ps1'), 'rb') as fh:
                sensor = fh.read()
            with open(os.path.join(folder, 'cpu-sensor.ps1.sha256')) as fh:
                self.assertEqual(fh.read(), setup.sha256(sensor))

    def test_stage_rejects_a_module_that_does_not_match(self):
        with FakeEnvironment(intel=b'tampered') as env, tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, 'IntelMSR.bin'):
                setup.stage(folder, env.fetch)

    def test_arguments_pass_the_source_folder_only_for_install(self):
        install = setup.setup_arguments('install', r'C:\T', r'C:\T\setup.log')
        self.assertEqual(install[-2:], ['-Source', r'C:\T'])
        self.assertNotIn('-Source', setup.setup_arguments('uninstall', r'C:\T', r'C:\T\setup.log'))
        self.assertIn('Bypass', install)


class SetupFlow(unittest.TestCase):
    def make(self, elevate, env):
        return setup.CpuSensorSetup(platform='win32', fetch=env.fetch, elevate=elevate,
                                    probe_pawnio=lambda: '2.2.0', probe_task=lambda: True)

    def run_to_end(self, runner, action='install'):
        runner.start(action)
        runner.thread.join(10)
        return runner.status()

    def test_success_reports_the_elevated_log(self):
        seen = {}

        def elevate(executable, args):
            seen['args'] = args
            log = args[args.index('-Log') + 1]
            self.assertTrue(os.path.exists(os.path.join(args[args.index('-Source') + 1], 'IntelMSR.bin')))
            with open(log, 'w', encoding='utf-8') as fh:
                fh.write('All files verified in the Program Files folder.\nFirst reading: {"celsius":41}\n')
            return 0

        with FakeEnvironment() as env:
            status = self.run_to_end(self.make(elevate, env))
        self.assertEqual(status['phase'], 'done')
        self.assertIn('First reading: {"celsius":41}', status['log'])
        self.assertTrue(status['installed'])
        self.assertFalse(os.path.exists(seen['args'][seen['args'].index('-Source') + 1]), 'The staging folder is deleted')

    def test_declined_uac_changes_nothing(self):
        with FakeEnvironment() as env:
            status = self.run_to_end(self.make(lambda exe, args: setup.ERROR_CANCELLED, env))
        self.assertEqual(status['phase'], 'cancelled')
        self.assertIn('nothing was changed', status['message'])

    def test_failure_shows_the_scripts_error(self):
        def elevate(executable, args):
            with open(args[args.index('-Log') + 1], 'w', encoding='utf-8') as fh:
                fh.write('ERROR: The PawnIO installer failed with exit code 5.\n')
            return 1

        with FakeEnvironment() as env:
            status = self.run_to_end(self.make(elevate, env))
        self.assertEqual(status['phase'], 'failed')
        self.assertEqual(status['message'], 'The PawnIO installer failed with exit code 5.')

    def test_a_bad_download_never_reaches_elevation(self):
        elevate = mock.Mock(return_value=0)
        with FakeEnvironment() as env:
            runner = self.make(elevate, env)
            runner.fetch = lambda spec: (_ for _ in ()).throw(RuntimeError('Download failed verification'))
            status = self.run_to_end(runner)
        self.assertEqual(status['phase'], 'failed')
        elevate.assert_not_called()

    def test_other_platforms_offer_nothing(self):
        runner = setup.CpuSensorSetup(platform='darwin')
        self.assertFalse(runner.status()['supported'])
        with self.assertRaises(ValueError):
            runner.start('install')

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            setup.CpuSensorSetup(platform='win32').start('format-disk')


class SensorFile(unittest.TestCase):
    def doc(self, **fields):
        base = {'v': 1, 'at': 1000.0, 'celsius': 47.5, 'sensor': 'intel_package', 'error': None}
        base.update(fields)
        return json.dumps(base)

    def test_fresh_reading(self):
        self.assertEqual(w.parse_wattever_sensor(self.doc(), now=1001.0), (47.5, 'intel_package'))

    def test_stale_future_and_foreign_readings_are_ignored(self):
        self.assertIsNone(w.parse_wattever_sensor(self.doc(), now=1000.0 + w.FRESH_S + 1))
        self.assertIsNone(w.parse_wattever_sensor(self.doc(at=1010.0), now=1000.0))
        self.assertIsNone(w.parse_wattever_sensor(self.doc(v=2), now=1001.0))
        self.assertIsNone(w.parse_wattever_sensor(self.doc(sensor='<script>'), now=1001.0))
        self.assertIsNone(w.parse_wattever_sensor(self.doc(celsius=900), now=1001.0))

    def test_reported_error_reaches_the_note(self):
        text = self.doc(celsius=None, error='PawnIO is not installed.', at=time.time())
        monitor = w.WindowsCpuTemperature(readers=[w.WatteverSensorReader(load=lambda: text)], background=False)
        monitor.refresh()
        self.assertIsNone(monitor.sample())
        self.assertEqual(monitor.note, 'The Wattever CPU sensor reports: PawnIO is not installed.')

    def test_reader_source_and_priority(self):
        text = self.doc(at=time.time())
        self.assertEqual(w.WatteverSensorReader(load=lambda: text).read()[1], 'windows_wattever_pawnio:intel_package')
        self.assertIsInstance(w.default_readers()[0], w.WatteverSensorReader)

    def test_half_written_file_is_skipped(self):
        self.assertIsNone(w.WatteverSensorReader(load=lambda: '{"v": 1, "at"').read())

    def test_unavailable_note_offers_the_one_click_setup(self):
        self.assertIn('Enable CPU temperature', w.UNAVAILABLE_NOTE)


if __name__ == '__main__':
    unittest.main()
