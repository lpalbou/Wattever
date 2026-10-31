"""Telemetry boundaries and retention, without spawning tools or touching inference."""
import ctypes
import io
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wattever import platform_util, power


class MemoryTests(unittest.TestCase):
    def test_linux_available_memory_boundary(self):
        with patch.multiple(platform_util, IS_WINDOWS=False, IS_LINUX=True, IS_MAC=False), \
             patch('builtins.open', return_value=io.StringIO('MemTotal: 1000 kB\nMemFree: 10 kB\nMemAvailable: 250 kB\n')):
            result = platform_util.system_memory()
        self.assertEqual(result['used_bytes'], 750 * 1024)
        self.assertEqual(result['percent'], 75)
        self.assertIn('MemAvailable', result['source'])

    def test_linux_missing_available_is_unknown_not_free_only(self):
        with patch.multiple(platform_util, IS_WINDOWS=False, IS_LINUX=True, IS_MAC=False), \
             patch('builtins.open', return_value=io.StringIO('MemTotal: 1000 kB\nMemFree: 10 kB\n')):
            self.assertIsNone(platform_util.system_memory())

    def test_windows_physical_memory_api(self):
        def fill(pointer):
            pointer._obj.total_phys = 4096
            pointer._obj.avail_phys = 1024
            return 1
        with patch.multiple(platform_util, IS_WINDOWS=True, IS_LINUX=False, IS_MAC=False), \
             patch.object(ctypes, 'windll', create=True) as dll:
            dll.kernel32.GlobalMemoryStatusEx.side_effect = fill
            result = platform_util.system_memory()
        self.assertEqual(result['percent'], 75)
        self.assertEqual(result['total_bytes'], 4096)

    def test_mac_uses_actual_cpu_ticks(self):
        with patch.multiple(power, IS_WINDOWS=False, IS_LINUX=False, IS_MAC=True), \
             patch.object(power, 'sysctl', return_value='8'), \
             patch.object(power, 'mac_cpu_times', side_effect=[(20, 100), (30, 150)]):
            meter = power._CpuMeter()
            self.assertIsNone(meter.sample())
            self.assertAlmostEqual(meter.sample(), .8)

    def test_mac_memory_available_is_explicit_estimate(self):
        def capacity(name, value, length, old, size):
            value._obj.value = 100 * 4096
            return 0
        def pagesize(host, value):
            value._obj.value = 4096
            return 0
        def statistics(host, flavor, values, count):
            values._obj[0], values._obj[2] = 10, 20
            return 0
        lib = SimpleNamespace(sysctlbyname=capacity, mach_host_self=lambda: 1,
                              host_page_size=pagesize, host_statistics=statistics)
        with patch.multiple(platform_util, IS_WINDOWS=False, IS_LINUX=False, IS_MAC=True), \
             patch.object(ctypes, 'CDLL', return_value=lib):
            result = platform_util.system_memory()
        self.assertEqual(result['percent'], 70)
        self.assertIn('estimate', result['source'])


