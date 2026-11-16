"""The observatory must work without starting an inference benchmark."""
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from wattever import server
from wattever.system_monitor import SystemMonitor


class SystemMonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = SystemMonitor()
        self.addCleanup(self.monitor.stop)
        self.sampler = MagicMock(source="test", error=None, interval_ms=1000)
        self.sampler.now.return_value = {"cpu_util_percent": 25, "gpu_w": None}
        self.sampler.scope_metadata.return_value = {"inventory_complete": False}
        factory = patch("wattever.system_monitor.power.PowerSampler", return_value=self.sampler)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        timer = patch("wattever.system_monitor.threading.Timer")
        timer.start()
        self.addCleanup(timer.stop)

    def test_viewers_share_sampler_and_idle_expiry_releases_it(self):
        first = self.monitor.snapshot()
        self.monitor.snapshot()
        self.factory.assert_called_once_with(interval_ms=1000)
        self.sampler.start.assert_called_once()
        self.assertEqual(first["telemetry"]["cpu_util_percent"], 25)
        self.assertIsNone(first["telemetry"]["gpu_w"])
        self.monitor._expire()
        self.sampler.stop.assert_not_called()
        self.monitor._last_view -= 16
        self.monitor._expire()
        self.sampler.stop.assert_called_once()
        self.monitor.snapshot()
        self.assertEqual(self.factory.call_count, 2)

    def test_failed_start_cleans_up_and_can_retry(self):
        self.sampler.start.side_effect = RuntimeError("sensor unavailable")
        with self.assertRaisesRegex(RuntimeError, "sensor unavailable"):
            self.monitor.snapshot()
        self.sampler.stop.assert_called_once()
        self.assertIsNone(self.monitor._sampler)
        self.sampler.start.side_effect = None
        self.assertIsNotNone(self.monitor.snapshot()["telemetry"])

    def test_http_observatory_does_not_start_benchmark_or_discover_models(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        try:
            with patch.object(server, "SYSTEM_MONITOR", self.monitor), \
                 patch.object(server.Handler, "env_payload", side_effect=AssertionError("endpoint discovery")), \
                 patch.object(server.BENCH, "start", side_effect=AssertionError("inference")):
                with urllib.request.urlopen("http://127.0.0.1:%d/api/system" % httpd.server_port) as response:
                    payload = json.load(response)
            self.assertEqual(payload["telemetry"]["cpu_util_percent"], 25)
            self.assertEqual(payload["source"], "test")
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
