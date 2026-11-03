"""Supplementary diagnostic counters; importing this module touches no hardware.

DiagnosticTelemetry(pids=[owned_pid], interval_ms=200) supports start(), stop(),
snapshot(), history(), window(perf_start, perf_end), and summary(). The standalone
harness must own ONE wattever.power.PowerSampler for board watts/energy, shared
by overlapping jobs. This module deliberately does not collect/integrate power.

Only start() initializes native read-only counters. CUDA Driver metadata is used
to map WDDM adapter LUIDs to physical NVML indices; it creates no CUDA context or
allocation. UUIDs, LUIDs, PCI addresses, process names and paths are not exported.

NVML PCIe rates are raw driver-reported KB/s (not bytes integrated over a job).
PDH GPU memory describes WDDM allocation/usage, not proof of paging or traffic.
Global paging includes other applications. Process PageFaultCount includes soft
and hard faults. Do not add process RSS to GPU shared allocation as physical RAM.
Unavailable counters are null with explicit availability, never measured zero.
"""
from collections import deque
import copy
import ctypes as C
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import struct
import threading
import time


def unavailable(reason, code=None):
    value = {"status": "unavailable", "reason": reason}
    if code is not None:
        value["native_status"] = int(code)
    return value


def available():
    return {"status": "available"}


def _bind(dll, name, arguments, result=C.c_int32):
    function = getattr(dll, name)
    function.argtypes, function.restype = arguments, result
    return function


def _system_dll(name):
    if os.name != "nt":
        raise OSError("Windows counters require Windows")
    return C.WinDLL(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name))


