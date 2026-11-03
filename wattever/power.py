"""GPU + system power telemetry.

Two backends behind one interface, chosen by platform:

  * **nvidia-smi** (Windows/Linux + NVIDIA): board power for the card alone,
    plus utilisation, VRAM, clocks and temperature.
  * **powermetrics** (macOS): SoC-domain power -- GPU, CPU and ANE rails read
    separately. Needs root, so it is strictly optional; without it the speed
    measurements still run and the wattage fields are simply empty.

The two measure *different boundaries*, which matters when comparing across
platforms: an NVIDIA figure is the card's board power and excludes the CPU,
while Apple's GPU rail excludes the memory the GPU is sharing with the CPU.
Every result therefore records which source produced it (`power.source`), and
the estimated whole-machine figure is the one to compare across platforms.

Whatever is not measured is *estimated* and labelled as such, never silently
mixed in.
"""
import ctypes
import datetime
import math
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from itertools import islice

from .platform_util import IS_LINUX, IS_MAC, IS_WINDOWS, linux_cpu_times, mac_cpu_times, popen, run, sysctl, system_memory

SMI_FIELDS = ["power.draw", "utilization.gpu", "memory.used", "clocks.sm", "temperature.gpu"]

# sample row indices
T, GPU_W, GPU_UTIL, MEM_MB, CLOCK, TEMP, CPU_UTIL, CPU_W = range(8)

SRC_NVIDIA = "nvidia-smi"
SRC_POWERMETRICS = "powermetrics"
SRC_NONE = "none"


def _f(tok):
    """nvidia-smi prints [N/A] / [Not Supported] for fields a card cannot report."""
    tok = (tok or "").strip()
    if not tok or tok.startswith("["):
        return None
    try:
        value = float(tok)
        return value if math.isfinite(value) else None
    except ValueError:
        return None


# --------------------------------------------------------------------------
# NVIDIA
# --------------------------------------------------------------------------

def _nvidia_gpu_info(index=0):
    q = "uuid,name,memory.total,driver_version,power.limit,pcie.link.gen.max,compute_cap"
    try:
        p = run(["nvidia-smi", "--id=%d" % index, "--query-gpu=" + q,
                 "--format=csv,noheader,nounits"])
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    if p.returncode != 0 or not p.stdout.strip():
        return {}
    parts = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]
    parts += [""] * (7 - len(parts))
    return {
        "uuid": parts[0],
        "name": parts[1],
        "vram_total_mb": _f(parts[2]),
        "driver": parts[3],
        "power_limit_w": _f(parts[4]),
        "pcie_gen": parts[5],
        "compute_cap": parts[6],
        "power_readable": _f(parts[4]) is not None,
        "power_source": SRC_NVIDIA,
        "memory_architecture": "discrete",
        "vendor": "nvidia",
    }


def _nvidia_list():
    try:
        p = run(["nvidia-smi", "--query-gpu=index,name,memory.total,pci.bus_id",
                 "--format=csv,noheader,nounits"])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    out = []
    for line in p.stdout.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            try:
                index = int(parts[0])
            except ValueError:
                continue
            out.append({"index": index, "name": parts[1],
                        "vram_total_mb": _f(parts[2]),
                        "pci_bus_id": parts[3] if len(parts) > 3 else None})
    return out


# --------------------------------------------------------------------------
# Apple Silicon / macOS
# --------------------------------------------------------------------------

PM_PATTERNS = {
    "gpu_mw": re.compile(r"^GPU Power:\s*([\d.]+)\s*mW", re.M),
    "cpu_mw": re.compile(r"^CPU Power:\s*([\d.]+)\s*mW", re.M),
    "ane_mw": re.compile(r"^ANE Power:\s*([\d.]+)\s*mW", re.M),
    "combined_mw": re.compile(r"^Combined Power \([^)]*\):\s*([\d.]+)\s*mW", re.M),
    # Intel Macs report a single derived package figure, in watts
    "package_w": re.compile(r"package power[^:]*:\s*([\d.]+)\s*W", re.I | re.M),
    "gpu_residency": re.compile(r"^GPU HW active residency:\s*([\d.]+)%", re.M),
    "gpu_freq": re.compile(r"^GPU HW active frequency:\s*([\d.]+)\s*MHz", re.M),
}


