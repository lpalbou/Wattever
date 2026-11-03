"""One pooled allocation-only diagnostic, separate from sequential pin captures.

Default plan makes no native calls. --run requires the completed prior cohort/
report and freshly idle engines. Hold8GiB, add8GiB, then add2.75GiB. Stop on the
first failed/skipped/uncertain allocation; release everything in reverse order.
Each next allocation requires available physical RAM and commit >=chunk+12GiB.

The API/context/state differ from the native Runtime engine. A pooled success
or failure alone cannot establish a global pin budget or per-allocation limit.
No model, copy, kernel, buffer read/fill, OS change or other-process termination.
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
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from untracked.host_pin_diagnostic import Driver, DriverFailure, GIB, HEADROOM, WindowsMemory, memory_gate, public_bdf, public_idle, utc

CHUNKS = (8 * GIB, 8 * GIB, 11 * GIB // 4)


class PooledDriver(Driver):
    """Separate pool ownership; Driver.pending_host remains unused/null."""
    def __init__(self):
        super().__init__()
        self.pool = []

    @property
    def held_bytes(self):
        return sum(item["size"] for item in self.pool if item["confirmed_allocated"])

    @property
    def ownership_certain(self):
        return all(item["confirmed_allocated"] for item in self.pool)

    def allocate_chunk(self, size):
        pointer = C.c_void_p()
        before = self.held_bytes
        started = time.perf_counter()
        code = self.dll.cuMemHostAlloc(C.byref(pointer), size, 0)
        elapsed = time.perf_counter() - started
        if pointer.value:
            # Even an unexpected nonnull pointer returned with an error remains
            # owned for best-effort release. Never export its numeric address.
            self.pool.append({"pointer": pointer, "size": size, "confirmed_allocated": code == 0})
        return {"allocation": self.status(code), "allocation_wall_s": elapsed,
                "allocation_returned_nonnull": bool(pointer.value), "held_bytes_before": before,
                "held_bytes_after": self.held_bytes if self.ownership_certain else None,
                "held_allocation_count_after": len(self.pool), "ownership_certain": self.ownership_certain}

    def close(self):
        releases = []
        for index in range(len(self.pool) - 1, -1, -1):
            item = self.pool[index]
            row = {"allocation_index": index + 1, "size_bytes": item["size"],
                   "held_confirmed_bytes_before": self.held_bytes}
            started = time.perf_counter()
            try:
                code = self.dll.cuMemFreeHost(item["pointer"])
                row.update(release=self.status(code), release_confirmed=code == 0,
                           release_wall_s=time.perf_counter() - started)
            except BaseException as exc:
                row.update(release_confirmed=False, error_type=type(exc).__name__,
                           release_wall_s=time.perf_counter() - started)
            if row["release_confirmed"]:
                self.pool.pop(index)
            row["held_confirmed_bytes_after"] = self.held_bytes
            releases.append(row)
        # Parent cleanup owns only the fresh Driver context (no primary-context
        # retain/reset). It cannot prove failed host frees were reclaimed.
        context = super().close()
        context.update(host_cleanup={"release_order": "reverse allocation order", "releases": releases},
                       remaining_host_allocations=0 if not self.pool else None,
                       unreleased_requested_bytes=sum(item["size"] for item in self.pool),
                       host_release_confirmed=not self.pool)
        context["confirmed"] = context.get("confirmed") is True and not self.pool
        return context


def run(output):
    from tools.standalone_screen import (validate_prior_completion, verify_idle, verify_gpu_mapping,
                                         verify_no_standalone_engines, write_json)
    import psutil
    if os.name != "nt":
        raise RuntimeError("Windows allocation diagnostic required")
    output = Path(output)
    if output.exists():
        raise RuntimeError("Preserve the existing diagnostic capture; use a new output path")
    prior = validate_prior_completion()
    idle = verify_idle()
    standalone = verify_no_standalone_engines(psutil)
    mapping = verify_gpu_mapping()
    card = next(item for item in mapping if item["physical_sensor_index"] == 0)
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["CUDA_VISIBLE_DEVICES"] = "0"
    result = {"schema_version": 1, "capture_kind": "diagnostic_pooled_host_allocation",
              "normal_aggregate_eligible": False, "inference_repeat_count": None,
              "started_at": utc(), "status": "starting", "attempts": [],
              "prior": {"progress_sha256": prior["progress_sha256"], "report_sha256": prior["report_sha256"],
                        "finalized_cases": prior.get("finalization", {}).get("cases")},
              "initial_idle_check": public_idle(idle, standalone),
              "physical_gpu": {"index": 0, "name": card["name"], "driver_version": card["driver_version"],
                               "pci_topology": public_bdf(card["pci_bus_id"])},
              "software": {"python": platform.python_version(), "windows_release": platform.release(),
                           "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                           "shared_driver_helper_sha256": hashlib.sha256((ROOT / "untracked/host_pin_diagnostic.py").read_bytes()).hexdigest()},
              "protocol": {"chunks_bytes": list(CHUNKS), "target_total_bytes": sum(CHUNKS), "api": "Driver cuMemHostAlloc flags0",
                           "context": "Fresh Driver API context on physical5070 only; distinct from native Runtime primary context",
                           "reserve_bytes": HEADROOM, "guard": "Before each chunk: available physical RAM and commit headroom each >=next chunk+12GiB; prior chunks remain held",
                           "retention": "Hold successful chunks concurrently until first failure/skip or full18.75GiB; then reverse-order free",
                           "application_reads_fills_copies_or_kernels": False,
                           "interpretation": "One fresh-state diagnostic, no inference repeat. Pooled and sequential outcomes alone do not establish a global budget, per-allocation limit or historical/native failure cause.",
                           "privacy": "No pointers, UUIDs, LUIDs, private addresses, observer command lines or paths exported"}}
    driver, memory = None, None
    output.parent.mkdir(parents=True, exist_ok=True)
    write_json(output, result)
    try:
        memory = WindowsMemory()
        driver = PooledDriver()
        result["before_context_idle_check"] = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
        result["driver"] = driver.open(card["pci_bus_id"])
        result["status"] = "running"
        for index, size in enumerate(CHUNKS):
            guard = public_idle(verify_idle(), verify_no_standalone_engines(psutil))
            before = memory.snapshot()
            row = {"chunk_index": index + 1, "size_bytes": size, "started_at": utc(),
                   "held_bytes_before": driver.held_bytes, "memory_before": before, "idle_check": guard,
                   "gate": memory_gate(before, size)}
            result["attempts"].append(row)
            if not row["gate"]["allowed"]:
                row.update(status="not_attempted", reason=row["gate"]["reason"], held_bytes_after=driver.held_bytes)
                result.update(sequence_outcome="stopped_by_memory_guard", first_unattempted_chunk=index + 1)
                write_json(output, result)
                break
            row.update(driver.allocate_chunk(size))
            row["memory_after_allocation"] = memory.snapshot()
            row["finished_at"] = utc()
            if row["allocation"]["success"] != row["allocation_returned_nonnull"]:
                row["status"] = "unexpected_allocation_pointer_state"
                raise RuntimeError("Allocation code/pointer presence disagree; stop and release owned pool")
            row["status"] = "held" if row["allocation"]["success"] else "allocation_failed"
            write_json(output, result)
            print("HOST_PIN_POOL", index + 1, size, row["status"], row["allocation"]["code"], flush=True)
            if not row["allocation"]["success"]:
                result.update(sequence_outcome="stopped_at_first_allocation_failure", first_failed_chunk=index + 1)
                break
        else:
            result["sequence_outcome"] = "all_chunks_held_concurrently"
        result["held_bytes_before_cleanup"] = driver.held_bytes if driver.ownership_certain else None
        result["held_allocations_before_cleanup"] = len(driver.pool)
        result["memory_before_cleanup"] = memory.snapshot()
        result["status"] = "completed"
    except DriverFailure as exc:
        result.update(status="failed", failed_operation=exc.operation, cuda_failure=exc.status)
        raise
    except BaseException as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        if driver is not None:
            result.setdefault("held_bytes_before_cleanup", driver.held_bytes if driver.ownership_certain else None)
            result.setdefault("held_allocations_before_cleanup", len(driver.pool))
        try:
            result["cleanup"] = driver.close() if driver is not None else {"confirmed": True, "context_destroyed": True, "remaining_host_allocations": 0}
        except BaseException as exc:
            result["cleanup"] = {"confirmed": False, "error_type": type(exc).__name__,
                                 "context_destroyed": False, "remaining_host_allocations": None}
        try:
            result["memory_after_cleanup"] = memory.snapshot()
        except BaseException as exc:
            result["memory_after_cleanup"] = {"available": False, "error_type": type(exc).__name__}
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
            "chunks_bytes": list(CHUNKS), "target_total_bytes": sum(CHUNKS), "reserve_bytes": HEADROOM,
            "gates": ["Prior180cases/report", "Fresh idle before context and each chunk", "Next chunk+12GiB physical RAM and commit"],
            "sequence": "Hold8GiB+8GiB+2.75GiB; first failure/skip stops further attempts; reverse-order free then owned-context destruction",
            "scope": "Single new pooled diagnostic; old sequential/native datasets remain unchanged"}, indent=2))
        return 0
    output = args.output or ROOT / "untracked/communication-investigation" / (
        "host-pin-pool-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    try:
        result = run(output)
        if result["status"] != "completed" or result["cleanup"].get("confirmed") is not True:
            return 1
        print("HOST_PIN_POOL_DIAGNOSTIC_COMPLETE", flush=True)
        return 0
    except KeyboardInterrupt:
        print("Stopped; owned allocation/context cleanup recorded.", flush=True)
        return 2
    except Exception as exc:
        print("Pooled host-pin diagnostic stopped (" + type(exc).__name__ + "); inspect capture.", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
