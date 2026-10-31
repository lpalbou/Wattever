"""Platform differences, in one place.

The benchmark logic is identical everywhere; only three things are not:
subprocess flags, where machine identity comes from, and how power is read.
This module owns the first and is imported by whoever needs the others.
"""
import os
import ctypes
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

# Hides the console window a subprocess would otherwise flash on Windows.
# Passing it on POSIX is not merely ignored -- CPython raises
# ValueError("creationflags is only supported on Windows platforms") -- so it
# has to be applied conditionally rather than defensively.
CREATE_NO_WINDOW = 0x08000000


def popen_kwargs():
    return {"creationflags": CREATE_NO_WINDOW} if IS_WINDOWS else {}


def run(args, timeout=15, env=None, text=True):
    """subprocess.run with the platform's flags and no inherited console."""
    return subprocess.run(args, capture_output=True, text=text, timeout=timeout,
                          env=env, errors="replace" if text else None,
                          **popen_kwargs())


def popen(args, **kwargs):
    kwargs.update(popen_kwargs())
    return subprocess.Popen(args, **kwargs)


def sysctl(name, default=None):
    """Read one sysctl value. macOS/BSD only; returns default elsewhere."""
    if not IS_MAC:
        return default
    try:
        p = run(["sysctl", "-n", name], timeout=8)
    except (OSError, subprocess.SubprocessError):
        return default
    if p.returncode != 0:
        return default
    value = (p.stdout or "").strip()
    return value or default


def platform_label():
    if IS_WINDOWS:
        return "windows"
    if IS_MAC:
        return "macos"
    if IS_LINUX:
        return "linux"
    return sys.platform


def linux_cpu_times():
    """Return idle and total CPU ticks from /proc/stat, or None."""
    try:
        with open('/proc/stat', encoding='utf-8') as f:
            fields = f.readline().split()
        if not fields or fields[0] != 'cpu' or len(fields) < 5:
            return None
        ticks = [int(x) for x in fields[1:]]
        # Guest time is already included in user/nice.
        return ticks[3] + (ticks[4] if len(ticks) > 4 else 0), sum(ticks[:8])
    except (OSError, ValueError):
        return None


def mac_cpu_times():
    """Actual host CPU ticks, rather than load average (which includes waits)."""
    try:
        lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        ticks = (ctypes.c_uint32 * 4)()
        count = ctypes.c_uint32(4)
        host = lib.mach_host_self()
        if lib.host_statistics(host, 3, ctypes.byref(ticks), ctypes.byref(count)):
            return None
        return ticks[2], sum(ticks)
    except (OSError, AttributeError):
        return None


def system_memory():
    """Host memory use without subprocesses, credentials or process identities.

    Linux uses MemAvailable, Windows uses GlobalMemoryStatusEx. macOS estimates
    available pages as free + inactive, so its provenance states that boundary.
    None means the OS did not supply a trustworthy capacity/availability pair.
    """
    try:
        if IS_WINDOWS:
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length', ctypes.c_uint32), ('load', ctypes.c_uint32)] + [
                    (name, ctypes.c_uint64) for name in
                    ('total_phys', 'avail_phys', 'total_page', 'avail_page', 'total_virtual', 'avail_virtual', 'extended')]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            total, available = status.total_phys, status.avail_phys
            source = 'GlobalMemoryStatusEx:physical_total_minus_available'
        elif IS_LINUX:
            with open('/proc/meminfo', encoding='ascii') as handle:
                values = {}
                for line in handle:
                    key, _, value = line.partition(':')
                    if key in ('MemTotal', 'MemAvailable'):
                        values[key] = int(value.split()[0]) * 1024
            total, available = values.get('MemTotal'), values.get('MemAvailable')
            source = '/proc/meminfo:MemTotal_minus_MemAvailable'
        elif IS_MAC:
            lib = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
            capacity = ctypes.c_uint64()
            length = ctypes.c_size_t(ctypes.sizeof(capacity))
            if lib.sysctlbyname(b'hw.memsize', ctypes.byref(capacity), ctypes.byref(length), None, 0):
                return None
            host = lib.mach_host_self()
            pagesize = ctypes.c_uint32()
            stats = (ctypes.c_uint32 * 15)()
            count = ctypes.c_uint32(15)
            if lib.host_page_size(host, ctypes.byref(pagesize)) or lib.host_statistics(host, 2, ctypes.byref(stats), ctypes.byref(count)):
                return None
            total, available = capacity.value, (stats[0] + stats[2]) * pagesize.value
            source = 'host_statistics:total_minus_free_and_inactive_pages_estimate'
        else:
            return None
        if not total or available is None or not 0 <= available <= total:
            return None
        used = total - available
        return {'total_bytes': total, 'used_bytes': used, 'percent': 100.0 * used / total, 'source': source}
    except (OSError, ValueError, IndexError, AttributeError):
        return None
