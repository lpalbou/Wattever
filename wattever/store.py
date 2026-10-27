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
import uuid
import hashlib
import shutil

from .platform_util import IS_MAC, IS_WINDOWS, platform_label, run as prun, sysctl

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(ROOT, "results", "runs")
CONFIG_PATH = os.path.join(ROOT, "results", "last-config.json")
TRASH_DIR = os.path.join(ROOT, "results", ".trash")
SCHEMA_VERSION = 2

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
    from .hardware import inventory
    machine = inventory()
    cpu_info = machine.get("cpu") or {}
    system_info = machine.get("system") or {}
    _host_cache = {
        "cpu": cpu_info.get("name") or cpu.strip(),
        "cores_physical": cpu_info.get("physical_cores"),
        "cores_logical": os.cpu_count(),
        "ram_gb": round(system_info["memory_bytes"] / (1024 ** 3), 1) if system_info.get("memory_bytes") else _ram_gb(),
        "os": os_name.strip(),
        "os_build": platform.version(),
        "python": platform.python_version(),
        "platform": platform_label(),
        "machine_model": system_info.get("model") or sysctl("hw.model") or "",
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
    return "%s_%s_%s" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime()),
                          _slug(gpu_name), uuid.uuid4().hex[:10])


def path_for(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", run_id):
        raise ValueError("Invalid benchmark ID.")
    return os.path.join(RUNS_DIR, "%s.json" % run_id)


def prepare_run(run):
    """Attach versioned, public machine/protocol metadata to a new session."""
    from . import __version__, corpus, hardware
    machine = hardware.inventory()
    run["schema_version"] = SCHEMA_VERSION
    run["$schema"] = "../benchmark.schema.json"
    run["application"] = {"name": "Wattever", "version": __version__,
                          "protocol_version": "2"}
    run["hardware"] = machine
    # Retain the legacy GPU block without publishing a device's unique ID.
    run["gpu"] = {k: v for k, v in (run.get("gpu") or {}).items() if k != "uuid"}
    cfg = run.get("config") or {}
    run["measurement_scope"] = {
        "power_gpu_sensor_index": cfg.get("gpu_index", 0),
        "power_boundary": "selected GPU only; not the sum of all connected GPUs",
        "runtime_gpu_placement": cfg.get("runtime_gpu_placement") or "unknown; managed by LM Studio",
        "runtime_gpu_placement_source": "user_reported" if cfg.get("runtime_gpu_placement") else "unknown",
    }
    run["corpus"]["sha256"] = hashlib.sha256(corpus.load_master().encode("utf-8")).hexdigest()
    # A configuration grouping key, never a unique machine identifier.
    cards = [{k: g.get(k) for k in ("name", "vendor", "memory_bytes")}
             for g in machine.get("gpus", [])]
    descriptor = {"cpu": machine.get("cpu"), "system": machine.get("system"),
                  "gpus": sorted(cards, key=lambda g: json.dumps(g, sort_keys=True)),
                  "os": machine.get("os")}
    run["classification"] = {
        "hardware_class": hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()[:20],
        "gpu_names": sorted(g.get("name", "") for g in machine.get("gpus", [])),
        "platform": (run.get("host") or {}).get("platform"),
    }
    return run


def save(run):
    ensure_dirs()
    tmp = path_for(run["id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(run, fh, indent=1, ensure_ascii=False, allow_nan=False)
    os.replace(tmp, path_for(run["id"]))
    return run


def backup(run_id):
    """Keep the exact pre-extension file outside the shared benchmark library."""
    source = path_for(run_id)
    history_dir = os.path.join(os.path.dirname(RUNS_DIR), ".history")
    os.makedirs(history_dir, exist_ok=True)
    target = os.path.join(history_dir, "%s_%s.json" % (run_id, uuid.uuid4().hex))
    shutil.copyfile(source, target)
    return target


def load(run_id):
    path = path_for(run_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            run = json.load(fh)
        if not isinstance(run, dict) or run.get("id") != run_id or not isinstance(run.get("results", []), list):
            return None
        if any(not isinstance(row, dict) for row in run.get("results", [])):
            return None
        if any(not isinstance(run.get(key, {}), dict) for key in ("gpu", "host", "config")):
            return None
        for row in run.get("results", []):
            if row.get("model") is not None and not isinstance(row["model"], str):
                return None
            if row.get("target_tokens") is not None and type(row["target_tokens"]) is not int:
                return None
            if type(row.get("concurrency", 1)) is not int:
                return None
        return run
    except (OSError, ValueError):
        return None


def list_runs(full=False):
    ensure_dirs()
    runs = []
    for name in os.listdir(RUNS_DIR):
        if not name.endswith(".json"):
            continue
        try:
            run = load(name[:-5])
        except ValueError:
            continue
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
        "hardware": run.get("hardware") or {},
        "classification": run.get("classification") or {},
        "schema_version": run.get("schema_version", 1),
        "concurrency_levels": sorted({r.get("concurrency", 1) for r in results}),
        "models": sorted({r.get("model") for r in results if r.get("model")}),
        "sizes": sorted({r.get("target_tokens") for r in results
                         if r.get("target_tokens")}),
        "mtp_modes": sorted({"on" if r.get("mtp") else "off" for r in results}),
        # the settings that decide whether this run can be compared with another
        "max_tokens": (run.get("config") or {}).get("max_tokens"),
        "reasoning_disabled": (run.get("config") or {}).get("disable_thinking"),
        "gpu_offload": (run.get("config") or {}).get("gpu_offload"),
        "n_results": len(results),
        "n_ok": len(ok),
        "n_failed": len(results) - len(ok),
        "idle": run.get("idle") or {},
    }


def delete(run_id):
    path = path_for(run_id)
    if os.path.isfile(path):
        os.makedirs(TRASH_DIR, exist_ok=True)
        os.replace(path, os.path.join(TRASH_DIR, "%s_%s.json" % (run_id, uuid.uuid4().hex[:10])))
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
    ("concurrency", lambda r, _: r.get("concurrency", 1)),
    ("aggregate_completion_tps", lambda r, _: r.get("aggregate_completion_tps")),
    ("aggregate_completion_tokens", lambda r, _: r.get("aggregate_completion_tokens")),
    ("completed_requests", lambda r, _: r.get("completed_requests")),
    ("wall_s", lambda r, _: r.get("wall_s")),
    ("completion_tokens_estimated", lambda r, _: r.get("completion_tokens_estimated")),
    ("aggregate_completion_tokens_estimated", lambda r, _: r.get("aggregate_completion_tokens_estimated")),
    ("hardware_class", lambda r, run: (run.get("classification") or {}).get("hardware_class")),
    ("runtime_gpu_placement", lambda r, run: (run.get("measurement_scope") or {}).get("runtime_gpu_placement")),
    ("session_id", lambda r, _: r.get("session_id")),
    ("context_planner_version", lambda r, run: _session_config(r, run).get("context_planner_version", "legacy")),
    ("context_policy", lambda r, run: _session_config(r, run).get("context_policy", "per_test")),
    ("context_safety_fraction", lambda r, run: _session_config(r, run).get("context_safety_fraction", 0)),
    ("per_request_context_length", lambda r, _: r.get("per_request_context_length")),
    ("requested_total_context", lambda r, _: r.get("requested_total_context", r.get("requested_context"))),
]


def _session_config(row, run):
    return next((session.get("config", {}) for session in run.get("sessions", [])
                 if session.get("id") == row.get("session_id")), run.get("config") or {})


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
