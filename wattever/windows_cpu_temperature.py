"""Windows CPU temperature from sensors that are readable without elevation.

Windows has no unprivileged API for the CPU package sensor (Intel/AMD keep it
in model-specific registers that need a kernel driver). Wattever does not ship
or install one. It reads, in order of trust:

  1. HWiNFO shared memory ("Shared Memory Support" enabled), CPU package/Tctl.
  2. LibreHardwareMonitor's local web server (http://127.0.0.1:8085), CPU package/Tctl.
  3. Core Temp shared memory: the hottest core, not a package sensor.
  4. ACPI thermal zone performance counters: a firmware zone, not a CPU package
     sensor, reported only after it has been seen to change (many boards
     report a fixed placeholder).

Those tools load their own drivers; when none is running and the firmware
exposes no live thermal zone, CPU temperature stays unavailable and the
reason is reported. Nothing is estimated.

Sources are polled on a private daemon thread about once a second, so the
sampler's tick only copies a cached reading and can never block on I/O.
"""
import ctypes
import json
import math
import re
import struct
import threading
import time
import urllib.request

FRESH_S = 5.0
REFRESH_S = 1.0
IDLE_EXIT_S = 30.0
UNAVAILABLE_NOTE = ('No CPU temperature sensor is readable without elevation: the firmware exposes no live '
                    'ACPI thermal zone and no HWiNFO (shared memory), LibreHardwareMonitor (web server) '
                    'or Core Temp is running.')


def _sane(value):
    return isinstance(value, (int, float)) and math.isfinite(value) and -30 <= value <= 150


# --------------------------------------------------------------------------
# Parsers (pure; tested with fake buffers)
# --------------------------------------------------------------------------

HWINFO_NAME = 'Global\\HWiNFO_SENS_SM2'
HWINFO_SIGNATURE = 0x53695748  # 'HWiS'; 'DEAD' while HWiNFO is shutting down
HWINFO_HEADER = struct.Struct('<IIIqIIIIIII')  # signature, version, revision, poll time, offsets/sizes/counts, polling ms
HWINFO_LABELS = ('CPU Package', 'CPU (Tctl/Tdie)', 'CPU Tctl/Tdie', 'CPU Die (average)')


def parse_hwinfo(buffer, now=None):
    """Return (celsius, label) for the CPU package reading in HWiNFO's shared memory, else None."""
    if len(buffer) < HWINFO_HEADER.size:
        return None
    (signature, _version, _revision, poll_time, _sensor_offset, _sensor_size, _sensor_count,
     reading_offset, reading_size, reading_count, polling_ms) = HWINFO_HEADER.unpack_from(buffer)
    if signature != HWINFO_SIGNATURE or reading_size < 300 or not 0 < reading_count <= 4096:
        return None
    # HWiNFO stops updating but may keep the mapping; a reading it no longer polls is not current.
    now = time.time() if now is None else now
    if poll_time and now - poll_time > max(10.0, 5 * (polling_ms or 2000) / 1000.0):
        return None
    found = {}
    for index in range(reading_count):
        start = reading_offset + index * reading_size
        if start + 300 > len(buffer):
            break
        kind = struct.unpack_from('<I', buffer, start)[0]
        if kind != 1:  # SENSOR_TYPE_TEMP
            continue
        label = buffer[start + 12:start + 140].split(b'\0', 1)[0].decode('latin-1').strip()
        if label not in HWINFO_LABELS or label in found:
            continue
        unit = buffer[start + 268:start + 284].split(b'\0', 1)[0].decode('latin-1')
        value = struct.unpack_from('<d', buffer, start + 284)[0]
        if 'F' in unit:
            value = (value - 32) * 5.0 / 9.0
        if _sane(value):
            found[label] = value
    for label in HWINFO_LABELS:
        if label in found:
            return found[label], label
    return None


CORETEMP_NAME = 'CoreTempMappingObject'
CORETEMP_SIZE = 2686


def parse_coretemp(buffer):
    """Return (celsius, detail) for the hottest core in Core Temp's shared memory, else None."""
    if len(buffer) < CORETEMP_SIZE:
        return None
    cores, cpus = struct.unpack_from('<II', buffer, 1536)
    if not 0 < cores <= 128 or not 0 < cpus <= 8 or cores * cpus > 256:
        return None
    tjmax = struct.unpack_from('<128I', buffer, 1024)
    temps = struct.unpack_from('<256f', buffer, 1544)
    fahrenheit, delta = buffer[2684], buffer[2685]
    values = []
    for cpu in range(cpus):
        for core in range(cores):
            value = temps[cpu * cores + core]
            if delta:  # distance to TjMax rather than a temperature
                value = tjmax[cpu] - value
            if fahrenheit:
                value = (value - 32) * 5.0 / 9.0
            if _sane(value):
                values.append(value)
    return (max(values), 'hottest_core') if values else None


LHM_URL = 'http://127.0.0.1:8085/data.json'
LHM_LABELS = ('CPU Package', 'Core (Tctl/Tdie)', 'CPU (Tctl/Tdie)', 'Core (Tctl)', 'Core (Tdie)')
_NUMBER = re.compile(r'-?\d+(?:[.,]\d+)?')


