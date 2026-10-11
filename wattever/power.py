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
import re
import subprocess
import threading
import time

from .platform_util import IS_MAC, IS_WINDOWS, popen, run, sysctl

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
        return float(tok)
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
        p = run(["nvidia-smi", "--query-gpu=index,name,memory.total",
                 "--format=csv,noheader,nounits"])
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    out = []
    for line in p.stdout.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) >= 3:
            out.append({"index": int(parts[0]), "name": parts[1],
                        "vram_total_mb": _f(parts[2])})
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
    if IS_MAC:
        return _mac_gpu_info()
    return {}


def list_gpus():
    found = _nvidia_list()
    if found:
        return found
    return _mac_list() if IS_MAC else []


class _CpuMeter:
    """Whole-machine CPU utilisation, no third-party deps.

    Windows: GetSystemTimes. macOS: load average over core count, which is a
    coarser signal but only ever feeds the *estimated* figure -- when
    powermetrics is available the CPU rail is measured and this is not used.
    """

    def __init__(self):
        self._prev = None
        self._ncpu = None
        if IS_MAC:
            raw = sysctl("hw.logicalcpu")
            self._ncpu = int(raw) if raw and raw.isdigit() else None

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
        if IS_WINDOWS:
            try:
                cur = self._times()
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
        if IS_MAC and self._ncpu:
            try:
                import os as _os
                load = _os.getloadavg()[0]
            except (OSError, AttributeError, IndexError):
                return None
            return max(0.0, min(1.0, load / float(self._ncpu)))
        return None


class PowerSampler:
    """Background sampler. Any time window can be queried after the fact.

    One long-lived child process streams readings rather than re-spawning a tool
    per sample, so sampling costs almost nothing and does not pollute the CPU
    figure that also feeds the system estimate.
    """

    def __init__(self, interval_ms=150, gpu_index=0, other_idle_w=60.0, cpu_tdp_w=140.0):
        self.interval_ms = max(50, int(interval_ms))
        self.gpu_index = gpu_index
        self.other_idle_w = float(other_idle_w)
        self.cpu_tdp_w = float(cpu_tdp_w)
        self.samples = []
        self.source = SRC_NONE
        self._proc = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.error = None

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        if self._thread:
            return
        self._stop.clear()
        info = gpu_info(self.gpu_index)
        if info.get("power_source") == SRC_POWERMETRICS:
            target = self._loop_powermetrics
            self.source = SRC_POWERMETRICS
        elif info.get("vendor") == "nvidia" or not IS_MAC:
            target = self._loop_nvidia
            self.source = SRC_NVIDIA
        else:
            # macOS without usable powermetrics: no wattage, speed still measured
            self.error = info.get("power_note") or "power measurement unavailable"
            self.source = SRC_NONE
            return
        self._thread = threading.Thread(target=target, daemon=True)
        self._thread.start()

    def _append(self, row):
        with self._lock:
            self.samples.append(row)

    def _loop_nvidia(self):
        cpu = _CpuMeter()
        cpu.sample()
        args = ["nvidia-smi", "--id=%d" % self.gpu_index,
                "--query-gpu=" + ",".join(SMI_FIELDS),
                "--format=csv,noheader,nounits", "-lms=%d" % self.interval_ms]
        try:
            self._proc = popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, bufsize=1)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error = "nvidia-smi unavailable: %s" % exc
            self.source = SRC_NONE
            return
        try:
            for line in self._proc.stdout:
                if self._stop.is_set():
                    break
                parts = [x.strip() for x in line.split(",")]
                if len(parts) < 5:
                    continue
                self._append((time.perf_counter(), _f(parts[0]), _f(parts[1]), _f(parts[2]),
                              _f(parts[3]), _f(parts[4]), cpu.sample(), None))
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
            self._append((time.perf_counter(), gpu_w, grab("gpu_residency"), None,
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
            except OSError:
                pass
        self._proc = None
        self._thread = None

    def now(self):
        """Most recent reading, for the live UI gauge."""
        with self._lock:
            if not self.samples:
                return None
            row = self.samples[-1]
        return {"gpu_w": row[GPU_W], "gpu_util": row[GPU_UTIL], "vram_mb": row[MEM_MB],
                "clock_mhz": row[CLOCK], "temp_c": row[TEMP], "cpu_util": row[CPU_UTIL],
                "cpu_w": row[CPU_W], "source": self.source,
                "system_w_est": self._system_w(row[GPU_W], row[CPU_UTIL], row[CPU_W])}

    def _system_w(self, gpu_w, cpu_util, cpu_w=None):
        """Estimated whole-machine draw.

        With a measured CPU rail (macOS) the estimate is GPU + CPU + a small
        constant for everything else. Otherwise the CPU side is inferred from
        its TDP scaled by measured load, which is the best Windows allows
        without extra drivers.
        """
        if cpu_w is not None:
            return (gpu_w or 0.0) + cpu_w + self.other_idle_w
        if gpu_w is None:
            return None
        return gpu_w + self.other_idle_w + self.cpu_tdp_w * (cpu_util or 0.0)

    # -- analysis ----------------------------------------------------------
    def window(self, t0, t1):
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
            gw = near[GPU_W]
            sw = self._system_w(gw, near[CPU_UTIL], near[CPU_W])
            return {
                "available": gw is not None,
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
        return {
            "available": bool(watts) or sys_j is not None,
            "low_confidence": len(inside) < 4,
            "samples": len(inside),
            "source": self.source,
            "duration_s": dur,
            "gpu_w_avg": (gpu_j / gpu_span) if gpu_j is not None else None,
            "gpu_w_max": max(watts) if watts else None,
            # scale the sampled span up to the full window so energy is not
            # under-reported when samples do not land on the exact edges
            "gpu_energy_j": (gpu_j / gpu_span * dur) if gpu_j is not None else None,
            "system_w_est": (sys_j / sys_span) if sys_j is not None else None,
            "system_energy_j_est": (sys_j / sys_span * dur) if sys_j is not None else None,
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
