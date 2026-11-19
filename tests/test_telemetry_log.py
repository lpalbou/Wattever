"""Rolling 24-hour System log: retention, restarts, torn lines, ownership and downsampling."""
import gzip
import os
import shutil
import tempfile
import unittest

from wattever import telemetry_log
from wattever.telemetry_log import TelemetryLog, compact, downsample

T0 = 1_800_000_000.0  # an exact UTC hour boundary


class Clock:
    def __init__(self, value=T0):
        self.value = value

    def __call__(self):
        return self.value


class TelemetryLogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="wattever-telemetry-test-")
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.clock = Clock()

    def log(self, **kwargs):
        log = TelemetryLog(self.directory, clock=self.clock, **kwargs)
        log.open()
        self.addCleanup(log.close)
        return log

    def fill(self, log, seconds, step=1.0, start=None):
        t = self.clock.value if start is None else start
        for _ in range(int(seconds / step)):
            t += step
            self.clock.value = t
            log.append({"t": t, "cpu_util_percent": t % 100, "gpus": [{"index": 0, "gpu_w": 50.0}]})

    def files(self):
        return sorted(name for name in os.listdir(self.directory) if name != telemetry_log.LOCK_NAME)

    def test_keeps_exactly_the_last_24_hours_and_prunes_whole_files(self):
        log = self.log()
        self.fill(log, 26 * 3600, step=10.0)
        files = self.files()
        self.assertLessEqual(len(files), 25, files)
        self.assertTrue(all(name.endswith(".jsonl.gz") for name in files[:-1]), "finished hours are compressed")
        self.assertTrue(files[-1].endswith(".jsonl"), "only the open hour is plain text")
        result = log.query(T0, self.clock.value, 4000)
        self.assertGreaterEqual(result["start"], self.clock.value - 86400)
        self.assertTrue(all(point["t"] >= self.clock.value - 86400 for point in result["points"]))
        self.assertAlmostEqual(result["oldest"], self.clock.value - 86400, delta=3600)
        self.assertEqual(result["retention_s"], 86400)

    def test_restart_reloads_finished_and_open_hours(self):
        log = self.log(flush_s=3600)
        self.fill(log, 5400)
        log.close()
        self.clock.value += 120  # the helper was down for two minutes
        restarted = self.log()
        self.assertEqual(restarted.mode, "owner")
        before = restarted.query(T0, self.clock.value, 4000)
        self.assertEqual(before["bucket_s"], 2)
        self.assertEqual(len(restarted.query(self.clock.value - 900, self.clock.value)["points"]), 781,
                         "unflushed readings are written on a clean stop")
        self.fill(restarted, 60)
        points = restarted.query(self.clock.value - 300, self.clock.value)["points"]
        gaps = [b["t"] - a["t"] for a, b in zip(points, points[1:])]
        self.assertEqual(max(gaps), 121.0, "the downtime stays a gap, nothing is interpolated")
        self.assertEqual(sum(gap > 4 for gap in gaps), 1)

    def test_torn_last_line_is_skipped_and_terminated_before_appending(self):
        log = self.log(flush_s=0)
        self.fill(log, 10)
        log.close()
        path = os.path.join(self.directory, self.files()[-1])
        with open(path, "ab") as handle:
            handle.write(b'{"t":18000000')  # crash during a write
        with open(path, "ab") as handle:
            pass
        reader = TelemetryLog(self.directory, clock=self.clock)
        reader.mode = "reader"
        self.assertEqual(len(reader.query(T0, self.clock.value)["points"]), 10)
        restarted = self.log(flush_s=0)
        self.fill(restarted, 5)
        restarted.close()
        with open(path, "rb") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(lines[10], b'{"t":18000000')
        self.assertEqual(len(telemetry_log.parse_lines(b"\n".join(lines).decode())), 15)

    def test_corrupt_compressed_hour_does_not_hide_other_hours(self):
        log = self.log()
        self.fill(log, 7200 + 10)
        first = os.path.join(self.directory, self.files()[0])
        with open(first, "wb") as handle:
            handle.write(gzip.compress(b'{"t":1800000001.0}\n')[:-8])
        points = log.query(T0, self.clock.value, 4000)["points"]
        self.assertGreater(len(points), 1000)

    def test_second_helper_reads_the_owner_log_then_takes_over(self):
        owner = self.log(flush_s=0)
        self.fill(owner, 30)
        other = self.log()
        self.assertEqual(other.mode, "reader")
        self.assertIn("Another Wattever helper", other.reason)
        self.assertFalse(other.append({"t": self.clock.value + 0.5}))
        self.assertEqual(len(other.query(T0, self.clock.value)["points"]), 30)
        owner.close()
        self.clock.value += 31
        self.assertTrue(other.append({"t": self.clock.value, "cpu_util_percent": 5}))
        self.assertEqual(other.mode, "owner")

    def test_without_a_directory_history_is_memory_only_and_bounded(self):
        log = TelemetryLog(None, clock=self.clock, memory_points=100)
        log.open()
        self.fill(log, 500)
        result = log.query(T0, self.clock.value, 4000)
        self.assertEqual(len(result["points"]), 100)
        self.assertFalse(result["log"]["persistent"])
        self.assertEqual(os.listdir(self.directory), [])

    def test_range_is_downsampled_to_the_point_budget_with_means_and_peaks(self):
        log = self.log()
        self.fill(log, 86400, step=1.0)
        result = log.query(self.clock.value - 86400, self.clock.value, 900)
        self.assertEqual(result["bucket_s"], 120)
        self.assertLessEqual(len(result["points"]), 900)
        self.assertGreaterEqual(len(result["points"]), 715)
        self.assertEqual(result["gap_s"], 300)
        point = result["points"][10]
        self.assertEqual(point["n"], 120)
        self.assertEqual(point["max"]["cpu_util_percent"], 99)
        self.assertEqual(point["gpus"], [{"index": 0, "gpu_w": 50.0}])
        cached = log.query(self.clock.value - 86400, self.clock.value, 900)
        self.assertEqual(cached["points"][:700], result["points"][:700], "finished hours are served from cache")
        raw = log.query(self.clock.value - 120, self.clock.value, 900)
        self.assertEqual(raw["bucket_s"], 1)
        self.assertNotIn("n", raw["points"][0])

    def test_downsampling_keeps_empty_buckets_absent_and_missing_fields_missing(self):
        records = [{"t": 0.0, "gpu_w": 10.0}, {"t": 1.0}, {"t": 50.0, "gpu_w": 30.0}]
        points = downsample(records, 10)
        self.assertEqual([point["n"] for point in points], [2, 1])
        self.assertEqual(points[0]["gpu_w"], 10.0)
        self.assertNotIn("cpu_util_percent", points[0])

    def test_compact_record_drops_missing_and_non_finite_values(self):
        record = compact({"gpu_w": None, "cpu_util_percent": float("nan"), "temp_c": 61.234, "name": "x",
                          "gpus": [{"index": 1, "gpu_w": 0.0, "name": "card"}, {"index": "bad"}]}, 12.3456)
        self.assertEqual(record, {"t": 12.346, "temp_c": 61.23, "gpus": [{"index": 1, "gpu_w": 0.0}]})

    def test_query_clamps_arguments(self):
        log = self.log()
        self.fill(log, 100)
        result = log.query(self.clock.value - 10 ** 9, self.clock.value + 10 ** 9, 10 ** 9)
        self.assertEqual(result["end"], self.clock.value)
        self.assertEqual(result["start"], self.clock.value - 86400)


if __name__ == "__main__":
    unittest.main()