def parse_lhm(document):
    """Return (celsius, label) for the CPU package in LibreHardwareMonitor's data.json tree, else None."""
    found = {}
    stack, seen = [(document, False)], 0
    while stack and seen < 20000:
        node, in_cpu = stack.pop()
        seen += 1
        if not isinstance(node, dict):
            continue
        text = str(node.get('Text') or '')
        sensor_id = str(node.get('SensorId') or '')
        in_cpu = in_cpu or str(node.get('ImageURL') or '').endswith('cpu.png') or '/intelcpu/' in sensor_id or '/amdcpu/' in sensor_id
        if text in LHM_LABELS and in_cpu and text not in found:
            raw = str(node.get('Value') or '')
            match = _NUMBER.search(raw)
            if match and ('°C' in raw or '°F' in raw):
                value = float(match.group(0).replace(',', '.'))
                if '°F' in raw:
                    value = (value - 32) * 5.0 / 9.0
                if _sane(value):
                    found[text] = value
        for child in node.get('Children') or []:
            stack.append((child, in_cpu))
    for label in LHM_LABELS:
        if label in found:
            return found[label], label
    return None


class AcpiZoneFilter:
    """Withhold a thermal zone until it has changed: fixed placeholder zones are common."""

    def __init__(self):
        self.first = {}
        self.moved = set()

    def choose(self, zones):
        """zones: {instance: celsius}. Return (celsius, instance) or None."""
        live = {}
        for name, value in zones.items():
            if not _sane(value):
                continue
            first = self.first.setdefault(name, value)
            if abs(value - first) >= 0.1:
                self.moved.add(name)
            if name in self.moved:
                live[name] = value
        if not live:
            return None
        cpu = [name for name in live if 'CPU' in name.upper()]
        name = max(cpu or live, key=lambda key: live[key])
        return live[name], name


# --------------------------------------------------------------------------
# Windows I/O
# --------------------------------------------------------------------------

def _read_mapping(name, limit=16 << 20):
    """Copy a named read-only file mapping, bounded by the mapped region; None when absent."""
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenFileMappingW.restype = wintypes.HANDLE
    kernel32.OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.MapViewOfFile.restype = ctypes.c_void_p
    kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.VirtualQuery.restype = ctypes.c_size_t
    kernel32.VirtualQuery.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]

    class MemoryInfo(ctypes.Structure):
        _fields_ = [('BaseAddress', ctypes.c_void_p), ('AllocationBase', ctypes.c_void_p),
                    ('AllocationProtect', wintypes.DWORD), ('PartitionId', wintypes.WORD),
                    ('RegionSize', ctypes.c_size_t), ('State', wintypes.DWORD),
                    ('Protect', wintypes.DWORD), ('Type', wintypes.DWORD)]

    handle = kernel32.OpenFileMappingW(0x0004, False, name)  # FILE_MAP_READ
    if not handle:
        return None
    try:
        view = kernel32.MapViewOfFile(handle, 0x0004, 0, 0, 0)
        if not view:
            return None
        try:
            info = MemoryInfo()
            if not kernel32.VirtualQuery(view, ctypes.byref(info), ctypes.sizeof(info)):
                return None
            return ctypes.string_at(view, min(int(info.RegionSize), limit))
        finally:
            kernel32.UnmapViewOfFile(view)
    finally:
        kernel32.CloseHandle(handle)


class _PdhThermalZones:
    """\\Thermal Zone Information(*)\\High Precision Temperature, in tenths of a kelvin."""
    PATH = '\\Thermal Zone Information(*)\\High Precision Temperature'

    def __init__(self):
        from ctypes import wintypes
        self.wintypes = wintypes
        self.pdh = ctypes.WinDLL('pdh')
        self.query, self.counter = ctypes.c_void_p(), ctypes.c_void_p()
        if self.pdh.PdhOpenQueryW(None, 0, ctypes.byref(self.query)) != 0:
            raise OSError('PdhOpenQuery failed')
        if self.pdh.PdhAddEnglishCounterW(self.query, ctypes.c_wchar_p(self.PATH), 0, ctypes.byref(self.counter)) != 0:
            self.close()
            raise OSError('thermal zone counter unavailable')

    def read(self):
        wintypes = self.wintypes

        class Value(ctypes.Structure):
            _fields_ = [('CStatus', wintypes.DWORD), ('doubleValue', ctypes.c_double)]

        class Item(ctypes.Structure):
            _fields_ = [('szName', ctypes.c_wchar_p), ('FmtValue', Value)]

        if self.pdh.PdhCollectQueryData(self.query) != 0:
            return {}
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x00000200, ctypes.byref(size), ctypes.byref(count), None)
        if status & 0xFFFFFFFF != 0x800007D2 or not size.value:  # PDH_MORE_DATA; anything else means no instances
            return {}
        buffer = (ctypes.c_byte * size.value)()
        if self.pdh.PdhGetFormattedCounterArrayW(self.counter, 0x00000200, ctypes.byref(size), ctypes.byref(count), buffer) != 0:
            return {}
        items = ctypes.cast(buffer, ctypes.POINTER(Item))
        zones = {}
        for index in range(min(count.value, 64)):
            item = items[index]
            if item.FmtValue.CStatus in (0, 1):  # PDH_CSTATUS_VALID_DATA / NEW_DATA
                zones[str(item.szName or 'zone%d' % index)] = item.FmtValue.doubleValue / 10.0 - 273.15
        return zones

    def close(self):
        if self.query:
            self.pdh.PdhCloseQuery(self.query)
            self.query = ctypes.c_void_p()