class TelemetryTests(unittest.TestCase):
    def sampler(self):
        sampler = power.PowerSampler(interval_ms=1000)
        sampler._started_perf = 100
        sampler._ended_perf = 110
        sampler._vram_total_mb = 1000
        return sampler

    def test_live_values_preserve_raw_tuple_and_memory_units(self):
        sampler = self.sampler()
        row = (101, 80, 75, 500, 2400, 65, .4, None)
        sampler._append(row, {'used_bytes': 300, 'total_bytes': 400, 'percent': 75, 'source': 'test'})
        self.assertEqual(len(sampler.samples[-1]), 8)
        reading = sampler.now()
        self.assertEqual(reading['cpu_util_percent'], 40)
        self.assertEqual(reading['vram_percent'], 50)
        self.assertEqual(reading['system_memory_percent'], 75)
        self.assertIsNone(reading['cpu_temp_c'])
        self.assertEqual(reading['elapsed_s'], 1)
        self.assertIn('+00:00', reading['timestamp'])
        window = sampler.window(100, 102)
        self.assertEqual(window['system_memory_percent_max'], 75)
        self.assertEqual(window['vram_percent_avg'], 50)

    def test_ring_does_not_lose_whole_run_peaks_or_means(self):
        sampler = self.sampler()
        sampler._started_perf = 0
        sampler._ended_perf = 4000
        for index in range(4000):
            sampler._append((index, 999 if index == 0 else 1, 50, 500, 2000, 60, .5, None))
        history, summary = sampler.history(), sampler.summary()
        self.assertEqual(len(history['points']), 3600)
        self.assertEqual(history['retention']['dropped_points'], 400)
        self.assertEqual(summary['metrics']['gpu_w']['max'], 999)
        self.assertAlmostEqual(summary['metrics']['gpu_w']['mean'], (999 + 3999) / 4000)
        self.assertEqual(summary['start']['gpu_w'], 999)
        self.assertEqual(summary['metrics']['gpu_w']['count'], 4000)

    def test_large_gap_not_counted_as_time_coverage(self):
        sampler = self.sampler()
        for stamp in (100, 101, 109, 110):
            sampler._append((stamp, 100, 50, 500, 2000, 60, .5, None))
        summary = sampler.summary()
        self.assertEqual(summary['metrics']['gpu_w']['coverage_s'], 2)
        self.assertEqual(summary['metrics']['gpu_w']['coverage_fraction'], .2)
        self.assertEqual(summary['metrics']['gpu_w']['time_weighted_mean'], 100)

    def test_unavailable_sensors_remain_null_and_no_compute_claims(self):
        sampler = self.sampler()
        sampler._append((101, None, None, None, None, None, .5, None))
        self.assertIsNone(sampler.now()['temp_c'])
        self.assertIsNone(sampler.now()['vram_percent'])
        self.assertNotIn('gpu_w', sampler.summary()['metrics'])
        self.assertNotIn('throttled', sampler.summary())
        self.assertIn('do not establish thermal throttling', sampler.summary()['drift_note'])

    def test_cpu_and_ram_sample_without_gpu_backend(self):
        sampler = power.PowerSampler(interval_ms=50)
        finished = threading.Event()
        append = sampler._append
        def observe(*args):
            append(*args)
            finished.set()
        with patch.object(power, 'gpu_info', return_value={}), \
             patch.object(power, 'IS_MAC', True), \
             patch.object(power, '_CpuMeter') as meter, \
             patch.object(power, '_CpuTemperature') as temperature, \
             patch.object(power, 'system_memory', return_value={'used_bytes': 3, 'total_bytes': 4, 'percent': 75}), \
             patch.object(sampler, '_append', side_effect=observe):
            meter.return_value.sample.return_value = .25
            temperature.return_value.sample.return_value = None
            temperature.return_value.source = None
            sampler.start()
            self.assertTrue(finished.wait(timeout=2))
            sampler.stop()
        self.assertEqual(sampler.now()['cpu_util_percent'], 25)
        self.assertEqual(sampler.now()['system_memory_percent'], 75)
        self.assertIsNone(sampler.now()['gpu_w'])

    def test_cpu_temperature_requires_actual_supported_package_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'hwmon0'
            folder.mkdir()
            (folder / 'name').write_text('coretemp')
            (folder / 'temp1_label').write_text('Package id 0')
            (folder / 'temp1_input').write_text('63000')
            with patch.object(power, 'IS_LINUX', True), patch.object(power, 'Path', return_value=Path(directory)):
                sensor = power._CpuTemperature()
            self.assertEqual(sensor.sample(), 63)
            self.assertEqual(sensor.source, 'linux_hwmon:coretemp:Package id 0')
            self.assertNotIn(directory, sensor.source)
        with patch.object(power, 'IS_LINUX', False):
            self.assertIsNone(power._CpuTemperature().sample())


if __name__ == '__main__':
    unittest.main()
