"""Run persistence.

One JSON file per run under results/runs/. The cache exists because each card
has to be benchmarked in a separate session -- you shut the machine down, swap
the GPU, relaunch -- so results have to survive the process and stay keyed to
the card that produced them.
"""
import ctypes
import json
import os
import platform
import re
import subprocess
import time

from .platform_util import IS_MAC, IS_WINDOWS, platform_label, run as prun, sysctl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "results", "runs")
CONFIG_PATH = os.path.join(ROOT, "results", "last-config.json")

_host_cache = None


def _ram_gb():
    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    if IS_MAC:
        raw = sysctl("hw.memsize")
        if raw and raw.isdigit():
            return round(int(raw) / (1024 ** 3), 1)
        return None
    try:
        st = MemStatus()
        st.dwLength = ctypes.sizeof(MemStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return round(st.ullTotalPhys / (1024 ** 3), 1)
    except (AttributeError, OSError):
        pass
    return None


def host_info():
    """Static machine description, cached -- the CPU query is slow."""
    global _host_cache
    if _host_cache is not None:
        return _host_cache
    cpu = platform.processor() or ""
    if IS_MAC:
        # On Apple Silicon this is the chip name ("Apple M4 Pro"); on Intel Macs
        # it is the usual Intel marketing string.
        cpu = sysctl("machdep.cpu.brand_string") or cpu
    elif IS_WINDOWS:
        try:
            p = prun(["powershell", "-NoProfile", "-Command",
                      "(Get-CimInstance Win32_Processor | Select-Object -First 1).Name"],
                     timeout=25)
            if p.returncode == 0 and p.stdout.strip():
                cpu = re.sub(r"\s+", " ", p.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    os_name = "%s %s" % (platform.system(), platform.release())
    if IS_MAC:
        os_name = "macOS %s" % (sysctl("kern.osproductversion") or platform.mac_ver()[0] or "")
    _host_cache = {
        "cpu": cpu.strip(),
        "cores_logical": os.cpu_count(),
        "ram_gb": _ram_gb(),
        "os": os_name.strip(),
        "os_build": platform.version(),
        "python": platform.python_version(),
        "platform": platform_label(),
        "machine_model": sysctl("hw.model") or "",
    }
    return _host_cache


def _slug(text):
    text = re.sub(r"(?i)\b(nvidia|geforce)\b", "", text or "gpu")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text or "gpu"


def ensure_dirs():
    if not os.path.isdir(RUNS_DIR):
        os.makedirs(RUNS_DIR)


def new_run_id(gpu_name):
    return "%s_%s" % (time.strftime("%Y%m%d-%H%M%S"), _slug(gpu_name))


def path_for(run_id):
    return os.path.join(RUNS_DIR, "%s.json" % re.sub(r"[^A-Za-z0-9_.-]", "_", run_id))


def save(run):
    ensure_dirs()
    tmp = path_for(run["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, path_for(run["id"]))
    return run


def load(run_id):
    path = path_for(run_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def list_runs(full=False):
    ensure_dirs()
    runs = []
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(".json"):
            continue
        run = load(name[:-5])
        if not run:
            continue
        runs.append(run if full else summarise(run))
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return runs


def summarise(run):
    results = run.get("results") or []
    ok = [r for r in results if r.get("status") == "ok"]
    return {
        "id": run.get("id"),
        "label": run.get("label"),
        "notes": run.get("notes"),
        "created_at": run.get("created_at"),
        "status": run.get("status"),
        "gpu": run.get("gpu") or {},
        "host": run.get("host") or {},
        "models": sorted({r.get("model") for r in results if r.get("model")}),
        "sizes": sorted({r.get("target_tokens") for r in results
                         if r.get("target_tokens")}),
        "mtp_modes": sorted({"on" if r.get("mtp") else "off" for r in results}),
        # the settings that decide whether this run can be compared with another
        "max_tokens": (run.get("config") or {}).get("max_tokens"),
        "reasoning_disabled": (run.get("config") or {}).get("disable_thinking"),
        "n_results": len(results),
        "n_ok": len(ok),
        "n_failed": len(results) - len(ok),
        "idle": run.get("idle") or {},
    }


def delete(run_id):
    path = path_for(run_id)
    if os.path.isfile(path):
        os.remove(path)
        return True
    return False


def set_meta(run_id, label=None, notes=None):
    run = load(run_id)
    if not run:
        return None
    if label is not None:
        run["label"] = label
    if notes is not None:
        run["notes"] = notes
    return save(run)


def save_config(cfg):
    ensure_dirs()
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=1)
    except OSError:
        pass


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


CSV_COLUMNS = [
    ("run_id", lambda r, run: run.get("id")),
    ("run_label", lambda r, run: run.get("label")),
    ("gpu", lambda r, run: (run.get("gpu") or {}).get("name")),
    ("vram_total_mb", lambda r, run: (run.get("gpu") or {}).get("vram_total_mb")),
    ("driver", lambda r, run: (run.get("gpu") or {}).get("driver")),
    ("created_at", lambda r, run: run.get("created_at")),
    ("model", lambda r, _: r.get("model")),
    ("quantization", lambda r, _: r.get("quantization")),
    ("mtp", lambda r, _: "on" if r.get("mtp") else "off"),
    ("reasoning", lambda r, _: "off" if r.get("reasoning_disabled") else "on"),
    ("target_tokens", lambda r, _: r.get("target_tokens")),
    ("prompt_tokens", lambda r, _: r.get("prompt_tokens")),
    ("status", lambda r, _: r.get("status")),
    ("fail_reason", lambda r, _: r.get("fail_kind")),
    ("context_length", lambda r, _: r.get("context_length")),
    ("gpu_offload", lambda r, _: r.get("gpu_offload")),
    ("load_s", lambda r, _: r.get("load_s")),
    ("ttft_s", lambda r, _: r.get("ttft_s")),
    ("prefill_tps", lambda r, _: r.get("prefill_tps")),
    ("decode_tps", lambda r, _: r.get("decode_tps")),
    ("completion_tokens", lambda r, _: r.get("completion_tokens")),
    ("reasoning_tokens", lambda r, _: r.get("reasoning_tokens")),
    ("prefill_gpu_w", lambda r, _: (r.get("power") or {}).get("prefill_gpu_w")),
    ("decode_gpu_w", lambda r, _: (r.get("power") or {}).get("decode_gpu_w")),
    ("prefill_system_w_est", lambda r, _: (r.get("power") or {}).get("prefill_system_w_est")),
    ("decode_system_w_est", lambda r, _: (r.get("power") or {}).get("decode_system_w_est")),
    ("j_per_1k_prefill", lambda r, _: (r.get("power") or {}).get("j_per_1k_prefill")),
    ("j_per_token_decode", lambda r, _: (r.get("power") or {}).get("j_per_token_decode")),
    ("vram_peak_mb", lambda r, _: r.get("vram_peak_mb")),
    ("gpu_util_prefill", lambda r, _: (r.get("power") or {}).get("prefill_gpu_util")),
    ("cpu_util_prefill", lambda r, _: (r.get("power") or {}).get("prefill_cpu_util")),
    # appended, so a CSV from an earlier version still parses identically
    ("power_source", lambda r, run: (r.get("power") or {}).get("source")
                                    or run.get("power_source")),
    ("memory_architecture", lambda r, run: (run.get("gpu") or {}).get("memory_architecture")
                                           or "discrete"),
    ("platform", lambda r, run: (run.get("host") or {}).get("platform") or "windows"),
    ("prefill_cpu_w", lambda r, _: (r.get("power") or {}).get("prefill_cpu_w")),
    ("decode_cpu_w", lambda r, _: (r.get("power") or {}).get("decode_cpu_w")),
]


def to_csv(runs):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([name for name, _ in CSV_COLUMNS])
    for run in runs:
        for r in run.get("results") or []:
            writer.writerow([fn(r, run) for _, fn in CSV_COLUMNS])
    return buf.getvalue()