class _NVML:
    """Physical NVIDIA enumeration plus context-free CUDA metadata mapping."""
    LINK_QUERIES = {
        "pcie_current_generation": "nvmlDeviceGetCurrPcieLinkGeneration",
        "pcie_current_width": "nvmlDeviceGetCurrPcieLinkWidth",
        "pcie_max_generation": "nvmlDeviceGetMaxPcieLinkGeneration",
        "pcie_max_width": "nvmlDeviceGetMaxPcieLinkWidth",
    }
    def __init__(self):
        self.dll = None
        self.initialized = False
        self.handles = {}
        self.cards = []
        self.luids = {}
        self.link_functions = {}
        self.mapping = unavailable("CUDA LUID mapping not initialized")
        self.state = unavailable("NVML not initialized")

    def open(self):
        try:
            self.dll = _system_dll("nvml.dll")
            _bind(self.dll, "nvmlInit_v2", [])
            _bind(self.dll, "nvmlShutdown", [])
            _bind(self.dll, "nvmlDeviceGetCount_v2", [C.POINTER(C.c_uint)])
            _bind(self.dll, "nvmlDeviceGetHandleByIndex_v2", [C.c_uint, C.POINTER(C.c_void_p)])
            _bind(self.dll, "nvmlDeviceGetName", [C.c_void_p, C.c_char_p, C.c_uint])
            _bind(self.dll, "nvmlDeviceGetPcieThroughput", [C.c_void_p, C.c_uint, C.POINTER(C.c_uint)])
            for field, name in self.LINK_QUERIES.items():
                try:
                    self.link_functions[field] = _bind(self.dll, name, [C.c_void_p, C.POINTER(C.c_uint)])
                except AttributeError:
                    pass  # Optional counter: preserve the other NVML readings.
            status = self.dll.nvmlInit_v2()
            if status:
                self.state = unavailable("NVML initialization failed", status)
                return
            self.initialized = True
            count = C.c_uint()
            status = self.dll.nvmlDeviceGetCount_v2(C.byref(count))
            if status:
                self.state = unavailable("NVML enumeration failed", status)
                return
            for index in range(count.value):
                handle, name = C.c_void_p(), C.create_string_buffer(256)
                status = self.dll.nvmlDeviceGetHandleByIndex_v2(index, C.byref(handle))
                if status:
                    self.cards.append({"gpu_index": index, "name": None, "availability": unavailable("NVML device handle unavailable", status)})
                    continue
                self.handles[index] = handle
                status = self.dll.nvmlDeviceGetName(handle, name, len(name))
                self.cards.append({"gpu_index": index, "name": name.value.decode("utf-8", "replace") if not status else None})
            self.state = available()
            self._map_luids()
        except (OSError, AttributeError):
            self.state = unavailable("NVML DLL or required entry point unavailable")

    def _map_luids(self):
        # No context retention/creation or allocation calls exist in this module.
        class PCI(C.Structure):
            _fields_ = [("busIdLegacy", C.c_char * 16), ("domain", C.c_uint),
                        ("bus", C.c_uint), ("device", C.c_uint), ("pciDeviceId", C.c_uint),
                        ("pciSubSystemId", C.c_uint), ("busId", C.c_char * 32)]
        try:
            _bind(self.dll, "nvmlDeviceGetPciInfo_v3", [C.c_void_p, C.POINTER(PCI)])
            cuda = _system_dll("nvcuda.dll")
            _bind(cuda, "cuInit", [C.c_uint])
            _bind(cuda, "cuDeviceGetByPCIBusId", [C.POINTER(C.c_int), C.c_char_p])
            _bind(cuda, "cuDeviceGetLuid", [C.c_char_p, C.POINTER(C.c_uint), C.c_int])
            code = cuda.cuInit(0)
            if code:
                self.mapping = unavailable("CUDA metadata initialization failed; no ordinal fallback", code)
                return
            failures = 0
            for index, handle in self.handles.items():
                pci, ordinal = PCI(), C.c_int()
                luid, node_mask = C.create_string_buffer(8), C.c_uint()
                if (self.dll.nvmlDeviceGetPciInfo_v3(handle, C.byref(pci)) or
                    cuda.cuDeviceGetByPCIBusId(C.byref(ordinal), pci.busId) or
                    cuda.cuDeviceGetLuid(luid, C.byref(node_mask), ordinal)):
                    failures += 1
                    continue
                low, high = struct.unpack("<II", luid.raw)
                key = (high, low)
                # A duplicated LUID is ambiguous; do not assign counters by name.
                if key in self.luids:
                    self.luids[key] = None
                    failures += 1
                else:
                    self.luids[key] = index
            self.mapping = {"status": "partial" if failures else "available",
                            "method": "NVML PCI address to CUDA cuDeviceGetLuid metadata; no CUDA context",
                            "mapped_gpu_indices": sorted(index for index in self.luids.values() if index is not None),
                            "unmapped_or_ambiguous_count": failures}
        except (OSError, AttributeError):
            self.mapping = unavailable("CUDA/PCI metadata unavailable; PDH memory is not guessed by GPU ordinal")

    def read(self):
        cards = []
        for card in self.cards:
            row = dict(card)
            row["availability"] = {}
            for direction, kind in (("tx", 0), ("rx", 1)):
                field = f"pcie_{direction}_kb_s"
                value = C.c_uint()
                handle = self.handles.get(card["gpu_index"])
                status = self.dll.nvmlDeviceGetPcieThroughput(handle, kind, C.byref(value)) if handle else None
                row[field] = value.value if status == 0 else None
                row["availability"][field] = available() if status == 0 else unavailable("NVML PCIe throughput unsupported or unreadable", status)
            for field in self.LINK_QUERIES:
                value = C.c_uint()
                function = self.link_functions.get(field)
                handle = self.handles.get(card["gpu_index"])
                status = function(handle, C.byref(value)) if function is not None and handle else None
                row[field] = value.value if status == 0 else None
                row["availability"][field] = (available() if status == 0 else
                    unavailable("NVML PCIe link query entry point missing" if function is None else "NVML PCIe link query unsupported or unreadable", status))
            cards.append(row)
        return cards

    def close(self):
        if self.initialized:
            self.dll.nvmlShutdown()
            self.initialized = False


class _PDHValue(C.Union):
    _fields_ = [("longValue", C.c_int32), ("doubleValue", C.c_double), ("largeValue", C.c_int64)]


class _PDHFormatted(C.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("CStatus", C.c_uint32), ("value", _PDHValue)]


class _PDHItem(C.Structure):
    _fields_ = [("name", C.c_wchar_p), ("value", _PDHFormatted)]


_INSTANCE = re.compile(r"(?:pid_(\d+)_)?luid_0x([a-fA-F0-9]+)_0x([a-fA-F0-9]+)_phys_(\d+)")


