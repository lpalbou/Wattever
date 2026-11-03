"""Deferred sustained local VRAM-copy reference; default plan performs no work.

Run only in a root-authorized idle interval after the180-case cohort/report:
  <flash-next-venv-python> untracked/run_sustained_vram_reference.py --run

Both cards run SEQUENTIALLY:256MiB payload,5 single-copy warmups, one calibration,
15 internal timed batches targeting80ms CUDA-event duration, at most128 copies.
Frozen communication-probe allocation, stream timing, synchronized group wall
timing, validation and cleanup are reused unchanged. Clocks are observations,
not proof of the cause of a rate difference. No inference repeats or model work.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import gpu_communication_probe as probe
from tools.diagnostic_telemetry import DiagnosticTelemetry, _NVML, _bind, available, unavailable
from untracked.host_pin_diagnostic import public_idle

SIZE = 256 * 1024**2
TARGET_MS = 80
MAX_COPIES = 128
WARMUPS = 5
SAMPLES = 15
MAX_PREDICTED_SAMPLE_S = .5
MAX_OBSERVED_SAMPLE_S = 1.0
CARD_BUDGET_S = 8
PROTOCOL = "sustained-local-vram-reference-v1"


def choose_copies(event_ms, host_s):
    if (isinstance(event_ms, bool) or isinstance(host_s, bool) or
        not isinstance(event_ms, (int, float)) or not isinstance(host_s, (int, float)) or
        not math.isfinite(event_ms) or not math.isfinite(host_s) or event_ms <= 0 or host_s <= 0):
        raise ValueError("Calibration requires positive finite event and host durations")
    if host_s > MAX_OBSERVED_SAMPLE_S:
        raise ValueError("Single-copy calibration exceeded the diagnostic sample budget")
    target = math.ceil(TARGET_MS / event_ms)
    wall_bound = max(1, int(MAX_PREDICTED_SAMPLE_S / host_s))
    return max(1, min(MAX_COPIES, target, wall_bound))


class ClockProvider:
    """Read-only optional NVML clocks; no board-power collection/integration."""
    def __init__(self):
        self.nvml = _NVML()
        self.clock = None

    def open(self):
        self.nvml.open()
        try:
            self.clock = _bind(self.nvml.dll, "nvmlDeviceGetClockInfo", [C.c_void_p, C.c_uint, C.POINTER(C.c_uint)])
        except AttributeError:
            pass
        return {"nvml": self.nvml.state, "clock_info": available() if self.clock else unavailable("NVML clock entry point unavailable"),
                "clock_units": "MHz; NVML_CLOCK_SM=1, NVML_CLOCK_MEM=2",
                "sampling_scope": "Both physical cards; correlate t_perf with synchronized copy intervals"}

    def read(self, timestamp):
        cards = self.nvml.read()
        for card in cards:
            handle = self.nvml.handles.get(card["gpu_index"])
            for field, kind in (("sm_clock_mhz", 1), ("memory_clock_mhz", 2)):
                value = C.c_uint()
                code = self.clock(handle, kind, C.byref(value)) if self.clock and handle else None
                card[field] = value.value if code == 0 else None
                card["availability"][field] = available() if code == 0 else unavailable("NVML clock reading unavailable", code)
        return {"gpus": cards, "processes": [], "paging": {}}

    def close(self):
        if self.nvml.initialized:
            code = self.nvml.dll.nvmlShutdown()
            if code:
                return ["NVML shutdown returned status%d" % code]
            self.nvml.initialized = False
        return []


def summarize_clocks(rows, device):
    readings = [gpu for row in rows for gpu in row.get("gpus", []) if gpu.get("gpu_index") == device]
    result = {"gpu_index": device, "observation_count": len(readings)}
    for field in ("sm_clock_mhz", "memory_clock_mhz"):
        values = [gpu[field] for gpu in readings if isinstance(gpu.get(field), (int, float)) and math.isfinite(gpu[field])]
        result[field] = {"valid_samples": len(values), "min": min(values) if values else None,
                         "max": max(values) if values else None,
                         "median": statistics.median(values) if values else None}
    return result


class CardFailure(RuntimeError):
    def __init__(self, row):
        super().__init__("Local copy reference stopped; partial card capture retained")
        self.row = row


def measure_card(route, pool, *, sample_fn=probe.sample, clock=time.perf_counter):
    row = {"gpu_index": route.source, "transfer_bytes": SIZE, "warmup_calls": WARMUPS,
           "target_gpu_event_ms": TARGET_MS, "max_copies_per_sample": MAX_COPIES,
           "requested_internal_samples": SAMPLES, "samples": [], "status": "running",
           "timing_scope": "Frozen CUDA-event stream timing plus synchronized host group wall timing; neither is a DRAM-controller counter",
           "rate_definition": "Payload counts bytes once; nominal read+write=2*payload is a convention, not independent measured traffic",
           "source_pattern": "Frozen probe deterministic64KiB random block repeated across source; differs from historical independent full-random initialization"}
    started = clock()
    row["start_perf"] = started
    def budget():
        if clock() - started > CARD_BUDGET_S:
            raise TimeoutError("Soft card runtime budget reached between synchronized operations")
    try:
        for _ in range(WARMUPS):
            budget()
            sample_fn(pool, [route], SIZE, [1])
        budget()
        calibration = sample_fn(pool, [route], SIZE, [1])
        row["calibration"] = calibration
        copies = choose_copies(calibration["routes"][0]["DtoD_gpu_event_ms"], calibration["elapsed_s"])
        row["copies_per_sample"] = copies
        route.poison(SIZE)  # Outside timing, after warmup and calibration.
        for _ in range(SAMPLES):
            budget()
            sample = sample_fn(pool, [route], SIZE, [copies])
            row["samples"].append(sample)
            if sample["elapsed_s"] > MAX_OBSERVED_SAMPLE_S:
                raise TimeoutError("Observed synchronized sample exceeded the diagnostic sample budget")
        row["copy_end_perf"] = clock()
        row["validation"] = route.validate(SIZE)
        row["validation_scope"] = "Full destination after final timed batch; poisoned after calibration; earlier internal samples not individually validated"
        if row["validation"].get("passed") is not True:
            raise RuntimeError("Full destination validation failed")
        event_rates, host_rates = [], []
        for sample in row["samples"]:
            milliseconds = sample["routes"][0]["DtoD_gpu_event_ms"]
            if not math.isfinite(milliseconds) or milliseconds <= 0:
                raise RuntimeError("Invalid CUDA-event elapsed time")
            event_rates.append(sample["payload_bytes"] / (milliseconds / 1000) / 1e9)
            host_rates.append(sample["payload_bytes_per_second"] / 1e9)
        row.update(status="completed", internal_samples=len(row["samples"]),
                   cuda_event_payload_GB_s=event_rates, synchronized_host_payload_GB_s=host_rates,
                   median_cuda_event_payload_GB_s=statistics.median(event_rates),
                   median_host_payload_GB_s=statistics.median(host_rates),
                   median_nominal_read_plus_write_cuda_event_GB_s=2 * statistics.median(event_rates),
                   median_nominal_read_plus_write_host_GB_s=2 * statistics.median(host_rates),
                   end_perf=clock())
        return row
    except BaseException as exc:
        row.update(status="failed", error_type=type(exc).__name__, internal_samples=len(row["samples"]), end_perf=clock())
        raise CardFailure(row) from exc


def run(output):
    from tools.standalone_screen import validate_prior_completion, verify_idle, verify_gpu_mapping, verify_no_standalone_engines, write_json
    from wattever.power import PowerSampler
    import psutil
    if os.name != "nt":
        raise RuntimeError("This diagnostic requires the installed Windows CUDA path")
    output = Path(output)
    if output.exists():
        raise RuntimeError("Preserve existing diagnostic captures; select a new directory")
    prior = validate_prior_completion()
    idle = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
    mapping = verify_gpu_mapping()
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ.pop("CUDA_VISIBLE_DEVICES", None)
    result = {"schema_version": 1, "protocol": PROTOCOL, "capture_kind": "diagnostic_sustained_local_vram_copy",
              "normal_benchmark_aggregate_eligible": False, "inference_repeat_count": None,
              "started_at": probe.utc(), "status": "starting", "cards": [],
              "prior": {"progress_sha256": prior["progress_sha256"], "report_sha256": prior["report_sha256"]},
              "idle": idle, "physical_mapping": mapping,
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                                ("tools/gpu_communication_probe.py", "tools/diagnostic_telemetry.py", "wattever/power.py",
                                 "tools/standalone_screen.py", "untracked/host_pin_diagnostic.py",
                                 "untracked/run_sustained_vram_reference.py")},
              "protocol_notes": ["Both contexts/buffers initialized before two sequential per-card reference groups",
                                 "One shared all-card PowerSampler; no summation of overlapping job energy",
                                 "Calibration uses one copy; count bounded128 and predicted synchronized batch<=500ms",
                                 "8s/card soft budget and1s observed-sample budget checked between synchronized API operations; in-flight CUDA calls are not forcibly interrupted",
                                 "Clock samples are sparse observations; report unknown states explicitly and do not assume boost caused historical differences"]}
    output.mkdir(parents=True, exist_ok=False)
    cuda = clocks = power = None
    cleanup = []
    try:
        # Fresh guards immediately before first context/allocation.
        result["before_cuda_idle"] = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
        cuda = probe.CUDA()
        result["cuda_inventory"] = cuda.inspect()
        cuda.allocate(SIZE)
        clocks = DiagnosticTelemetry(interval_ms=50, provider=ClockProvider())
        power = PowerSampler(interval_ms=150)
        power.start()
        clocks.start()
        time.sleep(.25)
        with ThreadPoolExecutor(max_workers=1) as pool:
            for device in (0, 1):
                guard = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
                route = probe.Route(cuda, "DtoD", device)
                try:
                    row = measure_card(route, pool)
                except CardFailure as exc:
                    result["cards"].append(dict(exc.row, idle_check=guard))
                    raise
                row["idle_check"] = guard
                time.sleep(.15)  # Final observation, not another copy/inference repeat.
                row["clock_observations"] = clocks.window(row["start_perf"], row["copy_end_perf"])
                row["clocks"] = summarize_clocks(row["clock_observations"]["samples"], device)
                row["whole_card_group_power"] = power.window(row["start_perf"], row["end_perf"])
                row["power_scope"] = "All detected boards, including other idle card; warmup/calibration/validation included, not transfer-only energy"
                result["cards"].append(row)
                write_json(output / "result.json", result)
                print("SUSTAINED_VRAM", device, row["copies_per_sample"], row["median_cuda_event_payload_GB_s"], row["median_host_payload_GB_s"], flush=True)
        result["status"] = "completed"
    except BaseException as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        for name, sampler in (("clocks", clocks), ("power", power)):
            if sampler is not None:
                try:
                    sampler.stop()
                    if name == "clocks" and sampler.metadata.get("status") != "stopped":
                        cleanup.append({"resource": name, "error_type": "CounterCleanupUnconfirmed"})
                    write_json(output / (name + "-raw.json"), {"history": sampler.history(), "summary": sampler.summary()})
                except BaseException as exc:
                    cleanup.append({"resource": name, "error_type": type(exc).__name__})
        if cuda is not None:
            try:
                result["cuda_cleanup"] = cuda.close()
            except BaseException as exc:
                result["cuda_cleanup"] = {"errors": [type(exc).__name__], "contexts_released": False,
                                          "remaining_device_allocations": None, "remaining_host_allocations": None}
        else:
            result["cuda_cleanup"] = {"errors": [], "contexts_released": True, "remaining_device_allocations": 0, "remaining_host_allocations": 0}
        state = result["cuda_cleanup"]
        confirmed = not state.get("errors") and state.get("contexts_released") is True and state.get("remaining_device_allocations") == 0 and state.get("remaining_host_allocations") == 0
        result["sampler_cleanup_errors"] = cleanup
        result["cleanup_confirmed"] = confirmed and not cleanup
        if not result["cleanup_confirmed"]:
            result["status"] = "cleanup_unconfirmed"
        result["finished_at"] = probe.utc()
        write_json(output / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--plan", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "plan_only", "native_calls": 0, "protocol": PROTOCOL,
                          "devices": [0, 1], "device_scheduling": "sequential", "transfer_bytes": SIZE,
                          "target_event_ms": TARGET_MS, "max_copies": MAX_COPIES,
                          "warmups": WARMUPS, "internal_samples": SAMPLES,
                          "inference_repeats": None, "requires": "Completed prior180/report and freshly idle engines"}, indent=2))
        return 0
    output = args.output or ROOT / "untracked/communication-investigation" / (
        "sustained-local-vram-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    try:
        result = run(output)
        return 0 if result["status"] == "completed" and result["cleanup_confirmed"] else 1
    except KeyboardInterrupt:
        return 2
    except Exception as exc:
        print("Sustained VRAM diagnostic stopped (" + type(exc).__name__ + "); inspect owned capture.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