def powermetrics_probe(timeout=12):
    """Can we read power without prompting for a password?

    `sudo -n` never prompts: it fails immediately unless credentials are already
    cached (which the macOS launcher arranges) or a NOPASSWD rule exists. That
    keeps power measurement genuinely optional instead of blocking a benchmark
    on an invisible password prompt.
    """
    if not IS_MAC:
        return False, "not macOS"
    try:
        p = run(["sudo", "-n", "powermetrics", "--samplers", "gpu_power",
                 "-i", "100", "-n", "1"], timeout=timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip().splitlines()
        return False, (detail[-1] if detail else "powermetrics exited %d" % p.returncode)
    if PM_PATTERNS["gpu_mw"].search(p.stdout or ""):
        return True, "ok"
    return False, "powermetrics produced no GPU power reading"


_mac_info_cache = None


def _mac_gpu_info():
    """Cached: system_profiler takes a second or two and the sudo probe spawns a
    process, while all of this is static for the life of the server."""
    global _mac_info_cache
    if _mac_info_cache is not None:
        return dict(_mac_info_cache)
    _mac_info_cache = _mac_gpu_info_uncached()
    return dict(_mac_info_cache)


def _mac_gpu_info_uncached():
    chip = sysctl("machdep.cpu.brand_string") or "Apple Silicon"
    model = sysctl("hw.model") or ""
    memsize = sysctl("hw.memsize")
    total_mb = (float(memsize) / (1024 ** 2)) if memsize and memsize.isdigit() else None
    cores = None
    name = chip
    try:
        p = run(["system_profiler", "-json", "SPDisplaysDataType"], timeout=30)
        if p.returncode == 0:
            import json
            entries = (json.loads(p.stdout or "{}").get("SPDisplaysDataType") or [])
            if entries:
                name = entries[0].get("sppci_model") or chip
                raw = entries[0].get("sppci_cores") or entries[0].get("spdisplays_cores")
                if raw:
                    digits = re.sub(r"[^0-9]", "", str(raw))
                    cores = int(digits) if digits else None
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    ok, why = powermetrics_probe()
    return {
        "uuid": model or name,
        "name": name,
        # Unified memory: there is no separate VRAM pool, so the figure that
        # answers "how big a model can this hold" is the system total. Flagged
        # as unified so nothing compares it to a discrete card's VRAM blindly.
        "vram_total_mb": total_mb,
        "driver": sysctl("kern.osproductversion") or "",
        "power_limit_w": None,
        "pcie_gen": "",
        "compute_cap": ("%d-core GPU" % cores) if cores else "",
        "power_readable": ok,
        "power_source": SRC_POWERMETRICS if ok else SRC_NONE,
        "power_note": None if ok else why,
        "memory_architecture": "unified",
        "vendor": "apple",
        "model_identifier": model,
    }


def _mac_list():
    info = _mac_gpu_info()
    if not info.get("name"):
        return []
    return [{"index": 0, "name": info["name"], "vram_total_mb": info.get("vram_total_mb")}]


# --------------------------------------------------------------------------
# public surface
# --------------------------------------------------------------------------

def gpu_info(index=0):
    """Static identity of the accelerator under test. {} when there is none."""
    info = _nvidia_gpu_info(index)
    if info:
        return info
    if IS_MAC and index == 0:
        return _mac_gpu_info()
    return {}


def list_gpus():
    found = _nvidia_list()
    if found:
        return found
    return _mac_list() if IS_MAC else []


class _CpuMeter:
    """Whole-machine CPU utilisation, no third-party deps.

    Windows: GetSystemTimes. Linux: /proc/stat. macOS: host CPU ticks. This is
    host-wide utilization, not utilization attributable to model inference.
    """

    def __init__(self):
        self._prev = None

    @staticmethod
    def _times():
        idle, kern, user = ctypes.c_ulonglong(), ctypes.c_ulonglong(), ctypes.c_ulonglong()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
        if not ok:
            return None
        # kernel time already includes idle time
        return idle.value, kern.value + user.value

    def sample(self):
        if IS_WINDOWS or IS_LINUX or IS_MAC:
            try:
                cur = self._times() if IS_WINDOWS else mac_cpu_times() if IS_MAC else linux_cpu_times()
            except (AttributeError, OSError):
                return None
            if cur is None:
                return None
            prev, self._prev = self._prev, cur
            if prev is None:
                return None
            d_idle, d_tot = cur[0] - prev[0], cur[1] - prev[1]
            if d_tot <= 0:
                return None
            return max(0.0, min(1.0, 1.0 - d_idle / d_tot))
        return None


class _CpuTemperature:
    """Read an actual Linux CPU package sensor when exposed, without elevation."""
    def __init__(self):
        self.path = None
        self.source = None
        if not IS_LINUX:
            return
        try:
            for folder in islice(Path('/sys/class/hwmon').glob('hwmon*'), 32):
                name = (folder / 'name').read_text(encoding='ascii').strip()
                if name not in ('coretemp', 'k10temp', 'zenpower'):
                    continue
                for label in islice(folder.glob('temp*_label'), 32):
                    description = label.read_text(encoding='ascii').strip()
                    if description.startswith('Package id') or description in ('Tctl', 'Tdie'):
                        self.path = label.with_name(label.name.replace('_label', '_input'))
                        self.source = 'linux_hwmon:%s:%s' % (name, description)
                        return
        except OSError:
            pass

    def sample(self):
        if self.path is None:
            return None
        try:
            value = float(self.path.read_text(encoding='ascii').strip()) / 1000.0
            return value if math.isfinite(value) and -30 <= value <= 150 else None
        except (OSError, ValueError):
            return None


class PowerSampler:
    """Background sampler. Any time window can be queried after the fact.

    One long-lived child process streams GPU readings. A separate bounded host
    sampler uses OS APIs for CPU/RAM, including when GPU sensors are unavailable.
    Monitoring itself has some overhead and is part of the observed host load.
    """

    def __init__(self, interval_ms=150, gpu_index=0, other_idle_w=60.0, cpu_tdp_w=140.0):
        self.interval_ms = max(50, int(interval_ms))
        self.gpu_index = gpu_index
        self.other_idle_w = float(other_idle_w)
        self.cpu_tdp_w = float(cpu_tdp_w)
        self.samples = deque(maxlen=72000)
        self.source = SRC_NONE
        self._proc = None
        self._thread = None
        self._backend_thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.error = None
        self._started_perf = time.perf_counter()
        self._started_utc = datetime.datetime.now(datetime.timezone.utc)
        self._ended_perf = None
        self._vram_total_mb = None
        self._sensor_row = None
        self._history = deque(maxlen=3600)
        self._system_samples = deque(maxlen=72000)
        self._statistics = {}
        self._observations = 0
        self._previous_point = None
        self._first_point = None
        self._latest_point = None
        self._gpu_meters = {}
        self._sensor_rows = {}
        self._device_name = None
        self._scope = 'benchmark_client_selected_gpu'
        self._inventory_complete = False

    def _configure_gpus(self, sensors):
        """Freeze the monitored NVIDIA inventory; child meters never start tools."""
        indices=[sensor.get('index') for sensor in sensors]
        if any(type(index) is not int or not 0 <= index <= 255 for index in indices) or len(set(indices)) != len(indices):
            raise ValueError('Invalid GPU sensor inventory')
        self._scope = 'benchmark_client_all_nvidia_gpus'
        self._inventory_complete = bool(sensors)
        for sensor in sensors:
            index = sensor.get('index')
            if type(index) is not int or index in self._gpu_meters:
                raise ValueError('Invalid GPU sensor inventory')
            meter = PowerSampler(self.interval_ms, index)
            meter.source = SRC_NVIDIA
            meter._scope = 'benchmark_client_gpu'
            meter._device_name = sensor.get('name') or 'GPU %d' % index
            meter._vram_total_mb = sensor.get('vram_total_mb')
            meter._started_perf, meter._started_utc = self._started_perf, self._started_utc
            self._gpu_meters[index] = meter
        capacities = [meter._vram_total_mb for meter in self._gpu_meters.values()]
        self._vram_total_mb = sum(capacities) if capacities and all(value and value > 0 for value in capacities) else None

    def scope_metadata(self):
        return {'scope': self._scope, 'gpu_indices': sorted(self._gpu_meters) or [self.gpu_index],
                'gpu_count': len(self._gpu_meters) or 1, 'inventory_complete': self._inventory_complete,
                'gpu_power_aggregation': 'sum of all monitored GPU board power; missing readings make the total unavailable',
                'vram_aggregation': 'sum of physical used/capacity readings; separate per-card allocations',
                'gpu_util_aggregation': 'unweighted mean of per-card utilization percentages',
                'temperature_aggregation': 'maximum reported GPU temperature',
                'system_power_boundary': 'GPU board power plus CPU and other-system estimates; not a wall-meter measurement'}

    def measurement_scope(self):
        metadata = self.scope_metadata()
        return {'power_gpu_sensor_index': self.gpu_index,
                'power_gpu_sensor_indices': metadata['gpu_indices'],
                'power_boundary': 'all detected NVIDIA GPU boards; CPU/RAM are whole-client observations' if self._gpu_meters
                                  else 'selected GPU/SoC sensor; CPU/RAM are whole-client observations',
                'gpu_telemetry_scope': self._scope,
                'gpu_inventory_complete': self._inventory_complete,
                'system_power_boundary': metadata['system_power_boundary']}

    def _gpu_rows_at(self, now):
        with self._lock:
            snapshots = dict(self._sensor_rows)
        rows = {}
        for index in self._gpu_meters:
            snapshot = snapshots.get(index)
            fresh = snapshot is not None and 0 <= now - snapshot[T] <= max(1.0, self.interval_ms / 1000.0 * 4)
            rows[index] = (now,) + tuple(snapshot[1:] if fresh else (None,) * 7)
        return rows

    @staticmethod
    def _combined_gpu_row(now, rows):
        devices = list(rows.values())
        def complete(index):
            values = [row[index] for row in devices]
            return values if values and all(value is not None for value in values) else None
        watts, memory, utilization = complete(GPU_W), complete(MEM_MB), complete(GPU_UTIL)
        temperatures = [row[TEMP] for row in devices if row[TEMP] is not None]
        return (now, sum(watts) if watts else None,
                sum(utilization)/len(utilization) if utilization else None,
                sum(memory) if memory else None,
                devices[0][CLOCK] if len(devices) == 1 else None,
                max(temperatures) if temperatures else None, None, None)

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._thread:
            return
        self._stop.clear()
        self._started_perf = time.perf_counter()
        self._started_utc = datetime.datetime.now(datetime.timezone.utc)
        self._ended_perf = None
        info = gpu_info(self.gpu_index)
        self._vram_total_mb = info.get('vram_total_mb')
        if info.get("power_source") == SRC_POWERMETRICS:
            target = self._loop_powermetrics
            self.source = SRC_POWERMETRICS
        elif info.get("vendor") == "nvidia" or not IS_MAC:
            target = self._loop_nvidia
            self.source = SRC_NVIDIA
            if info.get('vendor') == 'nvidia':
                try:
                    sensors = list_gpus()
                except (OSError, ValueError, subprocess.SubprocessError):
                    sensors = []
                if sensors:
                    self._configure_gpus(sensors)
                else:
                    self.error = 'GPU inventory unavailable; complete multi-GPU totals cannot be verified.'
        else:
            # macOS without usable powermetrics: no wattage, speed still measured
            self.error = info.get("power_note") or "power measurement unavailable"
            self.source = SRC_NONE
            target = None
        if target is not None:
            self._backend_thread = threading.Thread(target=target, daemon=True)
            self._backend_thread.start()
        self._thread = threading.Thread(target=self._loop_monitor, daemon=True)
        self._thread.start()

    def _loop_monitor(self):
        cpu, temperature = _CpuMeter(), _CpuTemperature()
        cpu.sample()
        while not self._stop.is_set():
            now = time.perf_counter()
            gpu_rows = self._gpu_rows_at(now) if self._gpu_meters else None
            if gpu_rows is not None:
                values = self._combined_gpu_row(now, gpu_rows)[1:]
            else:
                with self._lock:
                    sensor = self._sensor_row
                fresh = sensor is not None and now - sensor[T] <= max(2.0, self.interval_ms / 1000.0 * 4)
                values = sensor[1:] if fresh else (None,) * 7
            row = (now,) + tuple(values[:5]) + (cpu.sample(), values[6])
            memory = system_memory() or {}
            if gpu_rows is None:
                self._append(row, memory, temperature.sample(), temperature.source)
            else:
                self._append(row, memory, temperature.sample(), temperature.source, gpu_rows=gpu_rows)
            self._stop.wait(self.interval_ms / 1000.0)

    def _record_sensor(self, row, gpu_index=None):
        with self._lock:
            if gpu_index is None:
                self._sensor_row = row
            elif gpu_index in self._gpu_meters:
                self._sensor_rows[gpu_index] = row

    def _point(self, row, memory=None, cpu_temp=None, cpu_temp_source=None):
        memory = memory or {}
        elapsed = max(0.0, row[T] - self._started_perf)
        vram_pct = 100.0 * row[MEM_MB] / self._vram_total_mb if row[MEM_MB] is not None and self._vram_total_mb else None
        point = {'elapsed_s': elapsed,
                'timestamp': (self._started_utc + datetime.timedelta(seconds=elapsed)).isoformat(),
                'gpu_w': row[GPU_W], 'gpu_util': row[GPU_UTIL], 'vram_mb': row[MEM_MB],
                'vram_total_mb': self._vram_total_mb, 'vram_percent': vram_pct,
                'clock_mhz': row[CLOCK], 'temp_c': row[TEMP], 'cpu_util': row[CPU_UTIL],
                'cpu_util_percent': row[CPU_UTIL] * 100.0 if row[CPU_UTIL] is not None else None,
                'cpu_w': row[CPU_W], 'cpu_temp_c': cpu_temp, 'cpu_temp_source': cpu_temp_source,
                'system_memory_used_bytes': memory.get('used_bytes'),
                'system_memory_total_bytes': memory.get('total_bytes'),
                'system_memory_percent': memory.get('percent'),
                'system_memory_source': memory.get('source'),
                'system_w_est': self._system_w(row[GPU_W], row[CPU_UTIL], row[CPU_W]),
                'source': self.source, 'scope': self._scope}
        if self._device_name is not None:
            point.update(index=self.gpu_index, name=self._device_name)
        return point

    def _append(self, row, memory=None, cpu_temp=None, cpu_temp_source=None, gpu_rows=None):
        if gpu_rows is not None:
            for index, values in gpu_rows.items():
                self._gpu_meters[index]._append(values)
        point = self._point(row, memory, cpu_temp, cpu_temp_source)
        if self._gpu_meters:
            point['gpus'] = [meter.now() for meter in self._gpu_meters.values()]
            point['gpu_power_complete'] = self._inventory_complete and all(gpu and gpu['gpu_w'] is not None for gpu in point['gpus'])
            point['gpu_memory_complete'] = self._inventory_complete and all(gpu and gpu['vram_mb'] is not None for gpu in point['gpus'])
            point['gpu_count'] = len(self._gpu_meters)
        fields = ('gpu_w', 'gpu_util', 'vram_mb', 'vram_percent', 'clock_mhz', 'temp_c',
                  'cpu_util_percent', 'cpu_w', 'cpu_temp_c', 'system_memory_used_bytes',
                  'system_memory_percent', 'system_w_est')
        with self._lock:
            self.samples.append(row)
            # Tests/older callers can supply a list instead of the bounded deque.
            if isinstance(self.samples, list) and len(self.samples) > 72000:
                del self.samples[:-72000]
            self._observations += 1
            self._history.append(point)
            self._system_samples.append((row[T], point['system_memory_used_bytes'], point['system_memory_total_bytes'],
                                         point['system_memory_percent'], point['vram_percent'], point['cpu_temp_c']))
            if self._first_point is None:
                self._first_point = point
            previous = self._previous_point
            for field in fields:
                value = point[field]
                if value is None or not math.isfinite(value):
                    continue
                stats = self._statistics.setdefault(field, {'count': 0, 'sum': 0.0, 'min': value, 'max': value,
                    'first': value, 'last': value, 'integral': 0.0, 'coverage_s': 0.0, 'early_sum': 0.0, 'early_count': 0})
                stats['count'] += 1
                stats['sum'] += value
                stats['min'], stats['max'], stats['last'] = min(stats['min'], value), max(stats['max'], value), value
                if point['elapsed_s'] <= 60:
                    stats['early_sum'] += value
                    stats['early_count'] += 1
                dt = point['elapsed_s'] - previous['elapsed_s'] if previous else 0
                if previous and previous.get(field) is not None and 0 < dt <= max(1.0, self.interval_ms / 1000.0 * 4):
                    stats['integral'] += (previous[field] + value) * 0.5 * dt
                    stats['coverage_s'] += dt
            self._previous_point = self._latest_point = point

    def _loop_nvidia(self):
        indices = sorted(self._gpu_meters) or [self.gpu_index]
        args = ["nvidia-smi", "--id=" + ','.join(str(index) for index in indices),
                "--query-gpu=" + ",".join(['index'] + SMI_FIELDS),
                "--format=csv,noheader,nounits", "-lms=%d" % self.interval_ms]
        try:
            self._proc = popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error = "nvidia-smi unavailable: %s" % exc
            self.source = SRC_NONE
            return
        if self._stop.is_set():
            self._proc.terminate()
            return
        try:
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                parts = [x.strip() for x in line.split(",")]
                if len(parts) != 6:
                    continue
                try:
                    index = int(parts[0])
                except ValueError:
                    continue
                row = (time.perf_counter(), *(_f(value) for value in parts[1:]), None, None)
                self._record_sensor(row, gpu_index=index if self._gpu_meters else None)
        except (OSError, ValueError):
            pass

    def _loop_powermetrics(self):
        """Parse streaming powermetrics blocks into samples.

        Readings arrive as repeated text blocks, each introduced by a
        "*** Sampled system activity" header, so a block is flushed when the
        next header arrives.
        """
        args = ["sudo", "-n", "powermetrics", "--samplers", "gpu_power,cpu_power",
                "-i", str(self.interval_ms)]
        try:
            self._proc = popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error = "powermetrics unavailable: %s" % exc
            self.source = SRC_NONE
            return
        if self._stop.is_set():
            self._proc.terminate()
            return
        block = []

        def flush(buf):
            if not buf:
                return
            text = "\n".join(buf)

            def grab(key):
                m = PM_PATTERNS[key].search(text)
                return float(m.group(1)) if m else None

            gpu_mw = grab("gpu_mw")
            cpu_mw = grab("cpu_mw")
            ane_mw = grab("ane_mw")
            combined_mw = grab("combined_mw")
            package_w = grab("package_w")
            gpu_w = (gpu_mw / 1000.0) if gpu_mw is not None else None
            cpu_w = (cpu_mw / 1000.0) if cpu_mw is not None else None
            if cpu_w is not None and ane_mw:
                cpu_w += ane_mw / 1000.0
            if gpu_w is None and package_w is not None:
                # Intel Mac: only a combined package figure exists. Report it in
                # the GPU slot would be wrong, so leave GPU empty and let the
                # package figure drive the system estimate via the CPU slot.
                cpu_w = package_w
            if combined_mw is not None and cpu_w is None and gpu_w is not None:
                cpu_w = max(0.0, combined_mw / 1000.0 - gpu_w)
            if gpu_w is None and cpu_w is None:
                return
            self._record_sensor((time.perf_counter(), gpu_w, grab("gpu_residency"), None,
                                 grab("gpu_freq"), None, None, cpu_w))

        try:
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                if line.startswith("*** Sampled system activity"):
                    flush(block)
                    block = []
                block.append(line.rstrip("\n"))
                if len(block) > 400:          # guard against an unbounded buffer
                    block = block[-200:]
            flush(block)
        except (OSError, ValueError):
            pass

    def stop(self):
        self._stop.set()
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        for worker in (self._thread, self._backend_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=1.0)
        self._ended_perf = time.perf_counter()
        for meter in self._gpu_meters.values():
            meter._ended_perf = self._ended_perf
        self._proc = None
        self._thread = None
        self._backend_thread = None

    def now(self):
        """Most recent reading, for the live UI gauge."""
        with self._lock:
            if not self.samples:
                return None
            row = self.samples[-1]
            point = dict(self._latest_point or self._point(row))
        point['sample_age_s'] = max(0.0, time.perf_counter() - row[T])
        return point

    def history(self):
        """Bounded, timestamped client telemetry. Ring retention is explicit."""
        with self._lock:
            points = [dict(point) for point in self._history]
            observations = self._observations
        return {'schema_version': 1, 'available': bool(points), **self.scope_metadata(),
                'started_at': self._started_utc.isoformat(), 'sampling_interval_ms': self.interval_ms,
                'gpu_sensor_index': self.gpu_index, 'source': self.source,
                'retention': {'policy': 'most_recent_ring', 'capacity_points': 3600,
                              'total_observations': observations, 'retained_points': len(points),
                              'dropped_points': max(0, observations - len(points)),
                              'retained_start_elapsed_s': points[0]['elapsed_s'] if points else None,
                              'retained_end_elapsed_s': points[-1]['elapsed_s'] if points else None,
                              'phase_raw_capacity_points': 72000},
                'note': 'Whole-client CPU/RAM and the declared GPU boundary; not inference-process isolation. All-card totals require every card reading. Driver snapshots are held only while fresh. CPU temperature needs an actual sensor. Summary preserves all observations despite ring eviction.',
                'points': points}

    def summary(self):
        """Whole-run observation statistics; drift alone does not diagnose throttling."""
        end = self._ended_perf if self._ended_perf is not None else time.perf_counter()
        duration = max(0.0, end - self._started_perf)
        with self._lock:
            stats = {key: dict(value) for key, value in self._statistics.items()}
            points = list(self._history)
            first = dict(self._first_point) if self._first_point else None
            last = dict(self._latest_point) if self._latest_point else None
            observations = self._observations
        metrics = {}
        for key, value in stats.items():
            late = [point[key] for point in points if point.get(key) is not None and last and point['elapsed_s'] >= last['elapsed_s'] - 60]
            early_mean = value['early_sum'] / value['early_count'] if value['early_count'] else None
            late_mean = sum(late) / len(late) if late else None
            span = value['coverage_s']
            metrics[key] = {'mean': value['sum'] / value['count'], 'min': value['min'], 'max': value['max'],
                            'count': value['count'], 'first': value['first'], 'last': value['last'],
                            'time_weighted_mean': value['integral'] / span if span else None,
                            'coverage_s': span, 'coverage_fraction': min(1.0, span / duration) if duration else None,
                            'early_60s_mean': early_mean, 'late_60s_mean': late_mean,
                            'late_minus_early': late_mean - early_mean if early_mean is not None and late_mean is not None else None}
        result = {'schema_version': 1, 'available': bool(observations), **self.scope_metadata(),
                'duration_s': duration, 'observations': observations, 'source': self.source,
                'start': first, 'end': last, 'metrics': metrics,
                'drift_note': 'Early/late windows can overlap on runs shorter than 120 seconds. Differences describe observed workloads/environment; they do not establish thermal throttling, sustained model speed or hardware capability.',
                'coverage_note': 'Time-weighted coverage excludes gaps longer than four sampling intervals or one second, whichever is larger. Peaks are maxima of observed samples, not guaranteed physical peaks. Ring eviction does not change all-run aggregates.'}
        energy = stats.get('gpu_w') or {}
        result.update(gpu_energy_sampled_j=energy.get('integral') if energy.get('coverage_s',0)>0 else None,
                      gpu_energy_coverage_s=energy.get('coverage_s',0),
                      energy_scope='Covered intervals of the whole run including idle, loading and requests; phase energy is recorded per result.')
        if self._device_name is not None:
            result.update(index=self.gpu_index, name=self._device_name)
        if self._gpu_meters:
            result['gpus'] = [meter.summary() for meter in self._gpu_meters.values()]
        return result

    def _system_w(self, gpu_w, cpu_util, cpu_w=None):
        """Estimated whole-machine draw.

        With a measured CPU rail (macOS) the estimate is GPU + CPU + a small
        constant for everything else. Otherwise the CPU side is inferred from
        its TDP scaled by measured load, which is the best Windows allows
        without extra drivers.
        """
        if cpu_w is not None:
            return (gpu_w or 0.0) + cpu_w + self.other_idle_w
        if gpu_w is None or cpu_util is None:
            return None
        return gpu_w + self.other_idle_w + self.cpu_tdp_w * cpu_util

    # -- analysis ----------------------------------------------------------
    def window(self, t0, t1):
        result = self._power_window(t0, t1)
        with self._lock:
            details = [row for row in self._system_samples if t0 <= row[0] <= t1]
            truncated = bool(self.samples and self._observations > len(self.samples) and self.samples[0][T] > t0)
        def values(index):
            return [row[index] for row in details if row[index] is not None]
        def average(index):
            collected = values(index)
            return sum(collected) / len(collected) if collected else None
        def peak(index):
            collected = values(index)
            return max(collected) if collected else None
        result.update(system_memory_used_bytes_max=peak(1), system_memory_total_bytes=peak(2),
                      system_memory_percent_avg=average(3), system_memory_percent_max=peak(3),
                      vram_percent_avg=average(4), vram_percent_max=peak(4),
                      cpu_temp_c_max=peak(5), telemetry_samples=len(details),
                      raw_retention_truncated=truncated)
        if truncated:
            result['low_confidence'] = True
            result['retention_note'] = 'Requested phase begins before retained raw samples; phase statistics are incomplete and energy extrapolation is uncertain.'
        result.update(scope=self._scope)
        if self._gpu_meters:
            result.update(gpu_indices=sorted(self._gpu_meters), inventory_complete=self._inventory_complete,
                          gpus=[dict(meter.window(t0,t1), index=index, name=meter._device_name)
                                for index,meter in self._gpu_meters.items()])
        return result

    def _power_window(self, t0, t1):
        """Average power / integrated energy over [t0, t1].

        Trapezoidal integration over the samples inside the window. Short
        windows (a 512-token prefill can be under 300 ms) may catch only one or
        two samples, so those are flagged low confidence rather than pretending
        to a precision we do not have.
        """
        with self._lock:
            rows = list(self.samples)
        if not rows or t1 <= t0:
            return {"available": False, "source": self.source}
        inside = [r for r in rows if t0 <= r[T] <= t1]
        dur = t1 - t0
        if len(inside) < 2:
            mid = (t0 + t1) / 2.0
            near = min(rows, key=lambda r: abs(r[T] - mid))
            if abs(near[T] - mid) > max(1.0, self.interval_ms / 1000.0 * 4):
                return {'available': False, 'source': self.source, 'low_confidence': True, 'gpu_power_complete': False}
            gw = near[GPU_W]
            sw = self._system_w(gw, near[CPU_UTIL], near[CPU_W])
            return {
                "available": gw is not None,
                "gpu_power_complete": gw is not None,
                "low_confidence": True, "samples": len(inside), "source": self.source,
                "gpu_w_avg": gw, "gpu_w_max": gw,
                "gpu_energy_j": None if gw is None else gw * dur,
                "system_w_est": sw,
                "system_energy_j_est": None if sw is None else sw * dur,
                "gpu_util_avg": near[GPU_UTIL], "vram_mb_max": near[MEM_MB],
                "clock_mhz_avg": near[CLOCK], "temp_c_max": near[TEMP],
                "cpu_util_avg": near[CPU_UTIL], "cpu_w_avg": near[CPU_W],
                "duration_s": dur,
            }

        def integrate(value_of):
            energy = 0.0
            span = 0.0
            for a, b in zip(inside, inside[1:]):
                va, vb = value_of(a), value_of(b)
                if va is None or vb is None:
                    continue
                dt = b[T] - a[T]
                if dt <= 0 or dt > max(1.0, self.interval_ms / 1000.0 * 4):
                    continue
                energy += (va + vb) / 2.0 * dt
                span += dt
            return (energy, span) if span > 0 else (None, 0.0)

        gpu_j, gpu_span = integrate(lambda r: r[GPU_W])
        sys_j, sys_span = integrate(lambda r: self._system_w(r[GPU_W], r[CPU_UTIL], r[CPU_W]))

        def avg(idx):
            vals = [r[idx] for r in inside if r[idx] is not None]
            return (sum(vals) / len(vals)) if vals else None

        def peak(idx):
            vals = [r[idx] for r in inside if r[idx] is not None]
            return max(vals) if vals else None

        watts = [r[GPU_W] for r in inside if r[GPU_W] is not None]
        complete = bool(inside) and all(row[GPU_W] is not None for row in inside)
        strict = bool(self._gpu_meters) or self._device_name is not None
        return {
            "available": bool(watts) or sys_j is not None,
            "low_confidence": len(inside) < 4 or not complete or gpu_span < dur * .8,
            "gpu_power_complete": complete,
            "samples": len(inside),
            "source": self.source,
            "duration_s": dur,
            "gpu_w_avg": (gpu_j / gpu_span) if gpu_j is not None and (complete or not strict) else None,
            "gpu_sampled_coverage_s": gpu_span,
            "gpu_sampled_coverage_fraction": min(1.0, gpu_span / dur),
            "gpu_w_max": max(watts) if watts else None,
            # scale the sampled span up to the full window so energy is not
            # under-reported when samples do not land on the exact edges
            "gpu_energy_j": (gpu_j / gpu_span * dur) if gpu_j is not None and (complete or not strict) else None,
            "system_w_est": (sys_j / sys_span) if sys_j is not None and (complete or not strict) else None,
            "system_energy_j_est": (sys_j / sys_span * dur) if sys_j is not None and (complete or not strict) else None,
            "gpu_util_avg": avg(GPU_UTIL),
            "vram_mb_max": peak(MEM_MB),
            "clock_mhz_avg": avg(CLOCK),
            "temp_c_max": peak(TEMP),
            "cpu_util_avg": avg(CPU_UTIL),
            "cpu_w_avg": avg(CPU_W),
        }

    def measure_idle(self, seconds=1.5):
        t0 = time.perf_counter()
        time.sleep(seconds)
        return self.window(t0, time.perf_counter())