def map_gpu_memory(arrays, luid_map, indices, pids):
    """Convert private PDH instance names to public physical indices, never names.

    Distinct physical nodes are summed. Duplicate node instances use max and are
    flagged, rather than double-counted. Missing instances remain unavailable.
    """
    output = {index: {"gpu_index": index, "owned_processes": [], "availability": {}} for index in indices}
    owned = {int(pid) for pid in pids}
    for field in ("dedicated_bytes", "shared_bytes", "process_dedicated_bytes", "process_shared_bytes"):
        entries = arrays.get(field, [])
        grouped, duplicate = {}, set()
        for name, value in entries:
            match = _INSTANCE.search(name)
            if not match or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                continue
            pid, high, low, node = match.groups()
            pid = int(pid) if pid is not None else None
            if field.startswith("process_") and pid not in owned:
                continue
            index = luid_map.get((int(high, 16), int(low, 16)))
            if index is None or index not in output:
                continue
            key = (index, pid, int(node))
            if key in grouped:
                duplicate.add((index, pid))
                grouped[key] = max(grouped[key], value)
            else:
                grouped[key] = value
        for index, row in output.items():
            targets = sorted(owned) if field.startswith("process_") else [None]
            for pid in targets:
                selected = [value for (gpu, process, _), value in grouped.items() if gpu == index and process == pid]
                target = row
                key = field.removeprefix("process_")
                if pid is not None:
                    target = next((item for item in row["owned_processes"] if item["pid"] == pid), None)
                    if target is None:
                        target = {"pid": pid, "availability": {}}
                        row["owned_processes"].append(target)
                target[key] = sum(selected) if selected else None
                target["availability"][key] = (available() if selected else unavailable("No mapped PDH instance for this adapter/process"))
                if (index, pid) in duplicate:
                    target["availability"][key]["duplicate_node_instances"] = "max used, not summed"
    return output


class _PDH:
    SPECS = {
        "dedicated_bytes": r"\GPU Adapter Memory(*)\Dedicated Usage",
        "shared_bytes": r"\GPU Adapter Memory(*)\Shared Usage",
        "process_dedicated_bytes": r"\GPU Process Memory(*)\Dedicated Usage",
        "process_shared_bytes": r"\GPU Process Memory(*)\Shared Usage",
        "page_reads_s": r"\Memory\Page Reads/sec",
        "page_writes_s": r"\Memory\Page Writes/sec",
        "pages_input_s": r"\Memory\Pages Input/sec",
        "pages_output_s": r"\Memory\Pages Output/sec",
    }
    def __init__(self):
        self.dll, self.query = None, C.c_void_p()
        self.counters, self.availability = {}, {}

    def open(self):
        try:
            self.dll = _system_dll("pdh.dll")
            _bind(self.dll, "PdhOpenQueryW", [C.c_wchar_p, C.c_size_t, C.POINTER(C.c_void_p)])
            _bind(self.dll, "PdhAddEnglishCounterW", [C.c_void_p, C.c_wchar_p, C.c_size_t, C.POINTER(C.c_void_p)])
            _bind(self.dll, "PdhCollectQueryData", [C.c_void_p])
            _bind(self.dll, "PdhGetFormattedCounterValue", [C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(_PDHFormatted)])
            _bind(self.dll, "PdhGetFormattedCounterArrayW", [C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.c_void_p])
            _bind(self.dll, "PdhCloseQuery", [C.c_void_p])
            code = self.dll.PdhOpenQueryW(None, 0, C.byref(self.query))
            if code:
                raise OSError("PDH query failed")
            for key, path in self.SPECS.items():
                counter = C.c_void_p()
                code = self.dll.PdhAddEnglishCounterW(self.query, path, 0, C.byref(counter))
                self.availability[key] = available() if code == 0 else unavailable("PDH counter unavailable", code)
                if not code:
                    self.counters[key] = counter
            self.dll.PdhCollectQueryData(self.query)  # Prime rate counters.
        except (OSError, AttributeError):
            self.availability = {key: unavailable("Windows PDH or English counters unavailable") for key in self.SPECS}

    def _array(self, counter):
        size, count = C.c_uint32(), C.c_uint32()
        status = self.dll.PdhGetFormattedCounterArrayW(counter, 0x400, C.byref(size), C.byref(count), None)
        if status & 0xffffffff != 0x800007d2:
            return [], unavailable("PDH wildcard query unavailable", status)
        for _ in range(3):
            if not 0 < size.value <= 16 * 1024**2:
                return [], unavailable("PDH wildcard buffer size rejected")
            buffer = C.create_string_buffer(size.value)
            status = self.dll.PdhGetFormattedCounterArrayW(counter, 0x400, C.byref(size), C.byref(count), buffer)
            if status & 0xffffffff == 0x800007d2:
                continue
            if status:
                return [], unavailable("PDH wildcard read failed", status)
            if count.value * C.sizeof(_PDHItem) > len(buffer):
                return [], unavailable("PDH wildcard item count rejected")
            items = C.cast(buffer, C.POINTER(_PDHItem))
            rows = [(items[index].name, items[index].value.largeValue) for index in range(count.value)
                    if items[index].value.CStatus in (0, 1) and items[index].name]
            return rows, available() if rows else unavailable("No valid PDH wildcard instances")
        return [], unavailable("PDH wildcard instances changed during read")

    def read(self):
        arrays, paging, availability = {}, {}, dict(self.availability)
        if not self.query.value:
            return arrays, paging, availability
        status = self.dll.PdhCollectQueryData(self.query)
        if status:
            return {}, {}, {key: unavailable("PDH collection failed", status) for key in self.SPECS}
        for key, counter in self.counters.items():
            if "bytes" in key:
                arrays[key], availability[key] = self._array(counter)
            else:
                value = _PDHFormatted()
                code = self.dll.PdhGetFormattedCounterValue(counter, 0x200, None, C.byref(value))
                valid = not code and value.CStatus in (0, 1) and math.isfinite(value.doubleValue) and value.doubleValue >= 0
                paging[key] = value.doubleValue if valid else None
                availability[key] = available() if valid else unavailable("PDH rate not ready or invalid", code or value.CStatus)
        return arrays, paging, availability

    def close(self):
        if self.query.value:
            self.dll.PdhCloseQuery(self.query)
            self.query = C.c_void_p()


