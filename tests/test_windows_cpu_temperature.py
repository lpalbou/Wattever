"""Windows CPU temperature sources, with fake buffers and readers only (no drivers, no tools)."""
import struct
import unittest
from unittest.mock import patch

from wattever import power
from wattever import windows_cpu_temperature as w


def hwinfo_buffer(readings, signature=w.HWINFO_SIGNATURE, poll_time=1000, polling_ms=2000):
    size, offset = 316, 48
    header = w.HWINFO_HEADER.pack(signature, 2, 2, poll_time, 48, 264, 0, offset, size, len(readings), polling_ms)
    body = b''
    for kind, label, unit, value in readings:
        element = struct.pack('<III', kind, 0, 0) + label.encode('latin-1').ljust(128, b'\0') + b'\0' * 128
        element += unit.encode('latin-1').ljust(16, b'\0') + struct.pack('<dddd', value, value, value, value)
        body += element.ljust(size, b'\0')
    return header + body


def coretemp_buffer(temps, tjmax=100, delta=False, fahrenheit=False):
    buffer = bytearray(w.CORETEMP_SIZE)
    struct.pack_into('<I', buffer, 1024, tjmax)
    struct.pack_into('<II', buffer, 1536, len(temps), 1)
    struct.pack_into('<%df' % len(temps), buffer, 1544, *temps)
    buffer[2684], buffer[2685] = int(fahrenheit), int(delta)
    return bytes(buffer)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Fixed:
    def __init__(self, result=None, error=None, retry_s=5.0):
        self.result, self.error, self.retry_s, self.calls = result, error, retry_s, 0

    def read(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class ParserTests(unittest.TestCase):
    def test_hwinfo_package_reading_preferred_and_freshness_checked(self):
        buffer = hwinfo_buffer([(1, 'Core #0', '°C', 70), (2, 'CPU Package', 'V', 1.1),
                                (1, 'CPU Package', '°C', 61.5), (1, 'GPU Temperature', '°C', 50)])
        self.assertEqual(w.parse_hwinfo(buffer, now=1001), (61.5, 'CPU Package'))
        self.assertIsNone(w.parse_hwinfo(buffer, now=1100), 'A mapping HWiNFO no longer updates is not current')
        self.assertIsNone(w.parse_hwinfo(hwinfo_buffer([(1, 'CPU Package', '°C', 61)], signature=0x44414544), now=1000))
        self.assertIsNone(w.parse_hwinfo(hwinfo_buffer([(1, 'Core #0', '°C', 70)]), now=1000), 'A core is not the package')
        self.assertIsNone(w.parse_hwinfo(b'short'))
        fahrenheit = w.parse_hwinfo(hwinfo_buffer([(1, 'CPU Package', '°F', 140)]), now=1000)
        self.assertAlmostEqual(fahrenheit[0], 60)
        self.assertIsNone(w.parse_hwinfo(hwinfo_buffer([(1, 'CPU Package', '°C', 900)]), now=1000))

    def test_coretemp_is_hottest_core_including_delta_mode(self):
        self.assertEqual(w.parse_coretemp(coretemp_buffer([40, 55.5, 47])), (55.5, 'hottest_core'))
        self.assertEqual(w.parse_coretemp(coretemp_buffer([60, 30], tjmax=100, delta=True)), (70, 'hottest_core'))
        self.assertIsNone(w.parse_coretemp(coretemp_buffer([])))
        self.assertIsNone(w.parse_coretemp(b'\0' * 10))

    def test_lhm_tree_needs_cpu_package_under_a_cpu(self):
        tree = {'Text': 'Sensor', 'Children': [{'Text': 'PC', 'Children': [
            {'Text': 'NVIDIA', 'ImageURL': 'images_icon/nvidia.png', 'Children': [{'Text': 'CPU Package', 'Value': '99.0 °C'}]},
            {'Text': 'Intel Core i7', 'ImageURL': 'images_icon/cpu.png', 'Children': [{'Text': 'Temperatures', 'Children': [
                {'Text': 'CPU Core #1', 'Value': '70.0 °C'}, {'Text': 'CPU Package', 'Value': '58,5 °C'}]}]}]}]}
        self.assertEqual(w.parse_lhm(tree), (58.5, 'CPU Package'))
        self.assertIsNone(w.parse_lhm({'Text': 'CPU Package', 'Value': '50 °C'}), 'Not under a CPU node')
        self.assertIsNone(w.parse_lhm([]))

    def test_constant_acpi_zone_is_withheld_until_it_moves(self):
        zones = w.AcpiZoneFilter()
        self.assertIsNone(zones.choose({'\\_TZ.TZ00': 27.85}))
        self.assertIsNone(zones.choose({'\\_TZ.TZ00': 27.85}), 'A fixed placeholder is not a sensor')
        self.assertEqual(zones.choose({'\\_TZ.TZ00': 31.0}), (31.0, '\\_TZ.TZ00'))
        both = w.AcpiZoneFilter()
        both.choose({'\\_TZ.CPUZ': 40, '\\_TZ.SYS': 50})
        self.assertEqual(both.choose({'\\_TZ.CPUZ': 45, '\\_TZ.SYS': 55}), (45, '\\_TZ.CPUZ'), 'A CPU-named zone wins')
        reader = w.AcpiThermalZoneReader(zones=type('Z', (), {'read': lambda self: {}})())
        self.assertIsNone(reader.read())
        self.assertEqual(reader.retry_s, 60.0, 'No zones: check rarely')


class PollerTests(unittest.TestCase):
    def test_best_source_wins_and_failures_fall_through(self):
        clock = Clock()
        broken, hwinfo, acpi = Fixed(error=OSError('gone')), Fixed((62.0, 'windows_hwinfo_shared_memory:CPU Package')), Fixed((40.0, 'windows_acpi_thermal_zone:TZ'))
        sensor = w.WindowsCpuTemperature([broken, hwinfo, acpi], clock=clock, background=False)
        self.assertEqual(sensor.refresh()[1], 'windows_hwinfo_shared_memory:CPU Package')
        self.assertEqual(acpi.calls, 0)
        self.assertEqual(sensor.sample(), 62.0)
        self.assertEqual(sensor.source, 'windows_hwinfo_shared_memory:CPU Package')
        self.assertIsNone(sensor.note)
        sensor.refresh()
        self.assertEqual(broken.calls, 1, 'A failing source backs off')

    def test_stale_and_missing_readings_are_unavailable_with_reason(self):
        clock = Clock()
        source = Fixed((50.0, 'x'))
        sensor = w.WindowsCpuTemperature([source], clock=clock, background=False)
        sensor.refresh()
        clock.now += w.FRESH_S + 1
        self.assertIsNone(sensor.sample())
        self.assertIsNone(sensor.source)
        self.assertIn('stale', sensor.note)
        source.result = None
        clock.now += 10
        sensor.refresh()
        self.assertIsNone(sensor.sample())
        self.assertEqual(sensor.note, w.UNAVAILABLE_NOTE)
        source.result = (500.0, 'x')
        clock.now += 10
        self.assertIsNone(sensor.refresh(), 'Out-of-range values are rejected')

    def test_sample_only_reads_cache_and_poller_stops(self):
        class Slow(Fixed):
            def read(self):
                import time
                time.sleep(0.3)
                return (45.0, 'slow')
        sensor = w.WindowsCpuTemperature([Slow()])
        import time
        started = time.perf_counter()
        self.assertIsNone(sensor.sample())
        self.assertLess(time.perf_counter() - started, 0.1, 'sample() must not wait for a source')
        for _ in range(40):
            if sensor.sample() is not None:
                break
            time.sleep(0.05)
        self.assertEqual(sensor.sample(), 45.0)
        sensor.close()
        sensor._thread.join(timeout=3)
        self.assertFalse(sensor._thread.is_alive())


class IntegrationTests(unittest.TestCase):
    def test_windows_branch_reports_source_and_note_into_points(self):
        class Fake:
            def __init__(self):
                self.source, self.note, self.closed = None, w.UNAVAILABLE_NOTE, False

            def sample(self):
                return None

            def close(self):
                self.closed = True

        with patch.object(power, 'IS_WINDOWS', True), patch.object(power, 'IS_LINUX', False), \
             patch.object(w, 'WindowsCpuTemperature', Fake):
            sensor = power._CpuTemperature()
        self.assertIsNone(sensor.sample())
        self.assertEqual(sensor.note, w.UNAVAILABLE_NOTE)
        sensor.close()
        self.assertTrue(sensor._windows.closed)
        sampler = power.PowerSampler(interval_ms=1000)
        sampler._started_perf = 100
        sampler._append((101, None, None, None, None, None, .5, None), {}, None, None, None, sensor.note)
        self.assertEqual(sampler.now()['cpu_temp_note'], w.UNAVAILABLE_NOTE)
        sampler._append((102, None, None, None, None, None, .5, None), {}, 55.0, 'windows_hwinfo_shared_memory:CPU Package', None, 'ignored')
        point = sampler.now()
        self.assertEqual((point['cpu_temp_c'], point['cpu_temp_source'], point['cpu_temp_note']),
                         (55.0, 'windows_hwinfo_shared_memory:CPU Package', None))

    def test_sensor_errors_never_reach_the_sampler(self):
        class Broken:
            source = note = None

            def sample(self):
                raise RuntimeError('boom')

        with patch.object(power, 'IS_WINDOWS', True), patch.object(power, 'IS_LINUX', False), \
             patch.object(w, 'WindowsCpuTemperature', Broken):
            self.assertIsNone(power._CpuTemperature().sample())


if __name__ == '__main__':
    unittest.main()
