"""Bounded, isolated transfer microbenchmarks; no inference-engine lifecycle calls.

The storage test bypasses the OS page cache, not a drive's hardware cache. CUDA
copies use pinned host memory and synchronization, not a PCIe speed estimate.
"""
import copy
import ctypes as ct
import json
import math
import mmap
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from . import platform_util as pu

MIB = 1024 ** 2
FILE_BYTES = 256 * MIB
BLOCK_BYTES = 8 * MIB
SAMPLES = 3
_latest = None


def latest():
    return copy.deepcopy(_latest)


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason}


def _summary(samples, **details):
    if not samples or any(not math.isfinite(s["elapsed_s"]) or s["elapsed_s"] <= 0 or
                          s["bytes"] <= 0 for s in samples):
        raise ValueError("Invalid transfer sample")
    return dict(status="ok", bytes_per_second=statistics.median(
        s["bytes"] / s["elapsed_s"] for s in samples), statistic="median",
        samples=samples, **details)


def _windows_reads(path):
    from ctypes import wintypes as wt
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ct.c_void_p,
                                  wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.VirtualAlloc.argtypes = [ct.c_void_p, ct.c_size_t, wt.DWORD, wt.DWORD]
    kernel.VirtualAlloc.restype = ct.c_void_p
    kernel.VirtualFree.argtypes = [ct.c_void_p, ct.c_size_t, wt.DWORD]
    kernel.ReadFile.argtypes = [wt.HANDLE, ct.c_void_p, wt.DWORD,
                               ct.POINTER(wt.DWORD), ct.c_void_p]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    buffer = kernel.VirtualAlloc(None, BLOCK_BYTES, 0x3000, 0x04)
    if not buffer:
        raise OSError("Aligned buffer allocation failed")
    try:
        samples = []
        for _ in range(SAMPLES):
            handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3,
                                        0x20000000 | 0x08000000, None)
            if handle == ct.c_void_p(-1).value:
                raise OSError("Unbuffered file open failed")
            try:
                count, start = 0, time.perf_counter()
                while count < FILE_BYTES:
                    received = wt.DWORD()
                    if not kernel.ReadFile(handle, buffer, BLOCK_BYTES, ct.byref(received), None):
                        raise OSError("Unbuffered read failed")
                    if received.value != BLOCK_BYTES:
                        raise OSError("Incomplete unbuffered read")
                    count += received.value
                samples.append({"bytes": count, "elapsed_s": time.perf_counter() - start})
            finally:
                kernel.CloseHandle(handle)
        return samples, "win32_no_buffering"
    finally:
        kernel.VirtualFree(buffer, 0, 0x8000)


def _posix_reads(path):
    flags = os.O_RDONLY
    if sys.platform.startswith("linux") and hasattr(os, "O_DIRECT"):
        flags |= os.O_DIRECT
        method = "linux_o_direct"
    elif sys.platform == "darwin":
        method = "macos_f_nocache"
    else:
        raise NotImplementedError("OS-cache bypass is unsupported")
    samples = []
    # Anonymous mmap provides page alignment for O_DIRECT + readv.
    with mmap.mmap(-1, BLOCK_BYTES) as buffer:
        for _ in range(SAMPLES):
            fd = os.open(path, flags)
            try:
                if method == "macos_f_nocache":
                    import fcntl
                    fcntl.fcntl(fd, 48, 1)  # F_NOCACHE
                count, start = 0, time.perf_counter()
                while count < FILE_BYTES:
                    received = os.readv(fd, [buffer])
                    if received != BLOCK_BYTES:
                        raise OSError("Incomplete direct read")
                    count += received
                samples.append({"bytes": count, "elapsed_s": time.perf_counter() - start})
            finally:
                os.close(fd)
    return samples, method


