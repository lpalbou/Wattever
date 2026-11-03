"""CPU-only adversarial tests: no CUDA DLL, context, allocation, or transfer."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import gpu_communication_probe as probe


class FakeCUDA:
    def __init__(self, setup_failure=False):
        self.setup_failure = setup_failure
        self.detached = 0

    def set(self, device):
        if self.setup_failure:
            raise RuntimeError("fake context selection failure")

    def call(self, name, *args):
        if name == "cuCtxSetCurrent":
            self.detached += 1


class FakeRoute:
    def __init__(self, source=0, *, failure=False, setup_failure=False, delay=0):
        self.cuda = FakeCUDA(setup_failure)
        self.source = self.destination = source
        self.transport = "fake"
        self.failure, self.delay = failure, delay
        self.finished = False

    def execute(self, size, count):
        if self.delay:
            time.sleep(self.delay)
        self.finished = True
        if self.failure:
            raise RuntimeError("fake copy failure")
        return {"fake_event_ms": 1}


class CommunicationProbeTests(unittest.TestCase):
    def test_joint_payload_uses_measured_joint_interval(self):
        routes = [FakeRoute(0, delay=.003), FakeRoute(1, delay=.003)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result = probe.sample(pool, routes, 4096, [2, 1])
        self.assertEqual(result["payload_bytes"], 3 * 4096)
        self.assertEqual(result["payload_bytes_per_second"], result["payload_bytes"] / result["elapsed_s"])
        self.assertEqual([row["copies"] for row in result["routes"]], [2, 1])
        self.assertTrue(all(row["host_start_ns"] >= result["host_start_ns"] for row in result["routes"]))
        self.assertTrue(result["host_worker_intervals_overlap"])
        self.assertEqual([route.cuda.detached for route in routes], [1, 1])

    def test_copy_failure_waits_for_sibling_before_return(self):
        first, sibling = FakeRoute(failure=True), FakeRoute(1, delay=.02)
        sibling_started = threading.Event()
        original = sibling.execute
        def sibling_copy(size, count):
            sibling_started.set()
            return original(size, count)
        def failed_copy(size, count):
            self.assertTrue(sibling_started.wait(timeout=2))
            raise RuntimeError("fake copy failure")
        sibling.execute, first.execute = sibling_copy, failed_copy
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with self.assertRaisesRegex(RuntimeError, "fake copy failure"):
                probe.sample(pool, [first, sibling], 64, [1, 1])
            self.assertTrue(sibling.finished)
        self.assertEqual(sibling.cuda.detached, 1)

    def test_setup_failure_aborts_barrier_promptly_and_detaches(self):
        first, sibling = FakeRoute(setup_failure=True), FakeRoute(1)
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            with self.assertRaisesRegex(RuntimeError, "context selection"):
                probe.sample(pool, [first, sibling], 64, [1, 1])
        self.assertLess(time.perf_counter() - started, 2)
        self.assertEqual([first.cuda.detached, sibling.cuda.detached], [1, 1])

    def test_bad_shapes_and_sizes_rejected_without_submission(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for routes, size, counts in (([], 1, []), ([FakeRoute()], -1, [1]),
                                         ([FakeRoute()], 64, []), ([FakeRoute()], 64, [False]),
                                         ([FakeRoute(), FakeRoute(1)], 64, [1, 1])):
                with self.subTest(size=size, counts=counts), self.assertRaises(ValueError):
                    probe.sample(pool, routes, size, counts)

    def test_invalid_run_does_not_initialize_cuda(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(probe, "CUDA") as native:
            for sizes in ([], [0], [-1], [False], [1024 * probe.MIB + 1]):
                with self.assertRaises(ValueError):
                    probe.run(Path(directory) / "capture.json", sizes)
            native.assert_not_called()

    def test_initialization_failure_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.json"
            with patch.object(probe, "CUDA", side_effect=RuntimeError("fake initialization failure")):
                with self.assertRaisesRegex(RuntimeError, "initialization failure"):
                    probe.run(path, [4])
            capture = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(capture["status"], "failed")
            self.assertEqual(capture["cleanup"]["remaining_device_allocations"], 0)

    def test_existing_capture_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text("historic", encoding="utf-8")
            with patch.object(probe, "CUDA") as native, self.assertRaises(ValueError):
                probe.run(path, [4])
            native.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "historic")

    def test_cleanup_context_failure_does_not_skip_other_devices(self):
        cuda = probe.CUDA.__new__(probe.CUDA)
        cuda.streams, cuda.buffers, cuda.host, cuda.contexts = [], {0: {"source": 11}, 1: {"source": 22}}, [33], {0: 1, 1: 2}
        freed, released = [], []
        def select(device):
            if device == 0:
                raise RuntimeError("fake selection failure")
        def call(name, *args):
            if name == "cuMemFree_v2":
                freed.append(args[0])
            if name == "cuDevicePrimaryCtxRelease_v2":
                released.append(args[0])
        cuda.set, cuda.call = select, call
        result = cuda.close()
        self.assertEqual(freed, [22])
        self.assertEqual(released, [0, 1])
        self.assertTrue(result["errors"])
        self.assertIsNone(result["remaining_device_allocations"])

    def test_partial_stream_initialization_remains_registered(self):
        cuda = FakeCUDA()
        cuda.streams = []
        def call(name, *args):
            if name == "cuEventCreate":
                raise RuntimeError("fake event failure")
        cuda.call = call
        with self.assertRaisesRegex(RuntimeError, "event failure"):
            probe.Stream(cuda, 0)
        self.assertEqual(len(cuda.streams), 1)

    def test_cleanup_failure_cannot_claim_completed(self):
        class RunCUDA:
            def inspect(self): return {}
            def allocate(self, size): self.peer_enable_results = []
            def close(self): return {"errors": ["fake cleanup failure"], "contexts_released": False}
        with tempfile.TemporaryDirectory() as directory, patch.object(probe, "CUDA", RunCUDA), patch.object(probe, "configurations", return_value=[]):
            capture = probe.run(Path(directory) / "capture.json", [4])
        self.assertEqual(capture["status"], "failed")
        self.assertIn("cleanup", capture["error"])


if __name__ == "__main__":
    unittest.main()
