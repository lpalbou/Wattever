"""Exercise always-on monitor races: concurrent viewers, restarts and missing sensors."""
import concurrent.futures
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from wattever.system_monitor import SystemMonitor


class MonitorLifecycleRegression(unittest.TestCase):
    def setUp(self):
        self.sampler = MagicMock(source="none", error="GPU sensor unavailable", interval_ms=1000)
        self.sampler.now.side_effect = lambda: {"gpu_w": None, "cpu_util_percent": 42, "sample_age_s": 0.0}
        self.sampler.scope_metadata.return_value = {"inventory_complete": False}
        self.factory = patch("wattever.system_monitor.power.PowerSampler", return_value=self.sampler)
        self.mock_factory = self.factory.start()
        self.addCleanup(self.factory.stop)
        self.monitor = SystemMonitor(poll_s=0.01)
        self.addCleanup(self.monitor.stop)

    def test_concurrent_viewers_share_one_sampler_even_without_gpu_power(self):
        gate = threading.Barrier(12)
        def view():
            gate.wait(timeout=3)
            return self.monitor.snapshot()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: view(), range(12)))
        self.mock_factory.assert_called_once()
        self.sampler.start.assert_called_once()
        self.assertTrue(all(r["telemetry"]["gpu_w"] is None and r["error"] for r in results))

    def test_sensors_are_not_released_after_viewers_leave(self):
        self.monitor.snapshot()
        with patch("wattever.system_monitor.time.monotonic", return_value=time.monotonic() + 3600):
            for _ in range(5):
                self.monitor.tick()
        self.sampler.stop.assert_not_called()
        self.mock_factory.assert_called_once()

    def test_stop_then_view_restarts_cleanly_and_stop_is_idempotent(self):
        self.monitor.snapshot()
        self.monitor.stop()
        self.monitor.stop()
        self.sampler.stop.assert_called_once()
        self.assertIsNone(self.monitor._thread)
        self.monitor.snapshot()
        self.assertEqual(self.mock_factory.call_count, 2)
        self.assertIsNotNone(self.monitor._thread)

    def test_final_stop_at_shutdown_refuses_a_late_viewer_restart(self):
        self.monitor.snapshot()
        self.monitor.stop(final=True)
        late = self.monitor.snapshot()
        self.assertIsNone(late["telemetry"])
        self.assertIn("shutting down", late["error"])
        self.mock_factory.assert_called_once()
        self.assertIsNone(self.monitor._thread)

    def test_recording_thread_survives_a_bad_reading(self):
        calls = {"n": 0}
        def now():
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("bad reading")
            return {"cpu_util_percent": 1, "sample_age_s": 0.0}
        self.sampler.now.side_effect = now
        self.monitor.interval_ms = 20
        self.monitor.start()
        deadline = time.monotonic() + 3
        while calls["n"] < 6 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertGreaterEqual(calls["n"], 6)
        self.assertTrue(self.monitor._thread.is_alive())


class SensorFailureRegression(unittest.TestCase):
    def test_unavailable_gpu_sensor_keeps_cpu_monitor_running(self):
        monitor = SystemMonitor()
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
            deadline = time.monotonic() + 3
            while not monitor.log.query()["points"] and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(monitor.log.query()["points"], "CPU/RAM history is recorded without GPU sensors")


if __name__ == "__main__":
    unittest.main()