# --------------------------------------------------------------------------
# Readers: read() -> (celsius, source) or None; may raise, the poller catches.
# --------------------------------------------------------------------------

class HwinfoReader:
    retry_s = 5.0

    def __init__(self, load=None):
        self.load = load or (lambda: _read_mapping(HWINFO_NAME))

    def read(self):
        buffer = self.load()
        result = parse_hwinfo(buffer) if buffer else None
        return (result[0], 'windows_hwinfo_shared_memory:%s' % result[1]) if result else None


class LibreHardwareMonitorReader:
    retry_s = 15.0

    def __init__(self, fetch=None):
        self.fetch = fetch or self._fetch

    @staticmethod
    def _fetch():
        # Loopback only, no proxy: a port another program holds must not hang or leak anything.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(LHM_URL, timeout=0.5) as response:
            return json.loads(response.read(4 << 20).decode('utf-8', 'replace'))

    def read(self):
        result = parse_lhm(self.fetch())
        return (result[0], 'windows_librehardwaremonitor_web:%s' % result[1]) if result else None


class CoreTempReader:
    retry_s = 5.0

    def __init__(self, load=None):
        self.load = load or (lambda: _read_mapping(CORETEMP_NAME))

    def read(self):
        buffer = self.load()
        result = parse_coretemp(buffer) if buffer else None
        return (result[0], 'windows_coretemp_shared_memory:%s' % result[1]) if result else None


class AcpiThermalZoneReader:
    def __init__(self, zones=None):
        self._zones, self._owned = zones, zones is None
        self.filter = AcpiZoneFilter()
        self.retry_s = 60.0

    def read(self):
        if self._zones is None:
            self._zones = _PdhThermalZones()
        zones = self._zones.read()
        # Zones that exist are watched every couple of seconds so a change can be seen; none: check rarely.
        self.retry_s = 2.0 if zones else 60.0
        result = self.filter.choose(zones)
        return (result[0], 'windows_acpi_thermal_zone:%s' % result[1]) if result else None

    def close(self):
        if self._owned and self._zones is not None:
            zones, self._zones = self._zones, None
            zones.close()


def default_readers():
    return [HwinfoReader(), LibreHardwareMonitorReader(), CoreTempReader(), AcpiThermalZoneReader()]


class WindowsCpuTemperature:
    """Cached CPU temperature; sample() never performs I/O.

    `source` and `note` describe the value sample() last returned: a source only
    while its reading is fresh, otherwise a note saying why there is none.
    """

    def __init__(self, readers=None, clock=time.monotonic, background=True):
        self.readers = default_readers() if readers is None else list(readers)
        self.clock = clock
        self.background = background
        self.source = None
        self.note = 'Looking for a CPU temperature sensor.'
        self._value = self._at = self._source = None
        self._note = self.note
        self._retry = {}
        self._last_sample = clock()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def refresh(self):
        """One pass over the sources, best first. A source that has nothing backs off."""
        now = self.clock()
        for reader in self.readers:
            if self._retry.get(id(reader), 0) > now:
                continue
            try:
                result = reader.read()
            except Exception:  # noqa: BLE001 - a broken third-party source must not stop sampling
                result = None
            if result and _sane(result[0]):
                with self._lock:
                    self._value, self._source, self._at, self._note = float(result[0]), result[1], now, None
                return result
            self._retry[id(reader)] = now + getattr(reader, 'retry_s', 5.0)
        with self._lock:
            self._value = self._at = self._source = None
            self._note = UNAVAILABLE_NOTE
        return None

    def _run(self):
        try:
            while not self._stop.is_set() and self.clock() - self._last_sample <= IDLE_EXIT_S:
                self.refresh()
                self._stop.wait(REFRESH_S)
        finally:
            # A later sample() restarts the poller; reader state (e.g. the ACPI change filter) is kept.
            for reader in self.readers:
                close = getattr(reader, 'close', None)
                if close:
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass

    def sample(self):
        self._last_sample = now = self.clock()
        if self.background and not self._stop.is_set() and (self._thread is None or not self._thread.is_alive()):
            self._thread = threading.Thread(target=self._run, name='wattever-cpu-temperature', daemon=True)
            self._thread.start()
        with self._lock:
            fresh = self._value is not None and self._at is not None and now - self._at <= FRESH_S
            self.source = self._source if fresh else None
            self.note = None if fresh else self._note if self._value is None else 'The CPU temperature reading is stale.'
            return self._value if fresh else None

    def close(self):
        self._stop.set()
