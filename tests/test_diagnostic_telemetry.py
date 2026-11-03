"""CPU-only diagnostic telemetry tests: all hardware providers are fake."""
import ctypes as C
import json
import unittest
from unittest.mock import patch

from tools import diagnostic_telemetry as diagnostic


class FakeProvider:
    def __init__(self):
        self.closed = False
        self.reads = 0
    def open(self): return {"fake": {"status": "available"}}
    def close(self): self.closed = True; return []
    def read(self, timestamp):
        self.reads += 1
        return {"gpus": [{"gpu_index": 0, "name": "GPU A", "pcie_rx_kb_s": 10 * self.reads,
                          "pcie_tx_kb_s": None, "dedicated_bytes": 100, "shared_bytes": None,
                          "availability": {"pcie_tx_kb_s": diagnostic.unavailable("unsupported", 3)}}],
                "processes": [{"pid": 17, "working_set_bytes": 128, "cpu_percent_one_core": 200,
                               "cpu_percent_machine": 12.5}], "paging": {"pages_input_s": 0}}


class DiagnosticTests(unittest.TestCase):
    def test_constructor_does_not_initialize_hardware(self):
        with patch.object(diagnostic.WindowsDiagnosticProvider, "open") as native:
            sampler = diagnostic.DiagnosticTelemetry(pids=(pid for pid in [17]))
            self.assertEqual(sampler.pids, (17,))
            self.assertIsNone(sampler.snapshot())
            native.assert_not_called()

    def test_summary_keeps_missing_distinct_from_zero_and_no_energy_integration(self):
        sampler = diagnostic.DiagnosticTelemetry(pids=[17], provider=FakeProvider())
        with patch.object(diagnostic.time, "perf_counter", side_effect=[10, 10.01, 11, 11.02]):
            sampler._capture()
            sampler._capture()
        summary = sampler.summary()
        metrics = summary["gpus"][0]["metrics"]
        self.assertEqual(metrics["pcie_rx_kb_s"]["mean"], 15)
        self.assertEqual(metrics["pcie_tx_kb_s"]["valid_samples"], 0)
        self.assertIsNone(metrics["shared_bytes"]["max"])
        self.assertEqual(summary["paging"]["pages_input_s"]["mean"], 0)
        self.assertNotIn("energy_j", summary)
        self.assertEqual(sampler.window(10.5, 11.5)["sample_count"], 1)
        self.assertEqual(sampler.window(10.5, 11.5)["gpus"][0]["metrics"]["pcie_rx_kb_s"]["mean"], 20)
        self.assertEqual(sampler.history()[0]["gpus"][0]["availability"]["pcie_tx_kb_s"]["native_status"], 3)

    def test_retention_and_snapshots_cannot_mutate_capture(self):
        sampler = diagnostic.DiagnosticTelemetry(provider=FakeProvider(), max_samples=1)
        sampler._capture()
        sampler._capture()
        snapshot = sampler.snapshot()
        snapshot["gpus"][0]["name"] = "corrupted"
        self.assertEqual(sampler.snapshot()["gpus"][0]["name"], "GPU A")
        summary = sampler.summary()
        self.assertEqual(summary["retention_dropped_samples"], 1)
        self.assertEqual(summary["total_samples_collected"], 2)

    def test_native_errors_do_not_export_exception_paths(self):
        provider = FakeProvider()
        def broken(_): raise OSError(r"C:\Users\private-person\secret")
        provider.read = broken
        sampler = diagnostic.DiagnosticTelemetry(provider=provider)
        sampler._capture()
        serialized = json.dumps(sampler.summary())
        self.assertIn("OSError", serialized)
        self.assertNotIn("private-person", serialized)

    def test_start_stop_owns_one_sampler_and_closes(self):
        provider = FakeProvider()
        sampler = diagnostic.DiagnosticTelemetry(provider=provider)
        sampler.start()
        sampler.stop()
        self.assertTrue(provider.closed)
        self.assertFalse(sampler._thread.is_alive())
        self.assertEqual(sampler.metadata["status"], "stopped")
        with self.assertRaises(RuntimeError): sampler.start()

    def test_validation(self):
        for kwargs in ({"interval_ms": 0}, {"interval_ms": float("nan")}, {"pids": [False]}, {"max_samples": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                diagnostic.DiagnosticTelemetry(**kwargs)
        sampler = diagnostic.DiagnosticTelemetry(provider=FakeProvider())
        with self.assertRaises(ValueError): sampler.window(2, 1)

    def test_luid_mapping_uses_physical_index_and_filters_owned_processes(self):
        arrays = {
            "dedicated_bytes": [("luid_0x00000000_0x000000AA_phys_0", 100),
                                ("luid_0x00000000_0x000000BB_phys_0", 200)],
            "shared_bytes": [("luid_0x00000000_0x000000AA_phys_0", 0)],
            "process_dedicated_bytes": [("pid_17_luid_0x00000000_0x000000AA_phys_0", 40),
                                        ("pid_999_luid_0x00000000_0x000000AA_phys_0", 80)]}
        # Deliberately reverse order to catch accidental CUDA/PDH ordinal matching.
        rows = diagnostic.map_gpu_memory(arrays, {(0, 0xAA): 1, (0, 0xBB): 0}, [0, 1], [17])
        self.assertEqual(rows[0]["dedicated_bytes"], 200)
        self.assertEqual(rows[1]["dedicated_bytes"], 100)
        self.assertEqual(rows[1]["shared_bytes"], 0)
        self.assertIsNone(rows[0]["shared_bytes"])
        self.assertEqual(rows[1]["owned_processes"][0]["dedicated_bytes"], 40)
        serialized = json.dumps(rows)
        self.assertNotIn("luid", serialized)
        self.assertNotIn("999", serialized)

    def test_duplicate_nodes_not_double_counted_and_missing_mapping_not_guessed(self):
        arrays = {"shared_bytes": [("luid_0x0_0xAA_phys_0", 12), ("luid_0x0_0xAA_phys_0#1", 14),
                                    ("luid_0x0_0xAA_phys_1", 20)]}
        rows = diagnostic.map_gpu_memory(arrays, {(0, 0xAA): 0}, [0, 1], [])
        self.assertEqual(rows[0]["shared_bytes"], 34)
        self.assertIn("duplicate_node_instances", rows[0]["availability"]["shared_bytes"])
        self.assertIsNone(rows[1]["shared_bytes"])
        rows = diagnostic.map_gpu_memory(arrays, {(0, 0xAA): None}, [0, 1], [])
        self.assertIsNone(rows[0]["shared_bytes"])

    def test_pdh_array_abi_and_invalid_counter_status_are_respected(self):
        # Fake native output uses the exact ctypes structures bound by production.
        pdh = diagnostic._PDH()
        names = [C.create_unicode_buffer("luid_0x0_0xAA_phys_0"), C.create_unicode_buffer("bad")]
        storage = (diagnostic._PDHItem * 2)()
        storage[0].name = C.cast(names[0], C.c_wchar_p)
        storage[0].value.CStatus, storage[0].value.largeValue = 0, 2**35
        storage[1].name = C.cast(names[1], C.c_wchar_p)
        storage[1].value.CStatus, storage[1].value.largeValue = 0xc0000bc6, 99
        class DLL:
            def PdhGetFormattedCounterArrayW(self, counter, fmt, size_ptr, count_ptr, buffer):
                size = C.cast(size_ptr, C.POINTER(C.c_uint32))
                count = C.cast(count_ptr, C.POINTER(C.c_uint32))
                size[0], count[0] = C.sizeof(storage), 2
                if buffer is None:
                    return C.c_int32(0x800007d2).value
                C.memmove(buffer, storage, C.sizeof(storage))
                return 0
        pdh.dll = DLL()
        values, availability = pdh._array(None)
        self.assertEqual(values, [("luid_0x0_0xAA_phys_0", 2**35)])
        self.assertEqual(availability["status"], "available")

    def test_nvml_unsupported_is_not_zero_and_index_is_preserved(self):
        nvml = diagnostic._NVML()
        nvml.cards = [{"gpu_index": 1, "name": "GPU B"}]
        nvml.handles = {1: 123}
        class DLL:
            def nvmlDeviceGetPcieThroughput(self, handle, direction, pointer):
                self.handle = handle
                if direction == 0:
                    return 3
                C.cast(pointer, C.POINTER(C.c_uint))[0] = 321
                return 0
        nvml.dll = DLL()
        row = nvml.read()[0]
        self.assertEqual(row["gpu_index"], 1)
        self.assertIsNone(row["pcie_tx_kb_s"])
        self.assertEqual(row["pcie_rx_kb_s"], 321)
        self.assertEqual(row["availability"]["pcie_tx_kb_s"]["native_status"], 3)

    def test_nvml_link_negotiation_polled_per_card_and_missing_is_explicit(self):
        nvml = diagnostic._NVML()
        nvml.cards = [{"gpu_index": 0, "name": "GPU A"}, {"gpu_index": 1, "name": "GPU B"}]
        nvml.handles = {0: 100, 1: 200}
        class DLL:
            def nvmlDeviceGetPcieThroughput(self, handle, direction, pointer):
                return 3
        nvml.dll = DLL()
        def generation(handle, pointer):
            C.cast(pointer, C.POINTER(C.c_uint))[0] = 3 if handle == 100 else 1
            return 0
        def width(handle, pointer):
            if handle == 200:
                return 3
            C.cast(pointer, C.POINTER(C.c_uint))[0] = 16
            return 0
        nvml.link_functions = {"pcie_current_generation": generation, "pcie_current_width": width}
        first, second = nvml.read()
        self.assertEqual(first["pcie_current_generation"], 3)
        self.assertEqual(first["pcie_current_width"], 16)
        self.assertEqual(second["pcie_current_generation"], 1)
        self.assertIsNone(second["pcie_current_width"])
        self.assertEqual(second["availability"]["pcie_current_width"]["native_status"], 3)
        self.assertIsNone(first["pcie_max_width"])
        self.assertIn("entry point missing", first["availability"]["pcie_max_width"]["reason"])

    def test_exited_process_handle_does_not_attach_to_reused_pid(self):
        processes = diagnostic._Processes([17])
        processes.handles = {17: 123}
        class DLL:
            def GetProcessTimes(self, handle, created, exited, kernel, user):
                C.cast(exited, C.POINTER(C.c_uint64))[0] = 1
                return 1
        processes.dll = DLL()
        row = processes.read(1)[0]
        self.assertEqual(row["availability"]["process"]["status"], "unavailable")
        self.assertNotIn("cpu_time_s", row)


if __name__ == "__main__":
    unittest.main()