def storage_probe(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile removes only the exact file created by this invocation.
    with tempfile.NamedTemporaryFile(prefix="wattever-bandwidth-", suffix=".bin",
                                     dir=directory, delete=False) as stream:
        path = Path(stream.name)
        try:
            block = os.urandom(BLOCK_BYTES)
            for _ in range(FILE_BYTES // BLOCK_BYTES):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    try:
        samples, method = (_windows_reads(path) if pu.IS_WINDOWS else _posix_reads(path))
        return _summary(samples, method=method, file_bytes=FILE_BYTES, block_bytes=BLOCK_BYTES,
                        queue_depth=1, os_page_cache="bypassed", storage_scope="probe_directory_volume",
                        volume=directory.drive or "results filesystem",
                        note="Sequential reads of a flushed temporary file on the selected volume (results volume by default). File creation is excluded. Device/controller caches may contribute; model files on other volumes need separate measurement.")
    finally:
        path.unlink(missing_ok=True)


def cuda_probe(gpu_index):
    from . import power
    sensors = power.list_gpus()
    selected = next((s for s in sensors if s.get("index") == gpu_index), None)
    if not selected or not selected.get("pci_bus_id"):
        return _unavailable("No NVIDIA sensor with a verified PCI bus mapping")
    # Do not create another CUDA context when an engine already fills VRAM.
    free_result = pu.run(["nvidia-smi", "--id=%d" % gpu_index,
                          "--query-gpu=memory.free", "--format=csv,noheader,nounits"], timeout=5)
    if free_result.returncode or float(free_result.stdout.strip()) < 512:
        return _unavailable("Less than 512 MiB free VRAM or free memory unknown")
    driver = ct.WinDLL("nvcuda.dll") if pu.IS_WINDOWS else ct.CDLL("libcuda.so.1")
    def bind(name, args):
        fn = getattr(driver, name)
        fn.argtypes, fn.restype = args, ct.c_int
        return fn
    def check(code):
        if code:
            raise RuntimeError("CUDA driver operation failed (%d)" % code)
    init = bind("cuInit", [ct.c_uint])
    by_bus = bind("cuDeviceGetByPCIBusId", [ct.POINTER(ct.c_int), ct.c_char_p])
    name = bind("cuDeviceGetName", [ct.c_char_p, ct.c_int, ct.c_int])
    create = bind("cuCtxCreate_v2", [ct.POINTER(ct.c_void_p), ct.c_uint, ct.c_int])
    destroy = bind("cuCtxDestroy_v2", [ct.c_void_p])
    info = bind("cuMemGetInfo_v2", [ct.POINTER(ct.c_size_t), ct.POINTER(ct.c_size_t)])
    host_alloc = bind("cuMemAllocHost_v2", [ct.POINTER(ct.c_void_p), ct.c_size_t])
    host_free = bind("cuMemFreeHost", [ct.c_void_p])
    dev_alloc = bind("cuMemAlloc_v2", [ct.POINTER(ct.c_uint64), ct.c_size_t])
    dev_free = bind("cuMemFree_v2", [ct.c_uint64])
    copy_htod = bind("cuMemcpyHtoD_v2", [ct.c_uint64, ct.c_void_p, ct.c_size_t])
    copy_dtoh = bind("cuMemcpyDtoH_v2", [ct.c_void_p, ct.c_uint64, ct.c_size_t])
    sync = bind("cuCtxSynchronize", [])
    check(init(0))
    device = ct.c_int()
    check(by_bus(ct.byref(device), selected["pci_bus_id"].encode("ascii")))
    device_name = ct.create_string_buffer(128)
    check(name(device_name, 128, device))
    context, host, target = ct.c_void_p(), ct.c_void_p(), ct.c_uint64()
    check(create(ct.byref(context), 0, device))
    try:
        free, total = ct.c_size_t(), ct.c_size_t()
        check(info(ct.byref(free), ct.byref(total)))
        if free.value < 256 * MIB:
            return _unavailable("CUDA reports insufficient free device memory for a bounded probe")
        size, copies = 64 * MIB, 32
        check(host_alloc(ct.byref(host), size))
        check(dev_alloc(ct.byref(target), size))
        ct.memset(host, 0x5a, size)  # Commit every host page before timing.
        for _ in range(4):
            check(copy_htod(target, host, size))
        check(sync())
        samples = []
        for _ in range(SAMPLES):
            start = time.perf_counter()
            for _ in range(copies):
                check(copy_htod(target, host, size))
            check(sync())
            samples.append({"bytes": size * copies, "elapsed_s": time.perf_counter() - start})
        # Verify the destination via a completed readback outside the timed region.
        readback = ct.create_string_buffer(1)
        check(copy_dtoh(readback, target, 1))
        if readback.raw != b"\x5a":
            raise RuntimeError("CUDA copy verification failed")
        return _summary(samples, method="cuda_driver_synchronous_htod", host_memory="pinned",
                        buffer_bytes=size, copies_per_sample=copies, warmup_copies=4,
                        gpu_sensor_index=gpu_index, gpu_name=device_name.value.decode("utf-8"),
                        verified_readback=True,
                        note="Pinned RAM to device memory; host wall time includes completed copies and synchronization. Separate microbenchmark, not observed inference traffic or VRAM internal bandwidth.")
    finally:
        if target.value:
            dev_free(target)
        if host.value:
            host_free(host)
        destroy(context)


def _worker(directory, gpu_index):
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "scope": "benchmark_client", "unit": "bytes_per_second",
              "protocol": "wattever-transfer-v1"}
    for key, call in (("storage_to_ram", lambda: storage_probe(directory)),
                      ("ram_to_vram", lambda: cuda_probe(gpu_index))):
        try:
            result[key] = call()
        except Exception as exc:
            # Public files never include driver diagnostics or local file paths.
            result[key] = _unavailable("Transfer probe could not complete (%s)" % type(exc).__name__)
    return result


def measure(gpu_index=0, directory=None):
    """Run before inference; bounded child process releases its CUDA context on exit."""
    global _latest
    directory = str(Path(directory or Path(__file__).resolve().parents[1] / "results").resolve())
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        # The child owns only this freshly created directory. Timeout cleanup is
        # performed after subprocess.run has killed and reaped the child.
        with tempfile.TemporaryDirectory(prefix=".transfer-", dir=directory) as scratch:
            assert Path(scratch).resolve().parent == Path(directory)
            result = subprocess.run([sys.executable, "-m", "wattever.bandwidth", "--worker", scratch,
                                     str(gpu_index)], timeout=75, capture_output=True, text=True,
                                    cwd=str(Path(__file__).resolve().parents[1]), **pu.popen_kwargs())
        if result.returncode:
            raise ValueError("Worker failed")
        profile = json.loads(result.stdout)
        if profile.get("protocol") != "wattever-transfer-v1":
            raise ValueError("Invalid worker result")
        _latest = copy.deepcopy(profile)
        return profile
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                "scope": "benchmark_client", "protocol": "wattever-transfer-v1", "unit": "bytes_per_second",
                "storage_to_ram": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_to_vram": _unavailable("Probe worker failed or exceeded 75 seconds")}


def attach(run, profile, *, provenance="measured_before_session"):
    profile = copy.deepcopy(profile)
    profile["provenance"] = provenance
    run.setdefault("hardware", {})["transfer_bandwidth"] = profile
    for session in run.get("sessions", []):
        if session.get("id") == run.get("active_session_id"):
            session["transfer_bandwidth"] = copy.deepcopy(profile)
    return profile


def backfill(run, current, profile):
    """Attach a later measurement only when recorded hardware/driver match."""
    if not (run.get("classification") or {}).get("hardware_class") or (
            run["classification"]["hardware_class"] != (current.get("classification") or {}).get("hardware_class")):
        raise ValueError("Hardware differs or cannot be verified; transfer metadata was not updated.")
    if any((run.get("gpu") or {}).get(k) != (current.get("gpu") or {}).get(k)
           for k in ("name", "vram_total_mb", "driver")):
        raise ValueError("The selected GPU or driver differs; transfer metadata was not updated.")
    # Historical sessions retain their original measurement metadata.
    value = copy.deepcopy(profile)
    value.update(provenance="measured_later_on_matching_hardware",
                 historical_inference_conditions_verified=False)
    run.setdefault("hardware", {})["transfer_bandwidth"] = value
    run.setdefault("hardware_measurement_history", []).append({"captured_at": value["captured_at"],
        "kind": "transfer_bandwidth", "provenance": value["provenance"]})
    return value


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit("Internal bandwidth worker requires a directory and GPU sensor index")
    print(json.dumps(_worker(sys.argv[2], int(sys.argv[3]))))