class _ProcessMemory(C.Structure):
    _fields_ = [("cb", C.c_uint32), ("PageFaultCount", C.c_uint32)] + [
        (name, C.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
        "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
        "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]


class _Processes:
    def __init__(self, pids):
        self.pids, self.handles, self.previous = tuple(pids), {}, {}
        self.state = unavailable("Owned process counters not initialized")

    def open(self):
        try:
            self.dll = _system_dll("kernel32.dll")
            self.psapi = _system_dll("psapi.dll")
            _bind(self.dll, "OpenProcess", [C.c_uint32, C.c_int, C.c_uint32], C.c_void_p)
            _bind(self.dll, "CloseHandle", [C.c_void_p], C.c_int)
            _bind(self.dll, "GetProcessTimes", [C.c_void_p] + [C.POINTER(C.c_uint64)] * 4, C.c_int)
            _bind(self.psapi, "GetProcessMemoryInfo", [C.c_void_p, C.POINTER(_ProcessMemory), C.c_uint32], C.c_int)
            for pid in self.pids:
                handle = self.dll.OpenProcess(0x1000 | 0x10, False, pid)
                if not handle:
                    handle = self.dll.OpenProcess(0x400 | 0x10, False, pid)
                if handle:
                    self.handles[pid] = handle
            self.state = available()
        except (OSError, AttributeError):
            self.state = unavailable("Windows owned-process counters unavailable")

    def read(self, timestamp):
        result = []
        for pid in self.pids:
            row = {"pid": pid, "availability": {}}
            handle = self.handles.get(pid)
            if not handle:
                row["availability"]["process"] = unavailable("Owned PID unavailable or access denied")
                result.append(row)
                continue
            created, exited, kernel, user = [C.c_uint64() for _ in range(4)]
            if not self.dll.GetProcessTimes(handle, *[C.byref(value) for value in (created, exited, kernel, user)]) or exited.value:
                row["availability"]["process"] = unavailable("Owned process exited or times unavailable; handle prevents PID reuse")
                result.append(row)
                continue
            cpu = (kernel.value + user.value) / 10_000_000
            row.update(cpu_time_s=cpu, cpu_percent_one_core=None, cpu_percent_machine=None)
            previous = self.previous.get(pid)
            if previous is not None and timestamp > previous[0] and cpu >= previous[1]:
                percent = 100 * (cpu - previous[1]) / (timestamp - previous[0])
                row.update(cpu_percent_one_core=percent, cpu_percent_machine=percent / max(1, os.cpu_count() or 1))
                row["availability"]["cpu_percent"] = available()
            else:
                row["availability"]["cpu_percent"] = unavailable("First process CPU sample")
            self.previous[pid] = (timestamp, cpu)
            memory = _ProcessMemory()
            memory.cb = C.sizeof(memory)
            if self.psapi.GetProcessMemoryInfo(handle, C.byref(memory), memory.cb):
                row.update(working_set_bytes=memory.WorkingSetSize, peak_working_set_bytes=memory.PeakWorkingSetSize,
                           private_commit_bytes=memory.PrivateUsage, commit_charge_bytes=memory.PagefileUsage,
                           page_fault_count=memory.PageFaultCount)
                row["availability"]["memory"] = available()
            else:
                row["availability"]["memory"] = unavailable("Owned process memory unavailable")
            result.append(row)
        return result

    def close(self):
        for handle in self.handles.values():
            self.dll.CloseHandle(handle)
        self.handles.clear()


class WindowsDiagnosticProvider:
    def __init__(self, pids):
        self.nvml, self.pdh, self.processes = _NVML(), _PDH(), _Processes(pids)
        self.pids = tuple(pids)

    def open(self):
        self.nvml.open()
        self.pdh.open()
        self.processes.open()
        return {"nvml": self.nvml.state, "gpu_memory_mapping": self.nvml.mapping,
                "pdh_counters": self.pdh.availability, "processes": self.processes.state}

    def read(self, timestamp):
        cards = self.nvml.read()
        arrays, paging, availability = self.pdh.read()
        memory = map_gpu_memory(arrays, self.nvml.luids, [card["gpu_index"] for card in cards], self.pids)
        for card in cards:
            mapped = memory[card["gpu_index"]]
            card.update({key: value for key, value in mapped.items() if key != "availability"})
            card["availability"].update(mapped["availability"])
            for field in ("dedicated_bytes", "shared_bytes"):
                card.setdefault(field, None)
                counter_state = availability.get(field, unavailable("PDH memory unavailable"))
                if card[field] is None and counter_state.get("status") == "available":
                    counter_state = unavailable("PDH counter exists but no mapped adapter sample is available")
                card["availability"].setdefault(field, counter_state)
        return {"gpus": cards, "processes": self.processes.read(timestamp), "paging": paging,
                "paging_availability": {key: value for key, value in availability.items() if "bytes" not in key},
                "gpu_memory_counter_availability": {key: value for key, value in availability.items() if "bytes" in key}}

    def close(self):
        errors = []
        for provider in (self.processes, self.pdh, self.nvml):
            try:
                provider.close()
            except Exception as error:
                errors.append(type(error).__name__)
        return errors


def _stats(values):
    values = [value for value in values if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]
    return {"valid_samples": len(values), "min": min(values) if values else None,
            "max": max(values) if values else None, "mean": sum(values) / len(values) if values else None}


class DiagnosticTelemetry:
    """Bounded, low-frequency supplementary sampler with injectable fake provider.

    history()/window() return retained raw rows / a summary plus retained raw rows.
    summary() covers retained history and explicitly reports any dropped rows.
    stop() is required before final export; it confirms the sampler thread exited.
    Sampling latency and native collection overhead remain diagnostic overhead.
    """
    def __init__(self, pids=(), interval_ms=200, *, provider=None, max_samples=72000):
        pids = tuple(pids)
        if isinstance(interval_ms, bool) or not isinstance(interval_ms, (int, float)) or not math.isfinite(interval_ms) or interval_ms < 50:
            raise ValueError("interval_ms must be finite and >= 50")
        if isinstance(max_samples, bool) or not isinstance(max_samples, int) or max_samples < 1:
            raise ValueError("max_samples must be a positive integer")
        if any(isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 for pid in pids):
            raise ValueError("pids must contain positive owned process IDs")
        self.pids = tuple(sorted(set(pids)))
        self.interval_ms = interval_ms
        self.provider = provider if provider is not None else WindowsDiagnosticProvider(self.pids)
        self._rows, self._lock = deque(maxlen=max_samples), threading.Lock()
        self._stop, self._thread = threading.Event(), None
        self._total = 0
        self.metadata = {"schema_version": 1, "capture_kind": "diagnostic_telemetry",
                         "power_owner": "External wattever.power.PowerSampler; no energy integration in diagnostic sampler",
                         "interval_ms": interval_ms, "owned_pids": list(self.pids),
                         "pcie_unit": "NVML raw KB/s, driver sampling window; not integrated transfer bytes",
                         "pcie_link_scope": "Current negotiated generation/width polled per card; max is NVML-reported device/system limit, not a manufacturer specification. Current samples are under load only when their timestamps overlap the workload.",
                         "memory_scope": "WDDM allocation/usage; shared allocation is not proof of host spill or paging",
                         "paging_scope": "Whole system, not attributed to owned processes; process faults include hard and soft",
                         "process_commit_scope": "Win32 PagefileUsage is exported as commit_charge_bytes: committed virtual memory, not actual pagefile occupancy or disk paging. Do not sum it with private_commit_bytes.",
                         "process_cpu_scope": "One-core percent may exceed 100%; machine percent divides by logical CPU count",
                         "counter_mapping_policy": "No name/ordinal guesses; private LUID and PCI addresses omitted",
                         "status": "not_started"}

    def start(self):
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError("DiagnosticTelemetry instances cannot be restarted")
        self.metadata["started_at"] = datetime.now(timezone.utc).isoformat()
        self.metadata["perf_anchor_s"] = time.perf_counter()
        try:
            self.metadata["availability"] = self.provider.open()
        except Exception as error:
            self.metadata.update(status="failed", initialization_error=type(error).__name__)
            self.provider.close()
            raise
        self.metadata["status"] = "running"
        self._thread = threading.Thread(target=self._run, name="diagnostic-counters", daemon=True)
        self._thread.start()
        return self

    def _capture(self):
        timestamp = time.perf_counter()
        try:
            row = self.provider.read(timestamp)
        except Exception as error:
            row = {"gpus": [], "processes": [], "paging": {}, "collection_error": type(error).__name__}
        row.update(t_perf=timestamp, collection_end_perf=time.perf_counter())
        row["collection_elapsed_s"] = row["collection_end_perf"] - timestamp
        with self._lock:
            self._rows.append(copy.deepcopy(row))
            self._total += 1
        return row

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            self._capture()
            self._stop.wait(max(0.001, self.interval_ms / 1000 - (time.perf_counter() - started)))

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                self.metadata["status"] = "stop_unconfirmed"
                raise RuntimeError("Diagnostic counter thread did not exit; native counters remain owned")
        if self.metadata["status"] == "running":
            self.metadata["cleanup_errors"] = self.provider.close()
            self.metadata["status"] = "stopped" if not self.metadata["cleanup_errors"] else "cleanup_incomplete"
            self.metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        return self.summary()

    def snapshot(self):
        with self._lock:
            return copy.deepcopy(self._rows[-1]) if self._rows else None

    def history(self):
        with self._lock:
            return copy.deepcopy(list(self._rows))

    def window(self, t0, t1):
        if not all(isinstance(t, (float, int)) and math.isfinite(t) for t in (t0, t1)) or t1 < t0:
            raise ValueError("window requires finite ordered perf_counter timestamps")
        rows = [row for row in self.history() if t0 <= row["t_perf"] <= t1]
        return self._summarize(rows, (t0, t1))

    def summary(self):
        return self._summarize(self.history())

    def _summarize(self, rows, requested=None):
        indices = sorted({gpu["gpu_index"] for row in rows for gpu in row.get("gpus", [])})
        gpus = []
        for index in indices:
            samples = [gpu for row in rows for gpu in row.get("gpus", []) if gpu["gpu_index"] == index]
            metrics = {field: _stats([gpu.get(field) for gpu in samples]) for field in
                       ("pcie_rx_kb_s", "pcie_tx_kb_s", "pcie_current_generation", "pcie_current_width",
                        "pcie_max_generation", "pcie_max_width", "dedicated_bytes", "shared_bytes")}
            gpus.append({"gpu_index": index, "name": samples[0].get("name"), "metrics": metrics})
        processes = []
        for pid in self.pids:
            samples = [process for row in rows for process in row.get("processes", []) if process["pid"] == pid]
            processes.append({"pid": pid, "metrics": {field: _stats([process.get(field) for process in samples]) for field in
                ("cpu_percent_one_core", "cpu_percent_machine", "working_set_bytes", "private_commit_bytes", "commit_charge_bytes", "page_fault_count")}})
        return {"metadata": copy.deepcopy(self.metadata), "sample_count": len(rows),
                "total_samples_collected": self._total, "retention_dropped_samples": max(0, self._total - len(self._rows)),
                "requested_interval_perf": list(requested) if requested else None,
                "observed_interval_perf": [rows[0]["t_perf"], rows[-1]["t_perf"]] if rows else None,
                "gpus": gpus, "processes": processes,
                "paging": {field: _stats([row.get("paging", {}).get(field) for row in rows]) for field in
                           ("page_reads_s", "page_writes_s", "pages_input_s", "pages_output_s")},
                "collection_elapsed_s": _stats([row.get("collection_elapsed_s") for row in rows]),
                "samples": rows}
