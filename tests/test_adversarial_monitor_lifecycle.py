"""Exercise monitor races with deterministic timers and concurrent viewers."""
import concurrent.futures
import threading
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from wattever.system_monitor import SystemMonitor


class FakeTimer:
    instances = []

    def __init__(self, interval, function):
        self.interval, self.function = interval, function
        self.cancelled = False
        self.daemon = False
        self.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        # Model a callback already dispatched before cancel() was called.
        self.function()


class MonitorLifecycleRegression(unittest.TestCase):
    def setUp(self):
        FakeTimer.instances = []
        self.clock = 0.0
        self.sampler = MagicMock(source="none", error="GPU sensor unavailable", interval_ms=1000)
        self.sampler.now.return_value = {"gpu_w": None, "cpu_util_percent": 42}
        self.sampler.scope_metadata.return_value = {"inventory_complete": False}
        self.factory = patch("wattever.system_monitor.power.PowerSampler", return_value=self.sampler)
        self.mock_factory = self.factory.start()
        self.addCleanup(self.factory.stop)
        timer = patch("wattever.system_monitor.threading.Timer", FakeTimer)
        timer.start()
        self.addCleanup(timer.stop)
        monotonic = patch("wattever.system_monitor.time.monotonic", side_effect=lambda: self.clock)
        monotonic.start()
        self.addCleanup(monotonic.stop)
        self.monitor = SystemMonitor(idle_s=15)
        self.addCleanup(self.monitor.stop)

    def test_concurrent_viewers_share_sampler_even_without_gpu_power(self):
        gate = threading.Barrier(12)
        def view():
            gate.wait(timeout=3)
            return self.monitor.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: view(), range(12)))
        self.mock_factory.assert_called_once()
        self.sampler.start.assert_called_once()
        self.assertTrue(all(r["telemetry"]["gpu_w"] is None and r["error"] for r in results))
        self.assertEqual(len(FakeTimer.instances), 1)

    def test_recheck_schedules_only_remaining_idle_interval(self):
        self.monitor.snapshot()
        self.clock = 14
        self.monitor.snapshot()
        self.clock = 15
        FakeTimer.instances[-1].fire()
        self.assertEqual(FakeTimer.instances[-1].interval, 14)

    def test_callback_from_stopped_generation_does_not_schedule_a_second_timer(self):
        self.monitor.snapshot()
        stale = FakeTimer.instances[-1]
        self.monitor.stop()
        self.clock = 1
        self.monitor.snapshot()
        active = FakeTimer.instances[-1]
        stale.fire()
        self.assertIs(FakeTimer.instances[-1], active)


class SensorFailureRegression(unittest.TestCase):
    def test_unavailable_gpu_sensor_keeps_cpu_monitor_running(self):
        monitor = SystemMonitor(idle_s=5)
        self.addCleanup(monitor.stop)
        with patch("wattever.power.gpu_info", return_value={}), \
             patch("wattever.power.popen", side_effect=OSError("missing sensor")):
            monitor.snapshot()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                result = monitor.snapshot()
                if result["error"] and result["telemetry"] is not None:
                    break
                time.sleep(0.02)
            # macOS has no NVIDIA sampler to fail: it reports the missing wattage without starting one.
            expected = "power measurement unavailable" if sys.platform == "darwin" else "missing sensor"
            self.assertIn(expected, result["error"])
            self.assertEqual(result["source"], "none")
            self.assertIsNone(result["telemetry"]["gpu_w"])
            self.assertIn("cpu_util_percent", result["telemetry"])


if __name__ == "__main__":
    unittest.main()
