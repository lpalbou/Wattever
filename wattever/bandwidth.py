"""Bounded, isolated transfer microbenchmarks; no inference-engine lifecycle calls.

The storage test bypasses the OS page cache, not a drive's hardware cache. CUDA
copies use pinned host memory and synchronization, not a PCIe speed estimate.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
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
import threading
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


def _capture_hardware_key(machine):
    """Conservative compatibility key, including clocks absent from hardware_class."""
    if not isinstance(machine, dict):
        return None
    cpu = machine.get("cpu") or {}
    memory = machine.get("memory") or {}
    system = machine.get("system") or {}
    if any(not isinstance(value, dict) for value in (cpu, memory, system)):
        return None
    clocks = memory.get("configured_clock_mhz_reported") or []
    capacity = memory.get("installed_capacity_bytes") or system.get("memory_bytes")
    cpu_fields = tuple(cpu.get(field) for field in ("name", "physical_cores", "logical_cores"))
    cards = machine.get("gpus") or []
    if (not all(cpu_fields) or not capacity or not isinstance(clocks, list) or not clocks or
            not isinstance(cards, list) or not cards or any(not isinstance(card, dict) for card in cards)):
        return None
    if any(not isinstance(clock, (int, float)) or isinstance(clock, bool) or not math.isfinite(clock) or clock <= 0 for clock in clocks):
        return None
    gpu_fields = [tuple(card.get(field) for field in ("name", "memory_bytes", "driver")) for card in cards]
    if any(not all(card) or not isinstance(card[0], str) or not isinstance(card[2], str) for card in gpu_fields):
        return None
    return (cpu_fields, capacity, memory.get("module_count"), tuple(sorted(set(clocks))), tuple(sorted(gpu_fields)))


def saved_capture(machine, selected_gpu=None):
    """Read a matching saved probe without measuring, caching, or changing any run.

    An independent capture is preferred to a session capture, then newest wins
    within that category. Missing RAM clocks cannot establish compatibility.
    """
    from . import store
    from .validation import validate_transfer_bandwidth
    current_key = _capture_hardware_key(machine)
    if current_key is None:
        return None
    candidates = []

    def consider(run, profile, snapshot, kind, session_id=None):
        if (not isinstance(profile, dict) or profile.get("protocol") != "wattever-transfer-v1" or
                _capture_hardware_key(snapshot) != current_key or validate_transfer_bandwidth(profile)):
            return
        paths = [profile.get(field) for field in ("storage_to_ram", "ram_to_vram", "ram_copy_single", "ram_copy_parallel")]
        paths.extend(profile.get("storage_volumes") or [])
        if not any(isinstance(path, dict) and path.get("status") == "ok" and
                   isinstance(path.get("bytes_per_second"), (int, float)) and path["bytes_per_second"] > 0 for path in paths):
            return
        probed_gpu = (profile.get("ram_to_vram") or {}).get("gpu_name")
        if selected_gpu and probed_gpu and probed_gpu != selected_gpu.get("name"):
            return
        try:
            captured = datetime.fromisoformat(profile["captured_at"].replace("Z", "+00:00"))
            if captured.tzinfo is None:
                return
            timestamp = captured.timestamp()
        except (KeyError, TypeError, ValueError, OverflowError):
            return
        candidates.append(({"independent": 2, "session": 1, "benchmark": 0}[kind], timestamp,
                           profile, run.get("id"), kind, session_id))

    try:
        paths = sorted(Path(store.RUNS_DIR).glob("*.json"))
    except OSError:
        return None
    for path in paths:
        try:
            run = store.load(path.stem)
        except (OSError, ValueError):
            continue
        if not run:
            continue
        for capture in run.get("hardware_measurement_history") or []:
            if isinstance(capture, dict):
                consider(run, capture.get("transfer_bandwidth"), capture.get("hardware_snapshot"), "independent")
        for session in run.get("sessions") or []:
            if isinstance(session, dict):
                consider(run, session.get("transfer_bandwidth"), session.get("hardware"), "session", session.get("id"))
        snapshot = run.get("hardware") or {}
        if isinstance(snapshot, dict):
            consider(run, snapshot.get("transfer_bandwidth"), snapshot, "benchmark")
    if not candidates:
        return None
    _, _, original, run_id, kind, session_id = max(candidates, key=lambda item: item[:2])
    profile = copy.deepcopy(original)
    profile["source"] = "saved_capture"
    profile["saved_capture"] = {
        "source_run_id": run_id, "capture_kind": kind, "captured_at": original["captured_at"],
        "measured_this_helper_session": False,
        "matching_fields": ["cpu_name_and_core_counts", "ram_capacity_and_module_count",
                            "reported_ram_clocks", "all_gpu_names_memory_and_drivers"],
    }
    if session_id is not None:
        profile["saved_capture"]["source_session_id"] = session_id
    return profile


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
        device = {}
        drive = directory.drive
        if pu.IS_WINDOWS and len(drive) == 2 and drive[0].isalpha() and drive[1] == ":":
            try:
                command = "Get-Partition -DriveLetter '%s' | Get-Disk | Select-Object FriendlyName,@{Name='BusType';Expression={$_.BusType.ToString()}} | ConvertTo-Json -Compress" % drive[0]
                query = pu.run(["powershell", "-NoProfile", "-Command", command], timeout=8)
                disk = json.loads(query.stdout) if query.returncode == 0 else {}
                if isinstance(disk, dict):
                    device = {"device_model": disk.get("FriendlyName"), "bus_type": disk.get("BusType")}
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
        return _summary(samples, method=method, file_bytes=FILE_BYTES, block_bytes=BLOCK_BYTES,
                        queue_depth=1, os_page_cache="bypassed", storage_scope="probe_directory_volume",
                        volume=directory.drive or "results filesystem", **device,
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


def memory_probe(workers=1, buffer_bytes=64 * MIB, copies=8):
    """Completed native copies over a working set larger than typical CPU caches.

    Report copied payload bytes, not a guessed memory-controller traffic rate.
    ctypes releases the GIL around memmove; every worker owns disjoint buffers.
    """
    if not 1 <= workers <= 8 or buffer_bytes < 4096 or copies < 1:
        raise ValueError("Invalid bounded memory-copy workload")
    buffers = [(mmap.mmap(-1, buffer_bytes), mmap.mmap(-1, buffer_bytes)) for _ in range(workers)]
    views = []
    try:
        pointers = []
        for source, destination in buffers:
            src, dst = ct.c_char.from_buffer(source), ct.c_char.from_buffer(destination)
            views.extend((src, dst))
            a, b = ct.addressof(src), ct.addressof(dst)
            ct.memset(a, 0x5a, buffer_bytes)
            ct.memset(b, 0, buffer_bytes)
            pointers.append((a, b))
        samples = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            def transfer(pair, gate, count):
                gate.wait()
                for _ in range(count):
                    ct.memmove(pair[1], pair[0], buffer_bytes)
            for sample in range(SAMPLES + 1):
                clock = {}
                gate = threading.Barrier(workers + 1, action=lambda: clock.update(start=time.perf_counter()))
                tasks = [pool.submit(transfer, pair, gate, copies if sample else 1) for pair in pointers]
                gate.wait(timeout=15)
                for task in tasks:
                    task.result(timeout=20)
                elapsed = time.perf_counter() - clock["start"]
                if sample:
                    samples.append({"bytes": workers * buffer_bytes * copies, "elapsed_s": elapsed})
        verified = all(destination[0] == 0x5a and destination[-1] == 0x5a for _, destination in buffers)
        if not verified:
            raise RuntimeError("Memory-copy verification failed")
        return _summary(samples, method="native_memmove_disjoint_buffers", workers=workers,
                        buffer_bytes_per_worker=buffer_bytes, working_set_bytes=workers * buffer_bytes * 2,
                        copies_per_sample=copies, verified_readback=True, byte_accounting="copied_payload",
                        note="Native RAM copy payload throughput. Each payload byte requires a source read and destination write; 2x this rate is logical read+write bandwidth, not measured DRAM bus traffic. Cache/write-allocation effects and worker count influence results.")
    finally:
        views.clear()
        # Loop variables can retain exported mmap references.
        src = dst = None
        for source, destination in buffers:
            source.close()
            destination.close()


def _worker(directory, gpu_index, extra_directories=None):
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "scope": "benchmark_client", "unit": "bytes_per_second",
              "protocol": "wattever-transfer-v1"}
    for key, call in (("storage_to_ram", lambda: storage_probe(directory)),
                      ("ram_to_vram", lambda: cuda_probe(gpu_index)),
                      ("ram_copy_single", lambda: memory_probe()),
                      ("ram_copy_parallel", lambda: memory_probe(min(8, max(1, (os.cpu_count() or 2) // 2))))):
        try:
            result[key] = call()
        except Exception as exc:
            # Public files never include driver diagnostics or local file paths.
            result[key] = _unavailable("Transfer probe could not complete (%s)" % type(exc).__name__)
    if extra_directories:
        result["storage_volumes"] = []
        for folder in extra_directories:
            try:
                result["storage_volumes"].append(storage_probe(folder))
            except Exception as exc:
                result["storage_volumes"].append(dict(_unavailable("Storage probe could not complete (%s)" % type(exc).__name__),
                                                       volume=Path(folder).drive or "additional filesystem"))
    return result


def measure(gpu_index=0, directory=None, directories=None):
    """Run before inference; bounded child process releases its CUDA context on exit."""
    global _latest
    directory = str(Path(directory or Path(__file__).resolve().parents[1] / "results").resolve())
    try:
        Path(directory).mkdir(parents=True, exist_ok=True)
        # The child owns only this freshly created directory. Timeout cleanup is
        # performed after subprocess.run has killed and reaped the child.
        with ExitStack() as owned:
            scratch = owned.enter_context(tempfile.TemporaryDirectory(prefix=".transfer-", dir=directory))
            assert Path(scratch).resolve().parent == Path(directory)
            extras = []
            for folder in dict.fromkeys(str(Path(p).resolve()) for p in (directories or [])):
                if folder == directory:
                    continue
                if not Path(folder).is_dir():
                    raise ValueError("Additional storage folders must exist")
                extras.append(owned.enter_context(tempfile.TemporaryDirectory(prefix=".transfer-", dir=folder)))
            args = [sys.executable, "-m", "wattever.bandwidth", "--worker", scratch, str(gpu_index)]
            if extras:
                args.append(json.dumps(extras))
            result = subprocess.run(args, timeout=75, capture_output=True, text=True,
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
                "ram_to_vram": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_copy_single": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_copy_parallel": _unavailable("Probe worker failed or exceeded 75 seconds")}


def attach(run, profile, *, provenance="measured_before_session"):
    profile = copy.deepcopy(profile)
    profile["provenance"] = provenance
    run.setdefault("hardware", {})["transfer_bandwidth"] = profile
    for session in run.get("sessions", []):
        if session.get("id") == run.get("active_session_id"):
            session["transfer_bandwidth"] = copy.deepcopy(profile)
            for group in run.get("engine_groups", []):
                if group.get("id") == session.get("engine_group_id"):
                    if session.get("hardware"):
                        group["hardware"] = copy.deepcopy(session["hardware"])
                    group.setdefault("hardware", {})["transfer_bandwidth"] = copy.deepcopy(profile)
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
        "kind": "transfer_bandwidth", "provenance": value["provenance"],
        "hardware_snapshot": copy.deepcopy(current.get("hardware") or {}),
        "transfer_bandwidth": copy.deepcopy(value)})
    return value


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5) or sys.argv[1] != "--worker":
        raise SystemExit("Internal bandwidth worker requires a directory and GPU sensor index")
    print(json.dumps(_worker(sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4]) if len(sys.argv) == 5 else None)))
