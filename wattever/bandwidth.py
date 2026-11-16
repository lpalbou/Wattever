"""Bounded, isolated transfer microbenchmarks; no inference-engine lifecycle calls.

The storage test bypasses the OS page cache, not a drive's hardware cache. CUDA
copies use pinned host memory and synchronization, not a PCIe speed estimate.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import ctypes as ct
import hashlib
import json
import math
import mmap
import os
from pathlib import Path
import plistlib
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

from . import platform_util as pu, power

MIB = 1024 ** 2
FILE_BYTES = 256 * MIB
BLOCK_BYTES = 8 * MIB
SAMPLES = 3
MAX_STORAGE_VOLUMES = 8
MAX_EXTRA_STORAGE_PROBES = 3
_latest = None
_latest_hardware_key = None


def _gpu_path_fingerprint(sensors):
    """Configuration key from private PCI paths; never retain raw bus addresses.

    A matching fingerprint identifies the observed bus mapping, not motherboard
    connector names or a device serial. Invalid/missing mappings fail closed.
    """
    paths = []
    for sensor in sensors:
        if not isinstance(sensor, dict) or not sensor.get('pci_bus_id'):
            return None
        index, name, address = sensor.get('index'), sensor.get('name'), sensor['pci_bus_id']
        match = re.fullmatch(r'([0-9a-fA-F]{4,8}):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])', str(address))
        if type(index) is not int or index < 0 or not isinstance(name, str) or not name or match is None:
            return None
        domain, bus, device, function = (int(part, 16) for part in match.groups())
        paths.append((index, name, '%x:%02x:%02x.%x' % (domain, bus, device, function)))
    if not paths or len({row[0] for row in paths}) != len(paths):
        return None
    return hashlib.sha256(json.dumps(sorted(paths), separators=(',', ':'), ensure_ascii=True).encode('utf-8')).hexdigest()


def _current_gpu_path_fingerprint(sensors=None):
    try:
        return _gpu_path_fingerprint(power.list_gpus() if sensors is None else sensors)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _mapped_gpu_signature(cards, captured=False):
    fields = ('gpu_sensor_index', 'gpu_name', 'gpu_memory_bytes') if captured else ('sensor_index', 'name', 'memory_bytes')
    return sorted((str(card.get(fields[0])), str(card.get(fields[1])), str(card.get(fields[2])))
                  for card in cards if isinstance(card, dict) and card.get(fields[0]) is not None
                  and (captured or card.get('memory_architecture') != 'unified'))


def latest(machine=None, sensors=None):
    if machine is not None:
        if not _latest or 'ram_to_vram_devices' not in _latest:
            return None
        if _latest_hardware_key is None or _capture_hardware_key(machine, require_clocks=False) != _latest_hardware_key:
            return None
        if _mapped_gpu_signature(_latest['ram_to_vram_devices'], captured=True) != _mapped_gpu_signature(machine.get('gpus') or []):
            return None
        if _mapped_gpu_signature(machine.get('gpus') or []):
            captured = _latest.get('gpu_path_fingerprint')
            if not captured or captured != _current_gpu_path_fingerprint(sensors):
                return None
    return copy.deepcopy(_latest)


def _capture_hardware_key(machine, require_clocks=True):
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
    if (not all(cpu_fields) or not capacity or not isinstance(clocks, list) or (require_clocks and not clocks) or
            not isinstance(cards, list) or not cards or any(not isinstance(card, dict) for card in cards)):
        return None
    if any(not isinstance(clock, (int, float)) or isinstance(clock, bool) or not math.isfinite(clock) or clock <= 0 for clock in clocks):
        return None
    gpu_fields = [tuple(card.get(field) for field in ("name", "memory_bytes", "driver")) for card in cards]
    if any(not card[0] or not card[1] or not isinstance(card[0], str) or
           (require_clocks and (not card[2] or not isinstance(card[2], str))) for card in gpu_fields):
        return None
    gpu_fields = [(name, memory_bytes, str(driver or '')) for name, memory_bytes, driver in gpu_fields]
    # Index and link width preserve the observed device/path association. A
    # reported maximum still does not establish a physical motherboard slot.
    gpu_fields = [base + (str(card.get('sensor_index')), str((card.get('pcie') or {}).get('width_current')),
                         str((card.get('pcie') or {}).get('generation_max_reported')),
                         str((card.get('pcie') or {}).get('width_max_reported')))
                  for base, card in zip(gpu_fields, cards)]
    return (cpu_fields, capacity, memory.get("module_count"), tuple(sorted(set(clocks))), tuple(sorted(gpu_fields)))


def saved_capture(machine, selected_gpu=None, sensors=None):
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
    current_path = []  # Lazy: legacy single-card history needs no driver calls.

    def path_match(profile):
        captured = profile.get('gpu_path_fingerprint')
        if captured is not None:
            if not isinstance(captured, str) or not re.fullmatch(r'[0-9a-f]{64}', captured):
                return None
            if not current_path:
                current_path.append(_current_gpu_path_fingerprint(sensors))
            return 'matched_gpu_bus_topology_fingerprint' if captured == current_path[0] else None
        if len([card for card in machine.get('gpus') or [] if card.get('memory_architecture') != 'unified']) > 1:
            return None
        return 'unverified_legacy_single_gpu_capture'

    def consider(run, profile, snapshot, kind, session_id=None):
        if (not isinstance(profile, dict) or profile.get("protocol") != "wattever-transfer-v1" or
                _capture_hardware_key(snapshot) != current_key or validate_transfer_bandwidth(profile)):
            return
        if path_match(profile) is None:
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
        "gpu_path_match": path_match(original),
        "gpu_path_match_note": ('The observed GPU index/name/PCI bus mapping matches; physical connector names remain unverified.'
                                if original.get('gpu_path_fingerprint') else
                                'Legacy capture has no PCI bus topology fingerprint; physical path and slot matching are unverified.'),
        "matching_fields": ["cpu_name_and_core_counts", "ram_capacity_and_module_count",
                            "reported_ram_clocks", "all_gpu_names_memory_and_drivers",
                            "gpu_sensor_mapping_and_reported_link_widths"],
    }
    if session_id is not None:
        profile["saved_capture"]["source_session_id"] = session_id
    return profile


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason}


def storage_coverage(machine, profile):
    """Assess coverage from public inventory/capture only; never probe hardware.

    Inventory has no stable physical IDs, so even an equal number of measured
    disks is not proof that the same physical disks are still installed.
    """
    disks = (machine or {}).get('storage') or []
    detected = sum(1 for disk in disks if isinstance(disk, dict) and
                   str(disk.get('bus_type') or disk.get('interface') or '').lower() == 'nvme')
    discovery = (profile or {}).get('storage_discovery') or {}
    measured = discovery.get('nvme_measured', 0)
    measured = measured if type(measured) is int and measured >= 0 else 0
    status = 'incomplete' if detected > measured else 'unverified'
    return {'nvme_detected': detected, 'nvme_measured': measured, 'status': status}


def _json_readonly(args, timeout=8):
    result = pu.run(args, timeout=timeout)
    if result.returncode:
        raise OSError('Storage discovery failed')
    return json.loads(result.stdout)


def _nvme_discover_windows():
    # Disk numbers are used only to associate partitions, never as paths.
    script = ("$ErrorActionPreference='Stop'; @((Get-Disk | Where-Object {$_.BusType -eq 'NVMe'}) | ForEach-Object {"
              "$d=$_; $parts=@(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | ForEach-Object {"
              "$p=$_; $v=Get-Volume -Partition $p -ErrorAction SilentlyContinue; "
              "@{letter=[string]$p.DriveLetter; readonly=[bool]$d.IsReadOnly; offline=[bool]$d.IsOffline; "
              "health=[string]$v.HealthStatus; filesystem=[string]$v.FileSystem; driveType=[string]$v.DriveType}}); "
              "@{model=[string]$d.FriendlyName; partitions=$parts}}) | ConvertTo-Json -Depth 5 -Compress")
    disks = _json_readonly(['powershell', '-NoProfile', '-NonInteractive', '-Command', script])
    if isinstance(disks, dict):
        disks = [disks]
    found = []
    for disk in disks or []:
        parts = disk.get('partitions') or []
        if isinstance(parts, dict):
            parts = [parts]
        candidates = []
        for part in parts:
            letter = part.get('letter') or ''
            if not re.fullmatch(r'[A-Za-z]', letter):
                continue
            reason = ('Disk is offline' if part.get('offline') else 'Disk is read-only' if part.get('readonly') else
                      'Volume is not a local filesystem' if part.get('driveType') not in ('Fixed', '3') else
                      'Volume is locked or has no filesystem' if not part.get('filesystem') else None)
            candidates.append({'path': letter.upper() + ':\\', 'volume': letter.upper() + ':', 'reason': reason})
        found.append({'model': disk.get('model') or None, 'volumes': candidates})
    return found


def _nvme_discover_linux():
    data = _json_readonly(['lsblk', '--json', '--bytes', '--output', 'NAME,TYPE,TRAN,MODEL,RO,FSTYPE,MOUNTPOINTS'])
    found = []
    for disk in data.get('blockdevices') or []:
        if disk.get('type') != 'disk' or not (str(disk.get('tran') or '').lower() == 'nvme' or re.fullmatch(r'nvme\d+n\d+', str(disk.get('name') or ''))):
            continue
        volumes = []
        def walk(node):
            if node.get('type') not in ('disk', 'part'):
                return  # Layered/RAID mappings cannot establish a single physical device.
            mounts = node.get('mountpoints') or []
            if isinstance(mounts, str):
                mounts = [mounts]
            for mount in mounts:
                if isinstance(mount, str) and mount.startswith('/'):
                    fs = str(node.get('fstype') or '').lower()
                    reason = ('Volume is read-only' if node.get('ro') in (True, 1, '1') or disk.get('ro') in (True, 1, '1') else
                              'Volume is not a local filesystem' if fs in ('nfs', 'nfs4', 'cifs', 'smb3', '9p', 'fuse.sshfs') else
                              'Volume filesystem is unknown' if not fs else None)
                    volumes.append({'path': mount, 'volume': 'NVMe mounted filesystem', 'reason': reason})
            for child in node.get('children') or []:
                walk(child)
        walk(disk)
        found.append({'model': disk.get('model') or None, 'volumes': volumes})
    return found


def _nvme_discover_mac():
    deadline = time.monotonic() + 8
    def plist(args):
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(args, 8)
        result = pu.run(args, timeout=remaining)
        if result.returncode:
            raise OSError('Storage discovery failed')
        return plistlib.loads(result.stdout.encode() if isinstance(result.stdout, str) else result.stdout)
    listing = plist(['diskutil', 'list', '-plist'])
    found = []
    for disk in (listing.get('AllDisksAndPartitions') or [])[:16]:
        identifier = disk.get('DeviceIdentifier')
        if not identifier or not re.fullmatch(r'disk\d+', identifier):
            continue
        info = plist(['diskutil', 'info', '-plist', identifier])
        if str(info.get('BusProtocol') or '').lower() != 'nvme':
            continue
        volumes = []
        for partition in (disk.get('Partitions') or [])[:16]:
            part = partition.get('DeviceIdentifier')
            if not part or not re.fullmatch(r'disk\d+s\d+', part):
                continue
            detail = plist(['diskutil', 'info', '-plist', part])
            mount = detail.get('MountPoint')
            if mount and detail.get('Internal') is True and str(detail.get('BusProtocol') or '').lower() == 'nvme':
                volumes.append({'path': mount, 'volume': 'NVMe mounted filesystem',
                                'reason': None if detail.get('WritableVolume') is True else 'Volume is read-only or locked'})
        found.append({'model': info.get('MediaName') or None, 'volumes': volumes})
    return found


def discover_nvme():
    """Read-only discovery. Returned mount paths are private and must not be published."""
    try:
        if pu.IS_WINDOWS:
            return _nvme_discover_windows(), None
        if pu.IS_LINUX:
            return _nvme_discover_linux(), None
        if pu.IS_MAC:
            return _nvme_discover_mac(), None
        return [], 'NVMe discovery is unsupported on this platform'
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError, plistlib.InvalidFileException):
        return [], 'NVMe discovery could not complete'


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


def _pcie_during_copies(gpu_index, transfer):
    """Read the link while transfers run, outside the timed sample region."""
    from . import hardware
    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(hardware._nvidia_pcie, gpu_index)
        deadline = time.monotonic() + 6
        while not reading.done() and time.monotonic() < deadline:
            transfer()
        return reading.result(timeout=1)


def cuda_probe(gpu_index):
    from . import hardware
    sensors = power.list_gpus()
    selected = next((s for s in sensors if s.get("index") == gpu_index), None)
    if not selected or not selected.get("pci_bus_id"):
        return _unavailable("No NVIDIA sensor with a verified PCI bus mapping")
    before = hardware._nvidia_pcie(gpu_index)
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
        during = _pcie_during_copies(gpu_index, lambda: check(copy_htod(target, host, size)))
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
                        gpu_memory_bytes=int(selected['vram_total_mb'] * MIB) if selected.get('vram_total_mb') is not None else None,
                        pcie_before=before, pcie_during=during,
                        measurement_scope="pinned_host_to_device_on_installed_gpu_path",
                        physical_slot=None, physical_slot_source="unverified",
                        verified_readback=True,
                        readback_verified_bytes=1,
                        note="Pinned RAM to device memory on this installed GPU path; host wall time includes completed copies and synchronization. The during-transfer link snapshot is collected in untimed warmup. Device and slot both constrain this path; the physical slot is not identified. Separate microbenchmark, not observed inference traffic or VRAM internal bandwidth.")
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
    deadline = time.monotonic() + 62
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
              "scope": "benchmark_client", "unit": "bytes_per_second",
              "protocol": "wattever-transfer-v1"}
    primary_reason = extra_directories.get('primary_reason') if isinstance(extra_directories, dict) else None
    for key, call in (("storage_to_ram", lambda: _unavailable(primary_reason) if primary_reason else storage_probe(directory)),
                      ("ram_copy_single", lambda: memory_probe()),
                      ("ram_copy_parallel", lambda: memory_probe(min(8, max(1, (os.cpu_count() or 2) // 2))))):
        try:
            result[key] = call()
        except Exception as exc:
            # Public files never include driver diagnostics or local file paths.
            result[key] = _unavailable("Transfer probe could not complete (%s)" % type(exc).__name__)
    result['ram_to_vram_devices'] = []
    try:
        sensors = [sensor for sensor in power.list_gpus() if sensor.get('pci_bus_id') and type(sensor.get('index')) is int]
    except (OSError, ValueError, subprocess.SubprocessError):
        sensors = []
    fingerprint = _gpu_path_fingerprint(sensors)
    if fingerprint is not None:
        result['gpu_path_fingerprint'] = fingerprint
        result['gpu_path_fingerprint_method'] = 'sha256 of ordered GPU sensor index, name and normalized PCI bus path; not unique device IDs'
    # Probe sequentially so each context and all allocations are released before
    # the next card. The outer worker deadline bounds the whole capture.
    for sensor in sorted(sensors, key=lambda item: item['index']):
        metadata = {'gpu_sensor_index': sensor['index'], 'gpu_name': sensor.get('name'),
                    'gpu_memory_bytes': int(sensor['vram_total_mb'] * MIB) if sensor.get('vram_total_mb') is not None else None,
                    'measurement_scope': 'pinned_host_to_device_on_installed_gpu_path',
                    'pcie_before': None, 'pcie_during': None,
                    'physical_slot': None, 'physical_slot_source': 'unverified'}
        try:
            measured = cuda_probe(sensor['index'])
        except Exception as exc:
            measured = _unavailable('Transfer probe could not complete (%s)' % type(exc).__name__)
        result['ram_to_vram_devices'].append(dict(metadata, **measured))
    selected = next((item for item in result['ram_to_vram_devices'] if item['gpu_sensor_index'] == gpu_index), None)
    if selected is not None:
        result['ram_to_vram'] = copy.deepcopy(selected)
    elif sensors:
        result['ram_to_vram'] = _unavailable('The selected NVIDIA sensor is absent from the GPU inventory')
    else:
        try:
            result['ram_to_vram'] = cuda_probe(gpu_index)
        except Exception as exc:
            result['ram_to_vram'] = _unavailable('Transfer probe could not complete (%s)' % type(exc).__name__)
    if isinstance(extra_directories, dict):
        plan = extra_directories
        if plan.get('primary'):
            result['storage_to_ram'].update(plan['primary'])
        result['storage_volumes'] = []
        # Leave time for serialisation and process exit within the outer 75 s.
        for item in plan.get('items', []):
            metadata = item['metadata']
            if item.get('reason'):
                measured = _unavailable(item['reason'])
            elif time.monotonic() >= deadline:
                measured = _unavailable('Storage probe skipped because the capture time budget was exhausted')
            else:
                try:
                    child = subprocess.run([sys.executable, '-m', 'wattever.bandwidth', '--storage-worker', item['path']],
                                           timeout=min(10, max(0.1, deadline-time.monotonic())),
                                           capture_output=True, text=True, **pu.popen_kwargs())
                    if child.returncode:
                        raise ValueError('Storage worker failed')
                    measured = json.loads(child.stdout)
                    if measured.get('status') not in ('ok', 'unavailable'):
                        raise ValueError('Invalid storage worker result')
                except (OSError, ValueError, subprocess.SubprocessError):
                    measured = _unavailable('Storage probe failed or exceeded its time budget')
            result['storage_volumes'].append(dict(measured, **metadata))
        discovery = dict(plan.get('discovery') or {})
        nvme = [result['storage_to_ram']] + result['storage_volumes']
        discovery['nvme_measured'] = sum(1 for item in nvme if item.get('bus_type') == 'NVMe' and item.get('status') == 'ok')
        result['storage_discovery'] = discovery
    elif extra_directories:
        result["storage_volumes"] = []
        for folder in extra_directories:
            try:
                result["storage_volumes"].append(storage_probe(folder))
            except Exception as exc:
                result["storage_volumes"].append(dict(_unavailable("Storage probe could not complete (%s)" % type(exc).__name__),
                                                       volume=Path(folder).drive or "additional filesystem"))
    return result


def _volume_key(path):
    try:
        return ('device', os.stat(path).st_dev)
    except OSError:
        return None


def _directory_issue(path):
    if not os.path.isdir(path):
        return 'Volume is missing or unmounted'
    if pu.IS_WINDOWS:
        drive = Path(path).drive
        if not drive or ct.windll.kernel32.GetDriveTypeW(drive + '\\') != 3:
            return 'Volume is not a local fixed drive'
    else:
        try:
            if os.statvfs(path).f_flag & os.ST_RDONLY:
                return 'Volume is read-only'
        except (OSError, AttributeError):
            return 'Volume accessibility could not be verified'
        if pu.IS_LINUX:
            try:
                with open('/proc/self/mountinfo', encoding='utf-8') as stream:
                    matches = []
                    for line in stream:
                        fields = line.split()
                        sep = fields.index('-')
                        mount = fields[4].replace('\\040', ' ').replace('\\011', '\t').replace('\\134', '\\')
                        if path == mount or path.startswith(mount.rstrip('/') + '/'):
                            matches.append((len(mount), fields[sep+1]))
                    if matches and max(matches)[1].lower() in ('nfs', 'nfs4', 'cifs', 'smb3', '9p', 'fuse.sshfs'):
                        return 'Volume is not a local filesystem'
            except (OSError, ValueError, IndexError):
                return 'Volume filesystem could not be verified'
    return None


def _storage_plan(directory, directories, owned):
    disks, error = discover_nvme()
    plan = {'items': [], 'primary': None,
            'discovery': {'nvme_detected': len(disks), 'nvme_measured': 0,
                          'status': 'unavailable' if error else 'ok'}}
    if error:
        plan['discovery']['reason'] = error
    seen = {_volume_key(directory)} - {None}
    primary_key = _volume_key(directory)
    eligible = 0
    def add(path, metadata, reason=None):
        nonlocal eligible
        if len(plan['items']) >= MAX_STORAGE_VOLUMES:
            return
        if reason is None:
            reason = _directory_issue(path)
        key = _volume_key(path) if not reason else None
        if key is not None and key in seen:
            if key == primary_key and metadata.get('bus_type') == 'NVMe':
                plan['primary'] = metadata
            return
        if key is not None:
            seen.add(key)
        scratch = None
        if not reason:
            if eligible >= MAX_EXTRA_STORAGE_PROBES:
                reason = 'Storage probe skipped because the per-capture volume limit was reached'
            else:
                eligible += 1
                try:
                    scratch = owned.enter_context(tempfile.TemporaryDirectory(prefix='.transfer-', dir=path))
                except OSError:
                    reason = 'Volume is not writable or could not be accessed'
        plan['items'].append({'path': scratch, 'metadata': metadata, 'reason': reason})
    for disk in disks:
        metadata = {'bus_type': 'NVMe', 'device_model': disk.get('model'), 'volume': 'NVMe volume'}
        volumes = disk.get('volumes') or []
        if not volumes:
            add(None, metadata, 'No accessible mounted volume could be safely mapped to this NVMe device')
            continue
        selected = next((v for v in volumes if not v.get('reason') and not _directory_issue(v.get('path'))), volumes[0])
        metadata['volume'] = selected.get('volume') or 'NVMe volume'
        add(selected.get('path'), metadata, selected.get('reason'))
    for folder in directories or []:
        try:
            path = str(Path(folder).resolve())
            label = Path(path).drive or 'additional filesystem'
            add(path, {'volume': label})
        except (OSError, ValueError, TypeError):
            add(None, {'volume': 'additional filesystem'}, 'Additional volume could not be resolved')
    plan['discovery']['omitted'] = max(0, len(disks) - MAX_STORAGE_VOLUMES - (1 if plan['primary'] else 0))
    return plan


def measure(gpu_index=0, directory=None, directories=None):
    """Run before inference; bounded child process releases its CUDA context on exit."""
    global _latest, _latest_hardware_key
    directory = str(Path(directory or Path(__file__).resolve().parents[1] / "results").resolve())
    plan = None
    try:
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
            primary_reason = _directory_issue(directory)
        except OSError:
            primary_reason = 'Primary volume is not writable or could not be accessed'
        # The child owns only this freshly created directory. Timeout cleanup is
        # performed after subprocess.run has killed and reaped the child.
        with ExitStack() as owned:
            try:
                scratch = owned.enter_context(tempfile.TemporaryDirectory(prefix=".transfer-", dir=directory)) if not primary_reason else None
            except OSError:
                scratch = None
                primary_reason = 'Primary volume is not writable or could not be accessed'
            if scratch is None:
                scratch = owned.enter_context(tempfile.TemporaryDirectory(prefix=".transfer-"))
            plan = _storage_plan(directory, directories, owned)
            if primary_reason:
                plan['primary_reason'] = primary_reason
            args = [sys.executable, "-m", "wattever.bandwidth", "--worker", scratch, str(gpu_index)]
            if plan['items'] or plan['primary'] or primary_reason or plan['discovery']['nvme_detected'] or plan['discovery']['status'] != 'ok':
                args.append(json.dumps(plan))
            result = subprocess.run(args, timeout=75, capture_output=True, text=True,
                                    cwd=str(Path(__file__).resolve().parents[1]), **pu.popen_kwargs())
        if result.returncode:
            raise ValueError("Worker failed")
        profile = json.loads(result.stdout)
        if profile.get("protocol") != "wattever-transfer-v1":
            raise ValueError("Invalid worker result")
        # Keep compatibility state private. Reuse requires a fresh matching
        # CPU/RAM/clock/driver inventory, not merely the same selected GPU.
        from . import hardware
        try:
            _latest_hardware_key = _capture_hardware_key(hardware.inventory(), require_clocks=False)
        except (OSError, ValueError, subprocess.SubprocessError):
            _latest_hardware_key = None
        _latest = copy.deepcopy(profile)
        return profile
    except (OSError, ValueError, subprocess.SubprocessError):
        failure = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
                "scope": "benchmark_client", "protocol": "wattever-transfer-v1", "unit": "bytes_per_second",
                "storage_to_ram": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_to_vram": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_copy_single": _unavailable("Probe worker failed or exceeded 75 seconds"),
                "ram_copy_parallel": _unavailable("Probe worker failed or exceeded 75 seconds")}
        if plan:
            if plan.get('primary'):
                failure['storage_to_ram'].update(plan['primary'])
            failure['storage_volumes'] = [dict(_unavailable(item.get('reason') or 'Probe worker failed or exceeded 75 seconds'),
                                               **item['metadata']) for item in plan['items']]
            failure['storage_discovery'] = dict(plan['discovery'], nvme_measured=0)
        return failure


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


def _reboot_memory_match(old, new):
    """Verify a page-sized usable-RAM change against otherwise identical inventory."""
    old_key, new_key = _capture_hardware_key(old), _capture_hardware_key(new)
    if old_key is None or old_key != new_key:
        return None
    for key in ("motherboard", "os"):
        if not old.get(key) or old[key] != new.get(key):
            return None
    old_system, new_system = old.get("system") or {}, new.get("system") or {}
    before, after = old_system.get("memory_bytes"), new_system.get("memory_bytes")
    if type(before) is not int or type(after) is not int or min(before, after) <= 0:
        return None
    delta = after - before
    if not 0 < abs(delta) <= 65536 or delta % 4096:
        return None
    if {k:v for k,v in old_system.items() if k != "memory_bytes"} != {k:v for k,v in new_system.items() if k != "memory_bytes"}:
        return None
    # Require installed DIMM capacity, rather than the usable-RAM fallback in the key.
    if not (old.get("memory") or {}).get("installed_capacity_bytes") or not (new.get("memory") or {}).get("installed_capacity_bytes"):
        return None
    return {"method": "stable_inventory_with_page_sized_usable_ram_change",
            "usable_ram_delta_bytes": delta,
            "matching_fields": ["cpu_name_and_core_counts", "motherboard", "os", "system_model",
                                "installed_ram_capacity_and_module_count", "reported_ram_clocks",
                                "all_gpu_names_memory_and_drivers"],
            "note": "Usable physical RAM differs by at most 64 KiB; installed RAM and the listed inventory fields match. Historical inference conditions remain unverified."}


def backfill(run, current, profile):
    """Attach a later measurement only when recorded hardware/driver match."""
    old_inventory, new_inventory = run.get("hardware") or {}, current.get("hardware") or {}
    old_key, new_key = _capture_hardware_key(old_inventory), _capture_hardware_key(new_inventory)
    if old_key is not None and new_key is not None and old_key != new_key:
        raise ValueError("Hardware differs or cannot be verified; transfer metadata was not updated.")
    old_class = (run.get("classification") or {}).get("hardware_class")
    new_class = (current.get("classification") or {}).get("hardware_class")
    evidence = None
    if not old_class or not new_class or old_class != new_class:
        if old_class and new_class:
            evidence = _reboot_memory_match(old_inventory, new_inventory)
        if evidence is None:
            raise ValueError("Hardware differs or cannot be verified; transfer metadata was not updated.")
    if any((run.get("gpu") or {}).get(k) != (current.get("gpu") or {}).get(k)
           for k in ("name", "vram_total_mb", "driver")):
        raise ValueError("The selected GPU or driver differs; transfer metadata was not updated.")
    # Historical sessions retain their original measurement metadata.
    value = copy.deepcopy(profile)
    value.update(provenance="measured_later_on_matching_hardware",
                 historical_inference_conditions_verified=False)
    if evidence is not None:
        value["hardware_match"] = evidence
    run.setdefault("hardware", {})["transfer_bandwidth"] = value
    run.setdefault("hardware_measurement_history", []).append({"captured_at": value["captured_at"],
        "kind": "transfer_bandwidth", "provenance": value["provenance"],
        "hardware_snapshot": copy.deepcopy(current.get("hardware") or {}),
        "transfer_bandwidth": copy.deepcopy(value)})
    return value


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--storage-worker':
        try:
            print(json.dumps(storage_probe(sys.argv[2])))
        except Exception as exc:
            print(json.dumps(_unavailable('Storage probe could not complete (%s)' % type(exc).__name__)))
        raise SystemExit(0)
    if len(sys.argv) not in (4, 5) or sys.argv[1] != "--worker":
        raise SystemExit("Internal bandwidth worker requires a directory and GPU sensor index")
    print(json.dumps(_worker(sys.argv[2], int(sys.argv[3]), json.loads(sys.argv[4]) if len(sys.argv) == 5 else None)))
