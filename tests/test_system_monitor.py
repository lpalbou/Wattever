"""The observatory records continuously, without viewers and without an inference benchmark."""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from wattever import power, server
from wattever.system_monitor import SystemMonitor
from wattever.telemetry_log import TelemetryLog


def fake_sampler(**point):
    sampler = MagicMock(source="test", error=None, interval_ms=1000)
    sampler.now.side_effect = lambda: dict({"cpu_util_percent": 25, "gpu_w": None, "sample_age_s": 0.0}, **point)
    sampler.scope_metadata.return_value = {"inventory_complete": False}
    return sampler


class SystemMonitorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="wattever-telemetry-test-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.sampler = fake_sampler()
        factory = patch("wattever.system_monitor.power.PowerSampler", return_value=self.sampler)
        self.factory = factory.start()
        self.addCleanup(factory.stop)

    def monitor(self, **kwargs):
        monitor = SystemMonitor(**kwargs)
        self.addCleanup(monitor.stop)
        return monitor

    def test_capture_continues_with_no_viewers_and_is_never_idle_expired(self):
        monitor = self.monitor(interval_ms=50, poll_s=0.01)
        monitor.start(self.directory)
        self.factory.assert_called_once_with(interval_ms=50)
        time.sleep(0.6)  # nobody calls snapshot() or history()
        self.sampler.stop.assert_not_called()
        points = monitor.log.query(time.time() - 60, time.time(), 4000)["points"]
        self.assertGreaterEqual(len(points), 4, "readings are recorded while nobody watches")
        self.assertTrue(all(b["t"] - a["t"] >= 0.04 for a, b in zip(points, points[1:])), "about one record per interval")
        self.assertEqual(points[-1]["cpu_util_percent"], 25)
        self.assertNotIn("gpu_w", points[-1], "a missing reading is not stored as zero")
        monitor.stop()
        self.sampler.stop.assert_called_once()
        files = os.listdir(self.directory)
        self.assertTrue(any(name.endswith(".jsonl") for name in files), files)

    def test_stale_and_repeated_readings_are_not_recorded(self):
        monitor = self.monitor()
        monitor.start()
        self.assertTrue(monitor.tick())
        self.assertFalse(monitor.tick(), "the same reading polled again is not a new sample")
        monitor._last_t = None
        self.sampler.now.side_effect = lambda: {"cpu_util_percent": 1, "sample_age_s": 9}
        self.assertFalse(monitor.tick(), "a stale reading is a gap, not a value")

    def test_failed_start_is_reported_without_raising_and_retried(self):
        self.sampler.start.side_effect = RuntimeError("sensor unavailable")
        monitor = self.monitor()
        snapshot = monitor.snapshot()
        self.assertIsNone(snapshot["telemetry"])
        self.assertIn("sensor unavailable", snapshot["error"])
        self.sampler.stop.assert_called_once()
        self.sampler.start.side_effect = None
        monitor._retry_at = 0
        monitor.tick()
        self.assertIsNotNone(monitor.snapshot()["telemetry"])
        self.assertIsNone(monitor.snapshot()["error"])

    def test_dead_sensor_stream_is_restarted(self):
        monitor = self.monitor()
        monitor.start()
        self.sampler.error = "nvidia-smi telemetry stream ended unexpectedly"
        monitor._retry_delay = 0
        monitor.tick()
        self.assertGreaterEqual(self.factory.call_count, 2)

    def test_running_benchmark_sampler_is_read_instead_of_a_second_stream(self):
        benchmark = fake_sampler(cpu_util_percent=77)
        benchmark.source = "benchmark"
        current = {"sampler": None}
        monitor = self.monitor(borrow=lambda: current["sampler"])
        monitor.start()
        current["sampler"] = benchmark
        monitor.tick()
        self.sampler.stop.assert_called_once()
        snapshot = monitor.snapshot()
        self.assertEqual((snapshot["source"], snapshot["capture"]["sampler"]), ("benchmark", "benchmark"))
        self.assertEqual(snapshot["telemetry"]["cpu_util_percent"], 77)
        current["sampler"] = None
        monitor.tick()
        self.assertEqual(self.factory.call_count, 2, "own sensors resume after the benchmark")
        self.assertEqual(monitor.snapshot()["source"], "test")

    def test_log_lives_in_a_gitignored_local_directory(self):
        with patch.dict(os.environ, {"GPU_BENCH_TELEMETRY_DIR": ""}):
            default = server.telemetry_dir()
        self.assertEqual(os.path.basename(default), ".telemetry")
        self.assertEqual(os.path.dirname(default), os.path.dirname(server.store.RUNS_DIR))
        with open(os.path.join(server.ROOT, ".gitignore"), encoding="utf-8") as handle:
            self.assertIn("results/.telemetry/", handle.read().split())
        with patch.dict(os.environ, {"GPU_BENCH_TELEMETRY_DIR": self.directory}):
            self.assertEqual(server.telemetry_dir(), self.directory)

    def test_http_observatory_and_history_do_not_start_benchmark_or_discover_models(self):
        now = time.time()
        earlier = TelemetryLog(self.directory)
        earlier.open()
        for offset in range(3600, 0, -1):
            earlier.append({"t": now - offset, "cpu_util_percent": offset % 100})
        earlier.close()
        monitor = self.monitor()
        monitor.start(self.directory)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:%d" % httpd.server_port
        try:
            with patch.object(server, "SYSTEM_MONITOR", monitor), \
                 patch.object(server.Handler, "env_payload", side_effect=AssertionError("endpoint discovery")), \
                 patch.object(server.BENCH, "start", side_effect=AssertionError("inference")):
                with urllib.request.urlopen(base + "/api/system") as response:
                    payload = json.load(response)
                with urllib.request.urlopen(base + "/api/system/history?start=%f&end=%f&max_points=100" % (now - 3600, now)) as response:
                    history = json.load(response)
                with urllib.request.urlopen(base + "/api/system/history") as response:
                    recent = json.load(response)
                for bad in ("?start=abc", "?start=10&end=5", "?max_points=nan"):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        urllib.request.urlopen(base + "/api/system/history" + bad)
                    self.assertEqual(caught.exception.code, 400)
                    caught.exception.close()
            self.assertEqual(payload["telemetry"]["cpu_util_percent"], 25)
            self.assertEqual(payload["source"], "test")
            self.assertTrue(payload["capture"]["continuous"])
            self.assertEqual(payload["capture"]["log"]["mode"], "owner")
            self.assertAlmostEqual(payload["telemetry"]["t"], payload["timestamp"], delta=1)
            self.assertEqual(history["bucket_s"], 60, "an hour at <=100 points uses one-minute buckets")
            self.assertLessEqual(len(history["points"]), 100)
            self.assertTrue(all(point["n"] >= 1 and "max" in point for point in history["points"]))
            self.assertEqual(recent["bucket_s"], 1, "the default is the last 15 minutes at full resolution")
            self.assertGreaterEqual(len(recent["points"]), 890)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)


class BenchmarkSamplerSelection(unittest.TestCase):
    def test_server_borrows_only_a_running_real_benchmark_sampler(self):
        running = power.PowerSampler.__new__(power.PowerSampler)
        running._thread = object()
        stopped = power.PowerSampler.__new__(power.PowerSampler)
        stopped._thread = None
        with patch.object(server.BENCH, "sampler", running):
            self.assertIs(server._benchmark_sampler(), running)
        with patch.object(server.BENCH, "sampler", stopped):
            self.assertIsNone(server._benchmark_sampler())
        with patch.object(server.BENCH, "sampler", MagicMock(_thread=object())):
            self.assertIsNone(server._benchmark_sampler(), "remote-endpoint placeholders are not sensors")


if __name__ == "__main__":
    unittest.main()
