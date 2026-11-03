"""Allocation-only CUDA host-pinning diagnostic; plan-only unless --run.

Use the flash-next venv Python (psutil is already installed there). Execution
requires the preceding 180-case cohort/report plus freshly verified idle Strata,
LM Studio and standalone engines. This never starts/stops models or services.

The Driver API cuMemHostAlloc(flags=0) corresponds to Runtime cudaHostAllocDefault,
but this is a fresh Driver context, not the native engine's Runtime primary
context or memory state. Results cannot establish the cause of historical/native
fallbacks. No large allocation is read, filled, hashed or copied. Every allocation
is released before proceeding. No pointer, UUID, LUID or process path is exported.
"""
from __future__ import annotations

import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GIB = 1024**3
SIZES = (512 * 1024**2, 4 * GIB, 8 * GIB, 16 * GIB, 75 * GIB // 4)
HEADROOM = 12 * GIB


def utc():
    return datetime.now(timezone.utc).isoformat()


def bind(dll, name, args, result=C.c_int):
    function = getattr(dll, name)
    function.argtypes, function.restype = args, result
    return function


class PerformanceInformation(C.Structure):
    _fields_ = [("cb", C.c_uint32)] + [(key, C.c_size_t) for key in (
        "CommitTotal", "CommitLimit", "CommitPeak", "PhysicalTotal", "PhysicalAvailable", "SystemCache",
        "KernelTotal", "KernelPaged", "KernelNonpaged", "PageSize")] + [(key, C.c_uint32) for key in (
        "HandleCount", "ProcessCount", "ThreadCount")]


class WindowsMemory:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows memory accounting is required")
        self.dll = C.WinDLL(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/psapi.dll"))
        self.get = bind(self.dll, "GetPerformanceInfo", [C.POINTER(PerformanceInformation), C.c_uint32], C.c_int)

    def snapshot(self):
        info = PerformanceInformation()
        info.cb = C.sizeof(info)
        before = time.perf_counter()
        if not self.get(C.byref(info), info.cb) or info.PageSize <= 0 or info.CommitLimit < info.CommitTotal:
            return {"available": False, "reason": "GetPerformanceInfo failed or returned invalid accounting",
                    "t_perf": before}
        page = info.PageSize
        return {"available": True, "t_perf": before, "collection_end_perf": time.perf_counter(),
                "page_size_bytes": page, "physical_total_bytes": info.PhysicalTotal * page,
                "physical_available_bytes": info.PhysicalAvailable * page,
                "commit_total_bytes": info.CommitTotal * page, "commit_limit_bytes": info.CommitLimit * page,
                "commit_headroom_bytes": (info.CommitLimit - info.CommitTotal) * page,
                "system_cache_bytes": info.SystemCache * page,
                "source": "Windows GetPerformanceInfo system-wide page counts; available RAM includes reclaimable availability as reported by Windows"}


def memory_gate(snapshot, size):
    if snapshot.get("available") is not True:
        return {"allowed": False, "reason": "memory_or_commit_accounting_unavailable"}
    required = size + HEADROOM
    physical = snapshot.get("physical_available_bytes")
    commit = snapshot.get("commit_headroom_bytes")
    if type(physical) is not int or type(commit) is not int:
        return {"allowed": False, "reason": "memory_or_commit_accounting_invalid"}
    if physical < required:
        return {"allowed": False, "reason": "available_physical_memory_below_size_plus_12GiB", "required_bytes": required}
    if commit < required:
        return {"allowed": False, "reason": "commit_headroom_below_size_plus_12GiB", "required_bytes": required}
    return {"allowed": True, "required_bytes": required}


def public_bdf(value):
    match = re.fullmatch(r"([0-9a-fA-F]+):([0-9a-fA-F]{2}):([0-9a-fA-F]{2})\.([0-7])", value)
    if not match:
        raise RuntimeError("Unrecognized physical PCI topology format")
    domain, bus, device, function = match.groups()
    return {"domain": int(domain, 16), "bus": int(bus, 16), "device": int(device, 16), "function": int(function),
            "scope": "Non-unique PCI topology coordinates, not a device UUID or full PnP instance ID"}


def public_idle(observation, standalone):
    """Keep gate evidence, not observer command lines, endpoints or file paths."""
    strata = observation.get("strata", {})
    return {"checked_at": observation.get("checked_at"), "strata_state": strata.get("state"),
            "strata_uncertain": strata.get("uncertain"), "strata_model_present": bool(strata.get("model")),
            "strata_process_roles": sorted({process.get("role", "unknown") for process in strata.get("processes", [])}),
            "lmstudio_catalog_count": observation.get("lmstudio_model_count"),
            "standalone_matches": len(standalone.get("existing_matches", []))}


class Driver:
    def __init__(self):
        self.dll = C.WinDLL(str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/nvcuda.dll"))
        self.context = C.c_void_p()
        self.pending_host = None
        definitions = {
            "cuInit": [C.c_uint], "cuDriverGetVersion": [C.POINTER(C.c_int)],
            "cuDeviceGetCount": [C.POINTER(C.c_int)], "cuDeviceGetByPCIBusId": [C.POINTER(C.c_int), C.c_char_p],
            "cuDeviceGetPCIBusId": [C.c_char_p, C.c_int, C.c_int],
            "cuCtxCreate_v2": [C.POINTER(C.c_void_p), C.c_uint, C.c_int],
            "cuCtxDestroy_v2": [C.c_void_p], "cuCtxSetCurrent": [C.c_void_p],
            "cuMemHostAlloc": [C.POINTER(C.c_void_p), C.c_size_t, C.c_uint],
            "cuMemFreeHost": [C.c_void_p], "cuGetErrorName": [C.c_int, C.POINTER(C.c_char_p)],
            "cuGetErrorString": [C.c_int, C.POINTER(C.c_char_p)],
        }
        for name, args in definitions.items():
            bind(self.dll, name, args)

    def status(self, code):
        name, message = C.c_char_p(), C.c_char_p()
        name_code = self.dll.cuGetErrorName(code, C.byref(name))
        message_code = self.dll.cuGetErrorString(code, C.byref(message))
        return {"code": int(code), "success": code == 0,
                "name": name.value.decode("utf-8", "replace") if name_code == 0 and name.value else None,
                "message": message.value.decode("utf-8", "replace") if message_code == 0 and message.value else None}

    def required(self, operation, *args):
        code = getattr(self.dll, operation)(*args)
        if code:
            raise DriverFailure(operation, self.status(code))

    def open(self, bus):
        self.required("cuInit", 0)
        count, version, ordinal = C.c_int(), C.c_int(), C.c_int()
        self.required("cuDeviceGetCount", C.byref(count))
        if count.value != 1:
            raise RuntimeError("Exactly the selected physical 5070 Ti must be CUDA-visible")
        self.required("cuDriverGetVersion", C.byref(version))
        self.required("cuDeviceGetByPCIBusId", C.byref(ordinal), bus.encode("ascii"))
        observed = C.create_string_buffer(32)
        self.required("cuDeviceGetPCIBusId", observed, len(observed), ordinal)
        if public_bdf(observed.value.decode("ascii")) != public_bdf(bus):
            raise RuntimeError("Visible CUDA device does not match the verified physical 5070 Ti")
        started = time.perf_counter()
        self.required("cuCtxCreate_v2", C.byref(self.context), 0, ordinal)
        return {"cuda_driver_api_version": version.value, "visible_device_count": count.value,
                "context_create_wall_s": time.perf_counter() - started,
                "context": "Fresh Driver API context, flags0; no existing primary context retained or reset"}

    def allocate(self, size):
        if self.pending_host is not None:
            raise RuntimeError("Previous host allocation release is unconfirmed; refusing another allocation")
        pointer = C.c_void_p()
        started = time.perf_counter()
        code = self.dll.cuMemHostAlloc(C.byref(pointer), size, 0)
        elapsed = time.perf_counter() - started
        if pointer.value:
            self.pending_host = pointer
        return {"allocation": self.status(code), "allocation_wall_s": elapsed,
                "allocation_returned_nonnull": bool(pointer.value)}

    def release(self):
        if self.pending_host is None:
            return {"required": False, "release_confirmed": True, "release_wall_s": 0}
        started = time.perf_counter()
        code = self.dll.cuMemFreeHost(self.pending_host)
        elapsed = time.perf_counter() - started
        if code == 0:
            self.pending_host = None
        return {"required": True, "release_confirmed": code == 0,
                "release_wall_s": elapsed, "release": self.status(code)}

    def close(self):
        result = {"context_destroyed": False}
        try:
            result["host_cleanup"] = self.release()
        except BaseException as exc:
            result["host_cleanup"] = {"release_confirmed": False, "error_type": type(exc).__name__}
        if self.context.value:
            started = time.perf_counter()
            try:
                code = self.dll.cuCtxDestroy_v2(self.context)
                result.update(context_destroy=self.status(code), context_destroy_wall_s=time.perf_counter() - started,
                              context_destroyed=code == 0)
                if code == 0:
                    self.context = C.c_void_p()
            except BaseException as exc:
                result["context_destroy"] = {"success": False, "error_type": type(exc).__name__}
        else:
            result["context_destroyed"] = True
            result["context_destroy"] = {"required": False, "success": True}
        result["remaining_host_allocations"] = 0 if self.pending_host is None else None
        result["confirmed"] = result["context_destroyed"] and self.pending_host is None
        return result


class DriverFailure(RuntimeError):
    def __init__(self, operation, status):
        super().__init__(operation)
        self.operation, self.status = operation, status


def run(output):
    from tools.standalone_screen import (validate_prior_completion, verify_idle, verify_gpu_mapping,
                                         verify_no_standalone_engines, write_json)
    import psutil
    if os.name != "nt":
        raise RuntimeError("Allocation diagnostic requires Windows")
    output = Path(output)
    if output.exists():
        raise RuntimeError("Preserve the existing diagnostic capture; select a new output")
    prior = validate_prior_completion()
    idle = verify_idle()
    standalone = verify_no_standalone_engines(psutil)
    mapping = verify_gpu_mapping()
    card = next(item for item in mapping if item["physical_sensor_index"] == 0)
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    result = {"schema_version": 1, "capture_kind": "diagnostic_host_allocation", "normal_aggregate_eligible": False,
              "inference_repeat_count": None, "started_at": utc(), "status": "starting",
              "prior": {"progress_sha256": prior["progress_sha256"], "report_sha256": prior["report_sha256"],
                        "finalized_cases": prior.get("finalization", {}).get("cases"),
                        "partial": prior.get("finalization", {}).get("partial")},
              "initial_idle_check": public_idle(idle, standalone),
              "physical_gpu": {"index": 0, "name": card["name"], "pci_topology": public_bdf(card["pci_bus_id"]),
                               "driver_version": card["driver_version"]},
              "software": {"python": platform.python_version(), "windows_release": platform.release(),
                           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
              "protocol": {"api": "CUDA Driver cuMemHostAlloc", "flags": 0,
                           "native_comparison": "Flags0 match Runtime cudaHostAllocDefault, but API/context/state differ; no historical cause can be inferred from this fresh allocation-only state",
                           "sizes_bytes": list(SIZES), "physical_and_commit_reserve_bytes": HEADROOM,
                           "host_buffers_touched": False, "copies_or_kernels": False,
                           "placement": "Driver-allocated pinned pages; OS default affinity/NUMA, no affinity or OS changes",
                           "sequential": "One allocation at a time; immediate release before next size",
                           "privacy": "No host/context pointers, UUIDs, LUIDs, private machine addresses or paths exported"},
              "attempts": []}
    driver = None
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    try:
        memory = WindowsMemory()
        driver = Driver()
        result["before_context_idle_check"] = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
        result["driver"] = driver.open(card["pci_bus_id"])
        result["status"] = "running"
        for size in SIZES:
            # Repeat read-only guards because a previous large allocation can take
            # time. A missing/stale endpoint check is never interpreted as idle.
            guard = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
            before = memory.snapshot()
            row = {"size_bytes": size, "idle_check": guard, "memory_before": before,
                   "gate": memory_gate(before, size), "started_at": utc()}
            result["attempts"].append(row)
            if not row["gate"]["allowed"]:
                row.update(status="not_attempted", reason=row["gate"]["reason"])
                write_json(output, result)
                continue
            try:
                row.update(driver.allocate(size))
                row["memory_after_allocation"] = memory.snapshot()
            finally:
                # Also release when a later observation or serialization fails.
                row["release"] = driver.release()
            row["memory_after_release"] = memory.snapshot()
            row["finished_at"] = utc()
            allocated = row["allocation"]["success"] and row["allocation_returned_nonnull"]
            row["status"] = "allocated_and_released" if allocated else "allocation_failed"
            if row["allocation"]["success"] != row["allocation_returned_nonnull"]:
                row["status"] = "unexpected_allocation_pointer_state"
                raise RuntimeError("Allocation status/pointer presence disagree; halt diagnostic")
            if not row["release"]["release_confirmed"]:
                row["status"] = "release_unconfirmed"
                raise RuntimeError("Host release is unconfirmed; halt diagnostic")
            write_json(output, result)
            print("HOST_PIN", size, row["status"], row["allocation"]["code"], flush=True)
        result["status"] = "completed"
    except DriverFailure as exc:
        result.update(status="failed", failed_operation=exc.operation, cuda_failure=exc.status)
        raise
    except BaseException as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        try:
            result["cleanup"] = driver.close() if driver is not None else {"confirmed": True, "context_destroyed": True, "remaining_host_allocations": 0}
        except BaseException as exc:
            result["cleanup"] = {"confirmed": False, "error_type": type(exc).__name__, "remaining_host_allocations": None, "context_destroyed": False}
        if result["cleanup"].get("confirmed") is not True:
            result["status"] = "cleanup_unconfirmed"
        result["finished_at"] = utc()
        write_json(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"mode": "plan_only", "native_calls": 0, "gpu_allocations": 0,
            "sizes_bytes": list(SIZES), "reserve_bytes": HEADROOM, "physical_gpu": "Verified physical5070Ti(index0) only",
            "gates": ["Prior180cases and report complete", "Fresh Strata/LM Studio/standalone idle before CUDA context and each allocation",
                      "Available physical RAM and commit headroom each >= allocation+12GiB"],
            "api": "cuMemHostAlloc flags0; fresh Driver context, distinct from native Runtime conditions",
            "output_required": "New diagnostic JSON path; defaults to timestamped untracked capture"}, indent=2))
        return 0
    output = args.output or ROOT / "untracked/communication-investigation" / (
        "host-pin-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    try:
        result = run(output)
        if result["status"] != "completed" or result["cleanup"].get("confirmed") is not True:
            return 1
        print("HOST_PIN_DIAGNOSTIC_COMPLETE", flush=True)
        return 0
    except KeyboardInterrupt:
        print("Stopped; owned allocation/context cleanup recorded in diagnostic capture.", flush=True)
        return 2
    except Exception as exc:
        print("Host-pin diagnostic stopped (" + type(exc).__name__ + "); inspect the diagnostic capture.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
