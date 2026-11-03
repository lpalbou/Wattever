"""Standalone CUDA/cuBLAS compute and VRAM-copy measurements.

Default / --inspect only lists installed libraries: no CUDA DLL is loaded and no
GPU context or buffer is created. --run is for the explicitly authorized idle
measurement window, after separate Strata-stop / LM Studio unload verification.
This program never starts, stops, loads or switches an inference engine/model.
"""
import argparse
import array
import collections
import ctypes as C
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import threading
import time


ROOT = Path(__file__).resolve().parent
REFERENCES = {
    "cublas": "https://docs.nvidia.com/cuda/cublas/index.html",
    "driver_events": "https://docs.nvidia.com/cuda/cuda-driver-api/cuda_driver_api/group__CUDA__EVENT.html",
}
AROW = tuple(i % 7 - 3 for i in range(16))
AK = tuple((i * 3 + 1) % 7 - 3 for i in range(16))
BK = tuple((i * 5 + 2) % 9 - 4 for i in range(16))
BCOL = tuple(i * 2 % 5 - 2 for i in range(16))


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def discover():
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    candidates = []
    strata = local / "Strata/components/engine"
    for folder in sorted(strata.glob("*-cuda13"), reverse=True):
        if (folder / "cublas64_13.dll").is_file():
            candidates.append({"directory": str(folder), "library": "cublas64_13.dll", "family": "CUDA13"})
    vendor = Path.home() / ".lmstudio/extensions/backends/vendor/win-llama-cuda12-vendor-v2"
    if (vendor / "cublas64_12.dll").is_file():
        candidates.append({"directory": str(vendor), "library": "cublas64_12.dll", "family": "CUDA12"})
    return candidates


def describe_samples(times_ms, work, unit):
    if not times_ms or any(not math.isfinite(value) or value <= 0 for value in times_ms):
        raise ValueError("Timing samples must be finite and positive.")
    rates = [work / (value / 1000.0) / 1e12 for value in times_ms]
    return {"sample_ms": times_ms, "sample_rates": rates, "rate_unit": unit,
            "median_rate": statistics.median(rates), "mean_rate": statistics.mean(rates),
            "min_rate": min(rates), "max_observed_rate": max(rates),
            "median_ms": statistics.median(times_ms),
            "interpretation": "Observed dense-operation throughput, not an advertised peak or an inference-speed prediction."}


def matrix(n, rows, columns, dtype):
    """Column-major, exact small integers in nonconstant rank-two test data."""
    if n % 16:
        raise ValueError("Matrix dimension must be a multiple of 16.")
    import struct
    typecode = {"fp32": "f", "fp16": "H", "int8": "b"}[dtype]
    encode = (lambda value: int.from_bytes(struct.pack("<e", value), "little")) if dtype == "fp16" else lambda value: value
    block = array.array(typecode)
    for column in columns:
        block.extend(array.array(typecode, (encode(row + column) for row in rows)) * (n // 16))
    return block * (n // 16)


def expected_value(n, row, column):
    cycles = n // 16
    return (n * AROW[row % 16] * BCOL[column % 16] +
            AROW[row % 16] * sum(BK) * cycles + BCOL[column % 16] * sum(AK) * cycles +
            sum(a * b for a, b in zip(AK, BK)) * cycles)


class Driver:
    """CUDA driver API only; cuBLAS supplies its own matching runtime internals."""
    def __init__(self, device):
        if os.name != "nt":
            raise RuntimeError("This probe currently implements the installed Windows CUDA path only.")
        self.dll = C.WinDLL(str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/nvcuda.dll"))
        self.allocations = []
        self.events = []
        self.context = C.c_void_p()
        self.stream = C.c_void_p()
        self.device = C.c_int()
        signatures = {
            "cuInit": [C.c_uint], "cuDeviceGet": [C.POINTER(C.c_int), C.c_int],
            "cuDriverGetVersion": [C.POINTER(C.c_int)],
            "cuDeviceGetName": [C.c_char_p, C.c_int, C.c_int],
            "cuDeviceGetPCIBusId": [C.c_char_p, C.c_int, C.c_int],
            "cuDeviceGetAttribute": [C.POINTER(C.c_int), C.c_int, C.c_int],
            "cuDevicePrimaryCtxRetain": [C.POINTER(C.c_void_p), C.c_int],
            "cuDevicePrimaryCtxRelease_v2": [C.c_int], "cuCtxSetCurrent": [C.c_void_p],
            "cuCtxSynchronize": [], "cuMemGetInfo_v2": [C.POINTER(C.c_size_t), C.POINTER(C.c_size_t)],
            "cuMemAlloc_v2": [C.POINTER(C.c_uint64), C.c_size_t], "cuMemFree_v2": [C.c_uint64],
            "cuMemcpyHtoD_v2": [C.c_uint64, C.c_void_p, C.c_size_t],
            "cuMemcpyDtoH_v2": [C.c_void_p, C.c_uint64, C.c_size_t],
            "cuMemcpyDtoDAsync_v2": [C.c_uint64, C.c_uint64, C.c_size_t, C.c_void_p],
            "cuMemAllocHost_v2": [C.POINTER(C.c_void_p), C.c_size_t],
            "cuMemFreeHost": [C.c_void_p],
            "cuMemcpyHtoDAsync_v2": [C.c_uint64, C.c_void_p, C.c_size_t, C.c_void_p],
            "cuStreamCreate": [C.POINTER(C.c_void_p), C.c_uint],
            "cuStreamDestroy_v2": [C.c_void_p], "cuStreamSynchronize": [C.c_void_p],
            "cuEventCreate": [C.POINTER(C.c_void_p), C.c_uint],
            "cuEventRecord": [C.c_void_p, C.c_void_p], "cuEventSynchronize": [C.c_void_p],
            "cuEventElapsedTime": [C.POINTER(C.c_float), C.c_void_p, C.c_void_p],
            "cuEventDestroy_v2": [C.c_void_p],
            "cuGetErrorName": [C.c_int, C.POINTER(C.c_char_p)],
            "cuGetErrorString": [C.c_int, C.POINTER(C.c_char_p)],
        }
        for name, args in signatures.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = args, C.c_int
        self.call("cuInit", 0)
        self.call("cuDeviceGet", C.byref(self.device), device)
        self.call("cuDevicePrimaryCtxRetain", C.byref(self.context), self.device)
        self.call("cuCtxSetCurrent", self.context)
        self.call("cuStreamCreate", C.byref(self.stream), 1)
        for _ in range(2):
            event = C.c_void_p()
            self.call("cuEventCreate", C.byref(event), 0)
            self.events.append(event)

    def call(self, name, *args):
        status = getattr(self.dll, name)(*args)
        if status:
            error = C.c_char_p()
            self.dll.cuGetErrorString(status, C.byref(error))
            raise RuntimeError("%s failed (%d): %s" % (name, status, error.value.decode() if error.value else "unknown"))

    def info(self):
        version, name, bus = C.c_int(), C.create_string_buffer(256), C.create_string_buffer(32)
        self.call("cuDriverGetVersion", C.byref(version))
        self.call("cuDeviceGetName", name, len(name), self.device)
        self.call("cuDeviceGetPCIBusId", bus, len(bus), self.device)
        attributes = {}
        for key, number in (("compute_major", 75), ("compute_minor", 76), ("l2_cache_bytes", 38), ("multiprocessors", 16)):
            value = C.c_int()
            self.call("cuDeviceGetAttribute", C.byref(value), number, self.device)
            attributes[key] = value.value
        free, total = C.c_size_t(), C.c_size_t()
        self.call("cuMemGetInfo_v2", C.byref(free), C.byref(total))
        return {"name": name.value.decode(), "pci_bus_id": bus.value.decode(), "cuda_driver_api_version": version.value,
                "free_bytes_at_start": free.value, "total_bytes": total.value, **attributes}

    def allocate(self, size):
        free, total = C.c_size_t(), C.c_size_t()
        self.call("cuMemGetInfo_v2", C.byref(free), C.byref(total))
        if size > free.value - 1024 ** 3:
            raise RuntimeError("Refusing allocation without 1 GiB remaining free VRAM.")
        value = C.c_uint64()
        self.call("cuMemAlloc_v2", C.byref(value), size)
        self.allocations.append(value.value)
        return value.value

    def free(self, pointer):
        if pointer in self.allocations:
            self.call("cuMemFree_v2", pointer)
            self.allocations.remove(pointer)

    def upload(self, pointer, values):
        self.call("cuMemcpyHtoD_v2", pointer, C.c_void_p(values.buffer_info()[0]), len(values) * values.itemsize)
        # Pageable-host synchronous copy may return after staging. The compute
        # stream is nonblocking, so establish an explicit cross-stream barrier
        # before releasing host storage or consuming the device allocation.
        self.call("cuCtxSynchronize")

    def timed(self, operation, calls=1):
        self.call("cuEventRecord", self.events[0], self.stream)
        start = time.perf_counter()
        for _ in range(calls):
            operation()
        self.call("cuEventRecord", self.events[1], self.stream)
        self.call("cuEventSynchronize", self.events[1])
        elapsed = C.c_float()
        self.call("cuEventElapsedTime", C.byref(elapsed), self.events[0], self.events[1])
        return elapsed.value, time.perf_counter() - start

    def close(self):
        for pointer in list(self.allocations):
            self.free(pointer)
        for event in self.events:
            self.call("cuEventDestroy_v2", event)
        self.events.clear()
        if self.stream:
            self.call("cuStreamDestroy_v2", self.stream)
            self.stream = C.c_void_p()
        if self.context:
            self.call("cuCtxSetCurrent", None)
            self.call("cuDevicePrimaryCtxRelease_v2", self.device)
            self.context = C.c_void_p()


class Blas:
    def __init__(self, driver, directory, filename):
        self.directory_handle = os.add_dll_directory(str(directory))
        self.dll = C.WinDLL(str(directory / filename))
        self.handle = C.c_void_p()
        void = C.c_void_p
        definitions = {
            "cublasCreate_v2": [C.POINTER(void)], "cublasDestroy_v2": [void],
            "cublasSetStream_v2": [void, void], "cublasSetMathMode": [void, C.c_int],
            "cublasGetMathMode": [void, C.POINTER(C.c_int)],
            "cublasGetVersion_v2": [void, C.POINTER(C.c_int)],
            "cublasSgemm_v2": [void, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int,
                                void, void, C.c_int, void, C.c_int, void, void, C.c_int],
            "cublasGemmEx": [void, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, void,
                             void, C.c_int, C.c_int, void, C.c_int, C.c_int,
                             void, void, C.c_int, C.c_int, C.c_int, C.c_int],
        }
        for name, arguments in definitions.items():
            function = getattr(self.dll, name)
            function.argtypes, function.restype = arguments, C.c_int
        self.call("cublasCreate_v2", C.byref(self.handle))
        self.call("cublasSetStream_v2", self.handle, driver.stream)
        version = C.c_int()
        self.call("cublasGetVersion_v2", self.handle, C.byref(version))
        self.version = version.value
        self.properties = {}
        if hasattr(self.dll, "cublasGetProperty"):
            self.dll.cublasGetProperty.argtypes = [C.c_int, C.POINTER(C.c_int)]
            self.dll.cublasGetProperty.restype = C.c_int
            for number, key in enumerate(("major", "minor", "patch")):
                value = C.c_int()
                if self.dll.cublasGetProperty(number, C.byref(value)) == 0:
                    self.properties[key] = value.value

    def call(self, name, *args):
        status = getattr(self.dll, name)(*args)
        if status:
            raise RuntimeError("%s failed with cuBLAS status %d" % (name, status))

    def math_mode(self, mode):
        self.call("cublasSetMathMode", self.handle, mode)
        observed = C.c_int()
        self.call("cublasGetMathMode", self.handle, C.byref(observed))
        if observed.value != mode:
            raise RuntimeError("cuBLAS math mode was not retained.")
        return observed.value

    def close(self):
        if self.handle:
            self.call("cublasDestroy_v2", self.handle)
        self.directory_handle.close()


class Telemetry:
    fields = ("power.draw", "power.limit", "temperature.gpu", "clocks.sm", "clocks.mem", "utilization.gpu", "utilization.memory", "memory.used", "pcie.link.gen.current", "pcie.link.width.current", "pcie.link.gen.max", "pcie.link.width.max")
    names = ("gpu_w", "power_limit_w", "gpu_temp_c", "sm_clock_mhz", "memory_clock_mhz", "gpu_util_percent", "memory_util_percent", "vram_used_mib", "pcie_generation_current", "pcie_width_current", "pcie_generation_max_reported", "pcie_width_max_reported")
    def __init__(self, device):
        self.device, self.phase, self.started = device, "setup", time.perf_counter()
        self.samples = collections.deque(maxlen=3600)
        self.proc = self.thread = None
        self.error = None
    def start(self):
        try:
            self.proc = subprocess.Popen(["nvidia-smi", "-i", str(self.device), "--query-gpu=" + ",".join(self.fields), "--format=csv,noheader,nounits", "-lms=200"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=0x08000000)
            def read():
                for line in self.proc.stdout:
                    values = line.strip().split(",")
                    if len(values) != len(self.names): continue
                    row = {"utc": utc(), "elapsed_s": time.perf_counter() - self.started, "phase": self.phase}
                    for name, value in zip(self.names, values):
                        try: number = float(value); row[name] = number if math.isfinite(number) else None
                        except ValueError: row[name] = None
                    self.samples.append(row)
            self.thread = threading.Thread(target=read, daemon=True)
            self.thread.start()
        except OSError as exc:
            self.error = str(exc)
    def stop(self):
        if self.proc:
            self.proc.terminate()
            try: self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait(timeout=2)
        if self.thread: self.thread.join(timeout=2)
    def report(self):
        rows = list(self.samples)
        summaries = {}
        for phase in sorted({row["phase"] for row in rows}):
            summaries[phase] = {}
            for key in self.names:
                values = [row[key] for row in rows if row["phase"] == phase and row.get(key) is not None]
                summaries[phase][key] = {"count": len(values), "mean": statistics.mean(values) if values else None, "min": min(values) if values else None, "max": max(values) if values else None}
        return {"source": "nvidia-smi selected GPU; includes display/background work", "interval_ms": 200,
                "retention": "latest 3600 points", "samples": rows, "phase_summaries": summaries, "error": self.error}


def gpu_identity(device):
    keys = ("name", "driver_version", "vbios_version", "pci.sub_device_id", "power.limit", "pci.bus_id")
    result = subprocess.run(["nvidia-smi", "-i", str(device), "--query-gpu=" + ",".join(keys), "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=8, creationflags=0x08000000)
    if result.returncode:
        raise RuntimeError("nvidia-smi could not establish the selected GPU identity.")
    values = result.stdout.strip().splitlines()[0].split(",")
    if len(values) != len(keys): raise RuntimeError("Incomplete selected GPU identity.")
    return dict(zip(keys, (value.strip() for value in values)))


def pci_key(value):
    domain, bus, device = value.rsplit(":", 2)
    slot, function = device.split(".")
    return tuple(int(part, 16) for part in (domain, bus, slot, function))


def observe_active_pcie(driver, telemetry, seconds=2.5):
    """Exercise the link for observation only, separate from the matched H2D test."""
    size = 256 * 1024 ** 2
    host = C.c_void_p()
    destination = None
    try:
        destination = driver.allocate(size)
        driver.call("cuMemAllocHost_v2", C.byref(host), size)
        C.memset(host, 0x5A, size)
        telemetry.phase = "pcie_link_training/unmeasured_h2d"
        started = time.perf_counter()
        while time.perf_counter() - started < seconds:
            driver.call("cuMemcpyHtoDAsync_v2", destination, host, size, driver.stream)
            driver.call("cuStreamSynchronize", driver.stream)
        duration = time.perf_counter() - started
        samples = [dict(row) for row in list(telemetry.samples) if row["phase"] == telemetry.phase]
        tail = C.create_string_buffer(16)
        driver.call("cuMemcpyDtoH_v2", tail, destination + size - len(tail), len(tail))
        if tail.raw != b"\x5a" * 16: raise RuntimeError("Unmeasured H2D activity validation failed.")
        return {"status": "observed" if samples else "telemetry_unavailable", "purpose": "Active PCIe generation/width observation, not a throughput benchmark",
                "activity": "Repeated 256 MiB pinned-host-to-device transfers", "duration_s": duration,
                "throughput_GB_s": None, "matched_h2d_comparison": "Do not substitute this activity phase for the separately matched H2D benchmark",
                "active_generations_observed": sorted({row["pcie_generation_current"] for row in samples if row.get("pcie_generation_current") is not None}),
                "active_widths_observed": sorted({row["pcie_width_current"] for row in samples if row.get("pcie_width_current") is not None}),
                "samples": samples, "sampled_tail_validation_passed": True,
                "caveat": "Driver-reported link state while copies were active; max-reported link fields may include platform limits. No DMA bandwidth is inferred from encoding."}
    finally:
        telemetry.phase = "pcie_link_training/cleanup"
        if host: driver.call("cuMemFreeHost", host)
        if destination is not None: driver.free(destination)


def validate_gemm(driver, output, n, integer=False):
    coordinates = {(row, column) for row in (0, 1, 15, 16, n // 2, n - 1) for column in (0, 1, 15, 16, n // 2, n - 1)}
    rng = random.Random(5960)
    coordinates.update((rng.randrange(n), rng.randrange(n)) for _ in range(64))
    errors, examples, mismatches = [], [], []
    for row, column in sorted(coordinates):
        value = C.c_int32() if integer else C.c_float()
        driver.call("cuMemcpyDtoH_v2", C.byref(value), output + (row + column * n) * 4, 4)
        expected = expected_value(n, row, column)
        error = abs(value.value - expected)
        errors.append(error)
        if len(examples) < 8: examples.append({"row": row, "column": column, "expected": expected, "actual": value.value})
        if error and len(mismatches) < 8: mismatches.append({"row": row, "column": column, "expected": expected, "actual": value.value})
    if any(error != 0 for error in errors):
        raise RuntimeError("GEMM sampled output validation failed: maximum error %s; mismatches=%s" % (max(errors), mismatches))
    return {"passed": True, "method": "Exact analytical reference at deterministic corners and pseudorandom coordinates; not every output element is checked",
            "checked_elements": len(errors), "max_absolute_error": max(errors), "required_tolerance": 0, "examples": examples}


def gemm(driver, blas, telemetry, dtype, n, samples, warmups, target_ms):
    telemetry.phase = "%s_n%d/upload" % (dtype, n)
    integer = dtype == "int8"
    host_a = matrix(n, AK if integer else AROW, AROW if integer else AK, dtype)
    host_b = matrix(n, BK, BCOL, dtype)
    pointers = []
    try:
        a = driver.allocate(len(host_a) * host_a.itemsize); pointers.append(a)
        b = driver.allocate(len(host_b) * host_b.itemsize); pointers.append(b)
        c = driver.allocate(n * n * 4); pointers.append(c)
        driver.upload(a, host_a); driver.upload(b, host_b)
        del host_a, host_b
        alpha, beta = (C.c_int32(1), C.c_int32(0)) if integer else (C.c_float(1), C.c_float(0))
        requested_math = 2 if dtype == "fp32" else 1
        mode = blas.math_mode(requested_math)
        if dtype == "fp32":
            def operation():
                blas.call("cublasSgemm_v2", blas.handle, 0, 0, n, n, n, C.byref(alpha), C.c_void_p(a), n, C.c_void_p(b), n, C.byref(beta), C.c_void_p(c), n)
            compute = "FP32 multiplication/accumulation; SGEMM with CUBLAS_PEDANTIC_MATH (TF32 disabled)"
            algorithm = "cublasSgemm_v2 library-selected dense FP32 algorithm"
        else:
            input_type, output_type, compute_type = (3, 10, 72) if integer else (2, 0, 68)
            def operation():
                blas.call("cublasGemmEx", blas.handle, 1 if integer else 0, 0, n, n, n,
                          C.byref(alpha), C.c_void_p(a), input_type, n, C.c_void_p(b), input_type, n,
                          C.byref(beta), C.c_void_p(c), output_type, n, compute_type, 99)
            compute = "INT8 multiplication with INT32 accumulation/output" if integer else "FP16 inputs; CUBLAS_COMPUTE_32F requested; FP32 output; reduced-precision partial reductions not explicitly disabled"
            algorithm = "CUBLAS_GEMM_DEFAULT_TENSOR_OP (99); hardware instruction path is not independently profiled"
        telemetry.phase = "%s_n%d/warmup" % (dtype, n)
        for _ in range(warmups): operation()
        driver.call("cuStreamSynchronize", driver.stream)
        calibration_ms, _ = driver.timed(operation)
        calls = max(1, min(256, math.ceil(target_ms / max(calibration_ms, .001))))
        telemetry.phase = "%s_n%d/timed" % (dtype, n)
        times, wall = [], []
        started = utc()
        for _ in range(samples):
            elapsed, host_elapsed = driver.timed(operation, calls)
            times.append(elapsed); wall.append(host_elapsed)
        ended = utc()
        telemetry.phase = "%s_n%d/validation" % (dtype, n)
        validation = validate_gemm(driver, c, n, integer)
        operations = 2 * n * n * n
        return {"status": "measured", "kind": "dense_gemm", "input_dtype": dtype, "arithmetic": compute,
                "m": n, "n": n, "k": n, "layout": "column_major", "transpose_a": integer, "transpose_b": False,
                "alpha": 1, "beta": 0, "output_dtype": "int32" if integer else "fp32", "math_mode_requested_and_read_back": mode,
                "algorithm": algorithm, "sparsity": "dense operations; no structured-sparsity acceleration requested",
                "accumulation_policy": {"compute_type": "CUBLAS_COMPUTE_32I" if integer else "SGEMM FP32" if dtype == "fp32" else "CUBLAS_COMPUTE_32F",
                                        "reduced_precision_reductions_explicitly_disabled": True if dtype == "fp32" else None if integer else False,
                                        "instruction_path_independently_profiled": False},
                "input_pattern": "Nonconstant small exact integer rank-two matrices; library is still asked to execute full dense GEMM; no zero/identity shortcut",
                "operations_per_call": operations, "operation_count_convention": "2*m*n*k; multiply and addition count separately; excludes host transfer/validation",
                "warmup_calls": warmups, "calibration_ms": calibration_ms, "calls_per_sample": calls, "samples": samples,
                "host_sample_wall_s": wall, "timing": "CUDA stream events around batches; host enqueue gaps can contribute; no input/output transfers in timed region",
                "started_at": started, "ended_at": ended, "validation": validation,
                **describe_samples(times, operations * calls, "TOPS" if integer else "TFLOP/s")}
    finally:
        for pointer in reversed(pointers): driver.free(pointer)


def d2d(driver, telemetry, size_mib, samples, warmups, target_ms):
    size = size_mib * 1024 ** 2
    pointers = []
    try:
        source = driver.allocate(size); pointers.append(source)
        destination = driver.allocate(size); pointers.append(destination)
        telemetry.phase = "d2d_%dmib/random_upload" % size_mib
        digest = hashlib.sha256()
        chunk_bytes = 16 * 1024 ** 2
        for offset in range(0, size, chunk_bytes):
            data = os.urandom(min(chunk_bytes, size - offset)); digest.update(data)
            buffer = C.create_string_buffer(data, len(data))
            driver.call("cuMemcpyHtoD_v2", source + offset, buffer, len(data))
            driver.call("cuCtxSynchronize")
        def operation(): driver.call("cuMemcpyDtoDAsync_v2", destination, source, size, driver.stream)
        telemetry.phase = "d2d_%dmib/warmup" % size_mib
        for _ in range(warmups): operation()
        driver.call("cuStreamSynchronize", driver.stream)
        calibration_ms, _ = driver.timed(operation)
        calls = max(1, min(256, math.ceil(target_ms / max(calibration_ms, .001))))
        telemetry.phase = "d2d_%dmib/timed" % size_mib
        times = [driver.timed(operation, calls)[0] for _ in range(samples)]
        telemetry.phase = "d2d_%dmib/full_validation" % size_mib
        observed = hashlib.sha256()
        for offset in range(0, size, chunk_bytes):
            count = min(chunk_bytes, size - offset)
            buffer = C.create_string_buffer(count)
            driver.call("cuMemcpyDtoH_v2", buffer, destination + offset, count)
            observed.update(buffer.raw)
        if observed.digest() != digest.digest(): raise RuntimeError("VRAM D2D full-payload hash validation failed.")
        payload = [size * calls / (value / 1000) / 1e9 for value in times]
        return {"status": "measured", "kind": "vram_device_to_device_copy", "copy_api": "cuMemcpyDtoDAsync_v2",
                "bytes_per_copy": size, "buffers": 2, "warmup_calls": warmups, "calls_per_sample": calls,
                "sample_ms": times, "payload_GB_s": payload, "read_plus_write_GB_s": [2 * value for value in payload],
                "median_payload_GB_s": statistics.median(payload), "median_read_plus_write_GB_s": 2 * statistics.median(payload),
                "units": "decimal GB/s; payload=size/time; nominal traffic=2*size/time for one read and one write",
                "interpretation": "Measured CUDA D2D copy path, not a theoretical DRAM peak. Cache/copy-engine behavior may contribute; transfers for initialization and full validation are untimed.",
                "payload_pattern": "Independent os.urandom bytes for the entire source allocation", "validation": {"passed": True, "method": "SHA256 of every destination byte equals uploaded source", "sha256": observed.hexdigest()}}
    finally:
        for pointer in reversed(pointers): driver.free(pointer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly start the authorized GPU measurement; default is inspection only")
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--blas-dir", type=Path)
    parser.add_argument("--sizes", default="4096,8192")
    parser.add_argument("--copy-mib", default="256,1024")
    parser.add_argument("--samples", type=int, default=15)
    parser.add_argument("--warmups", type=int, default=5)
    parser.add_argument("--target-sample-ms", type=float, default=80)
    parser.add_argument("--int8", action="store_true", help="Attempt actual INT8/INT32 GEMM; unsupported combinations are recorded, never estimated")
    parser.add_argument("--output", type=Path, default=ROOT / "gpu_compute_results.json")
    args = parser.parse_args()
    available = discover()
    if not args.run or args.inspect:
        print(json.dumps({"status": "inspection_only", "gpu_context_created": False, "available_libraries": available,
                          "plan": "Strict FP32 SGEMM; FP16/FP32-accum GEMM with tensor-enabled policy; optional INT8; full-validated VRAM D2D. Wait for root GO before --run.", "references": REFERENCES}, indent=2))
        return
    sizes = [int(value) for value in args.sizes.split(",")]
    copies = [int(value) for value in args.copy_mib.split(",")]
    if not 3 <= args.samples <= 100 or not 1 <= args.warmups <= 100 or not 10 <= args.target_sample_ms <= 1000:
        parser.error("Use 3..100 samples, 1..100 warmups and 10..1000 ms target batches.")
    if any(n < 256 or n > 16384 or n % 256 for n in sizes) or any(n < 128 or n > 4096 for n in copies):
        parser.error("Matrix sizes must be 256-aligned in 256..16384; copy sizes 128..4096 MiB.")
    chosen = next((item for item in available if not args.blas_dir or Path(item["directory"]) == args.blas_dir), None)
    if args.blas_dir and not chosen:
        files = sorted(args.blas_dir.glob("cublas64_*.dll"), reverse=True)
        chosen = {"directory": str(args.blas_dir), "library": files[0].name} if files else None
    if not chosen: parser.error("No installed cuBLAS DLL found; use --blas-dir to identify an existing installation.")
    report = {"schema_version": 1, "benchmark_kind": "standalone_gpu_compute_and_vram_probe", "started_at": utc(),
              "python_version": sys.version.split()[0], "references": REFERENCES, "measurements": [],
              "baseline_comparison": {"i7_5820k_gpu_compute_probe": "unavailable", "speedup": None,
                                      "note": "No matching TFLOPS/VRAM-copy probe exists for the prior 5820K run; do not claim a measured before/after compute or VRAM-bandwidth change."},
              "int4": {"status": "unavailable", "reason": "No validated INT4 GEMM implementation is included; INT4 weight quantization does not establish INT4 hardware arithmetic."},
              "lifecycle": "Does not control Strata or LM Studio. Operator must separately verify stopped/unloaded engines before this run."}
    driver = blas = None
    telemetry = Telemetry(args.device)
    try:
        telemetry.start()
        driver = Driver(args.device)
        report["gpu"] = driver.info()
        report["gpu"]["nvidia_smi"] = gpu_identity(args.device)
        if pci_key(report["gpu"]["pci_bus_id"]) != pci_key(report["gpu"]["nvidia_smi"]["pci.bus_id"]):
            raise RuntimeError("CUDA device ordinal and nvidia-smi sensor index refer to different PCI devices.")
        blas = Blas(driver, Path(chosen["directory"]), chosen["library"])
        report["library"] = {"dll": chosen["library"], "cublas_get_version": blas.version, "properties": blas.properties,
                             "transport": "NVIDIA CUDA driver API; no separately loaded CUDA runtime DLL"}
        report["active_pcie_observation"] = observe_active_pcie(driver, telemetry)
        for dtype in (["fp32", "fp16", "int8"] if args.int8 else ["fp32", "fp16"]):
            for n in sizes:
                print("Measuring %s %d" % (dtype, n), flush=True)
                try: result = gemm(driver, blas, telemetry, dtype, n, args.samples, args.warmups, args.target_sample_ms)
                except Exception as exc:
                    result = {"status": "unavailable", "input_dtype": dtype, "n": n, "error": str(exc)}
                    if dtype != "int8": raise
                report["measurements"].append(result)
        if not args.int8: report["int8"] = {"status": "not_attempted", "reason": "Enable --int8 to measure an actual supported cuBLAS combination."}
        for size in copies:
            print("Measuring VRAM D2D %d MiB" % size, flush=True)
            result = d2d(driver, telemetry, size, args.samples, args.warmups, args.target_sample_ms)
            result["reported_l2_cache_bytes"] = report["gpu"]["l2_cache_bytes"]
            result["copy_exceeds_reported_l2"] = result["bytes_per_copy"] > report["gpu"]["l2_cache_bytes"]
            report["measurements"].append(result)
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="error", error=str(exc))
        raise
    finally:
        cleanup_errors = []
        try:
            if blas: blas.close()
        except Exception as exc:
            cleanup_errors.append("cuBLAS cleanup: " + str(exc))
        try:
            if driver: driver.close()
        except Exception as exc:
            cleanup_errors.append("CUDA cleanup: " + str(exc))
        try:
            telemetry.stop()
        except Exception as exc:
            cleanup_errors.append("owned telemetry process cleanup: " + str(exc))
        report["cleanup"] = {"cublas_destroy_completed": blas is not None and not any("cuBLAS" in error for error in cleanup_errors),
                             "gpu_allocation_count_remaining": len(driver.allocations) if driver else 0,
                             "cuda_context_reference_released": driver is not None and not bool(driver.context),
                             "stream_destroyed": driver is not None and not bool(driver.stream),
                             "events_remaining": len(driver.events) if driver else 0,
                             "owned_telemetry_process_exited": telemetry.proc is None or telemetry.proc.poll() is not None,
                             "errors": cleanup_errors}
        if cleanup_errors: report["status"] = "error"
        report["telemetry"] = telemetry.report()
        report["finished_at"] = utc()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        print("Saved " + str(args.output), flush=True)
        if cleanup_errors: raise RuntimeError("Probe cleanup failed: " + "; ".join(cleanup_errors))


if __name__ == "__main__":
    main()
