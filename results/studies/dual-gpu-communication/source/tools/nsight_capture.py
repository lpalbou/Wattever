"""Private, opt-in Nsight diagnostics; never included in benchmark aggregates.

launch_command instruments an owned NEW process before CUDA initialization.
start/stop delimit requests after model loading/warmup. Existing servers are not
attached, ports are not inspected/killed, and shutdown always uses --kill=false.
The caller must stop its verified target process. Plan-only is the default.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
INVESTIGATION = ROOT / "untracked/communication-investigation"
SCHEMA = "wattever-nsight-diagnostic-v1"
PINNED_VERSION = "2026.3.2.476"


def default_nsys():
    matches = sorted((INVESTIGATION / "nsight-2026.3.2").glob(
        "**/target-windows-x64/nsys.exe"))
    return matches[0] if matches else None


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def _label(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value))[:64] or "capture"


def safe_symbol(value, kind="symbol"):
    """Only code symbols leave the private trace; never export arbitrary strings."""
    value = str(value or "unknown")
    if (len(value) <= 350 and not value.lstrip().startswith(("+", "-", "@", "="))
            and not re.search(r"[/\\@=\r\n]|https?:|\b\d+\.\d+\.\d+\.\d+\b", value)
            and re.fullmatch(r"[\w\s:<>*,().+~&\[\]-]+", value, flags=re.ASCII)):
        return value
    return kind + "_" + hashlib.sha256(value.encode()).hexdigest()[:16]


class NsightUnavailable(RuntimeError):
    pass


class NsightCapture:
    """One uniquely owned interactive session, possibly several short ranges.

    A normal integration is launch_command -> caller Popen -> adopt_launcher ->
    readiness/warmup -> start -> requests -> stop -> caller target shutdown ->
    close -> export_and_summarize. Constructor/plan methods never launch GPU work.
    execute=True is required for every actual Nsight session command.
    """
    def __init__(self, nsys_exe=None, output_dir=None, label="profile", *, execute=False,
                 graph_nodes=True, command_timeout=60):
        self.nsys = Path(nsys_exe) if nsys_exe else default_nsys()
        if not self.nsys or not self.nsys.is_file():
            raise NsightUnavailable("Pinned Nsight executable unavailable")
        self.directory = Path(output_dir or INVESTIGATION / "profiles") / (
            _label(label) + "-" + uuid.uuid4().hex[:12])
        self.private = self.directory / "private"
        self.public = self.directory / "shareable"
        self.session = "wattever_" + uuid.uuid4().hex
        self.execute = execute
        self.graph_nodes = graph_nodes
        self.timeout = min(max(float(command_timeout), 1), 60)
        self.launcher = None
        self.active = None
        self.ranges = []
        self.failures = []
        self.commands = []
        self._launch_built = False
        self._closed = False
        self._version_checked = False
        self.observed_version = None
        self._owned_target_exit_confirmed = False
        self._closure_verification = None

    def _directories(self):
        self.private.mkdir(parents=True, exist_ok=True)
        self.public.mkdir(parents=True, exist_ok=True)

    def launch_command(self, application):
        if not application or self._launch_built:
            raise ValueError("Provide one owned application command per capture")
        self._launch_built = True
        return [str(self.nsys), "launch", "--session-new=" + self.session,
                "--trace=cuda,nvtx", "--cuda-event-trace=false",
                "--cuda-graph-trace=" + ("node:host-only" if self.graph_nodes else "graph"),
                "--system-wide=false", "--resolve-symbols=false",
                "--inherit-environment=true", "--show-output=true", "--wait=all",
                *map(str, application)]

    def adopt_launcher(self, process):
        """Register the caller's Popen handle, never an arbitrary PID/session."""
        if not self.execute or not self._launch_built:
            raise ValueError("Actual launch requires execute=True and launch_command")
        if not isinstance(process, subprocess.Popen) or process.poll() is not None:
            self.failures.append({"operation": "launch", "reason": "owned_launcher_exited_or_invalid"})
            self._manifest()
            raise NsightUnavailable("Owned Nsight launcher did not remain running")
        self.launcher = process

    def launch(self, application, **popen_options):
        """Convenience wrapper; caller still verifies/stops target descendants."""
        if not self.execute:
            raise ValueError("Plan-only capture cannot launch an application")
        self._directories()
        command = self.launch_command(application)
        self._check_version()
        process = subprocess.Popen(command, **popen_options)
        self.adopt_launcher(process)
        return process

    def _check_version(self):
        if self._version_checked:
            return
        result = subprocess.run([str(self.nsys), "--version"], capture_output=True,
                                text=True, timeout=self.timeout)
        if result.returncode or PINNED_VERSION not in result.stdout + result.stderr:
            raise NsightUnavailable("Unexpected Nsight version; require " + PINNED_VERSION)
        self._version_checked = True
        self.observed_version = PINNED_VERSION

    def start_command(self, phase="workload", index=None):
        i = len(self.ranges) if index is None else index
        report = self.private / (f"{i:02d}-" + _label(phase))
        return [str(self.nsys), "start", "--session=" + self.session,
                "--sample=none", "--cpuctxsw=none", "--backtrace=none",
                "--discard-environment=true", "--capture-range=none",
                "--force-overwrite=false", "--output=" + str(report)]

    def stop_command(self):
        return [str(self.nsys), "stop", "--session=" + self.session]

    def shutdown_command(self):
        return [str(self.nsys), "shutdown", "--session=" + self.session, "--kill=false"]

    def status_command(self):
        return [str(self.nsys), "status", "--session=" + self.session]

    @staticmethod
    def _absent_session(result):
        detail = (result.stdout + result.stderr).strip().casefold()
        return result.returncode == 1 and detail in ("there are no active sessions.", "session is not found.")

    def _run(self, command, operation, *, allow_closed=False, allow_absent=False):
        if not self.execute or self.launcher is None or (self._closed and not allow_closed):
            raise ValueError("No owned, enabled Nsight session")
        self._directories()
        self._check_version()
        index = len(self.commands)
        record = {"operation": operation, "command": command,
                  "utc": datetime.now(timezone.utc).isoformat()}
        self.commands.append(record)
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=self.timeout)
            record["returncode"] = result.returncode
            (self.private / f"command-{index:02d}.stdout.txt").write_text(
                result.stdout, encoding="utf-8")
            (self.private / f"command-{index:02d}.stderr.txt").write_text(
                result.stderr, encoding="utf-8")
            if result.returncode:
                if allow_absent and operation == "status" and self._absent_session(result):
                    record["session_absent"] = True
                    return result
                if operation == "shutdown" and self._owned_target_exit_confirmed and self._absent_session(result):
                    try:
                        status = self._run(self.status_command(), "status", allow_absent=True)
                    except NsightUnavailable:
                        status = None
                    if status is not None and self._absent_session(status):
                        self._closure_verification = {"owned_target_exit_confirmed":True,
                            "scoped_session_absent":True, "shutdown_returncode":result.returncode,
                            "status_returncode":status.returncode, "status":"already_closed"}
                        record["already_closed_session_verified"] = True
                        return result
                detail = (result.stdout + result.stderr).lower()
                reason = ("insufficient_privileges_or_counter_access" if any(
                    term in detail for term in ("privilege", "permission", "access is denied", "err_nvgpuctrperm"))
                    else "nsight_command_failed")
                self.failures.append({"operation": operation, "reason": reason})
                raise NsightUnavailable(operation + " failed; raw reason retained privately")
            return result
        except subprocess.TimeoutExpired as exc:
            self.failures.append({"operation": operation, "reason": "command_timeout"})
            raise NsightUnavailable(operation + " timed out") from exc
        except OSError as exc:
            self.failures.append({"operation": operation, "reason": "nsight_process_unavailable"})
            raise NsightUnavailable(operation + " unavailable") from exc
        finally:
            _write_json(self.private / "commands.json", self.commands)
            self._manifest()

    def start(self, phase="workload"):
        if self.active is not None:
            raise ValueError("Stop current collection before starting another")
        if self.launcher is None or self.launcher.poll() is not None:
            raise NsightUnavailable("Owned launcher is not running")
        self._run(self.start_command(phase), "start")
        self.active = {"phase": _label(phase), "index": len(self.ranges),
                       "host_started_ns": time.perf_counter_ns(),
                       "report_stem": f"{len(self.ranges):02d}-" + _label(phase)}
        self._manifest()
        return dict(self.active)

    def stop(self):
        if self.active is None:
            return None
        active = self.active
        active["host_stopped_ns"] = time.perf_counter_ns()
        try:
            self._run(self.stop_command(), "stop")
            active["status"] = "collected"
        except NsightUnavailable:
            active["status"] = "unavailable"
            raise
        finally:
            self.ranges.append(active)
            self.active = None
            self._manifest()
        return dict(active)

    def close(self, *, owned_target_exit_confirmed=False):
        """End only our session; this never terminates the target application."""
        if type(owned_target_exit_confirmed) is not bool:
            raise ValueError("Owned target exit confirmation must be boolean")
        self._owned_target_exit_confirmed = owned_target_exit_confirmed
        if self._closed or self.launcher is None:
            return
        try:
            if self.active is not None:
                self.stop()
        finally:
            try:
                self._run(self.shutdown_command(), "shutdown")
            finally:
                self._closed = True
                self._manifest()

    def _manifest(self):
        self._directories()
        summary = self.plan()
        summary.update({"ranges": [{k: v for k, v in r.items()
                                    if not k.startswith("host_")} for r in self.ranges],
                        "failures": self.failures, "closed": self._closed,
                        "closure_verification": self._closure_verification})
        _write_json(self.public / "capture.json", summary)
        _write_json(self.private / "ranges.json", self.ranges)

    def plan(self):
        return {"schema": SCHEMA, "capture_kind": "profile", "normal_aggregate_eligible": False,
                "tool": "NVIDIA Nsight Systems", "required_version": PINNED_VERSION,
                "observed_version": self.observed_version,
                "execution_enabled": self.execute, "collection": "cuda,nvtx",
                "graph_trace": "node:host-only" if self.graph_nodes else "graph",
                "model_loading_excluded": "caller starts collection after readiness/warmup",
                "profiling_overhead": {"present": True, "quantified": False,
                    "note": "Injection is present before collection; graph-node trace can add substantial overhead. Compare separate unprofiled conditions."},
                "unavailable_counters": ["CPU sampling/context switches disabled (no elevation)",
                    "GPU hardware/PCIe throughput counters not requested", "WDDM residency/page-fault trace not requested",
                    "Power/energy/temperature/clocks require separate synchronized per-card telemetry"],
                "privacy": "Raw nsys-rep, sqlite, commands, stdout and stderr remain in private/; only shareable/ is exportable"}

    def export_and_summarize(self):
        if not self._closed:
            raise ValueError("Close capture after caller-owned target shutdown before export")
        results = []
        for item in self.ranges:
            stem = item["report_stem"]
            report = self.private / (stem + ".nsys-rep")
            database = self.private / (stem + ".sqlite")
            if item["status"] != "collected" or not report.is_file():
                results.append({"phase": item["phase"], "status": "unavailable",
                                "reason": "report_not_produced"})
                continue
            self._run([str(self.nsys), "export", "--type=sqlite", "--force-overwrite=false",
                       "--output=" + str(database), str(report)], "export", allow_closed=True)
            results.append(analyze_sqlite(database, self.public / stem))
        return results


def _union_ns(intervals):
    if not intervals:
        return 0
    intervals = sorted(intervals)
    start, end = intervals[0]
    total = 0
    for a, b in intervals[1:]:
        if a > end:
            total += end - start
            start, end = a, b
        else:
            end = max(end, b)
    return total + end - start


def _distribution(values):
    values = sorted(values)
    if not values:
        return {}
    return {"count": len(values), "total_duration_ns": sum(values),
            "min_ns": values[0], "median_ns": statistics.median(values),
            "p95_ns": values[min(len(values) - 1, int((len(values) - 1) * .95))],
            "max_ns": values[-1]}


COPY_KINDS = {0: "unknown", 1: "HtoD", 2: "DtoH", 3: "HtoA", 4: "AtoH",
              5: "AtoA", 6: "AtoD", 7: "DtoA", 8: "DtoD", 9: "HtoH", 10: "PtoP"}
MEMORY_KIND_NAMES = {"PAGEABLE":"pageable", "PINNED":"pinned", "DEVICE":"device",
                     "ARRAY":"array", "MANAGED":"managed", "DEVICE_STATIC":"device_static",
                     "MANAGED_STATIC":"managed_static", "UNKNOWN":"unknown"}


def analyze_sqlite(database, output_dir, *, timeline_limit=500000):
    """Read-only schema-adaptive parser. No profiler/GPU/process operations.

    Only allowlisted CUDA event columns and symbol strings are published. Device
    ordinals are trace-local, not nvidia-smi identities. Physical P2P transport
    cannot be proven from a CUPTI copyKind or a peer API call alone.
    """
    database, output_dir = Path(database), Path(output_dir)
    if timeline_limit < 0:
        raise ValueError("timeline_limit must be nonnegative")
    if not database.is_file():
        raise FileNotFoundError(database)
    output_dir.mkdir(parents=True, exist_ok=False)
    events = []
    missing = []
    from contextlib import closing
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        memory_kinds = {}
        if "ENUM_CUDA_MEM_KIND" in tables:
            columns = {r[1] for r in con.execute('PRAGMA table_info("ENUM_CUDA_MEM_KIND")')}
            if {"id", "name"} <= columns:
                for value, name in con.execute('SELECT id,name FROM "ENUM_CUDA_MEM_KIND" LIMIT 256'):
                    prefix = "CUDA_MEMOPR_MEMORY_KIND_"
                    if isinstance(name, str) and name.startswith(prefix):
                        label = MEMORY_KIND_NAMES.get(name[len(prefix):])
                        if label:
                            memory_kinds[value] = label
        if not memory_kinds:
            missing.append("CUDA memory-kind enum unavailable; memory types remain unverified")
        strings = {}
        if "StringIds" in tables:
            cols = {r[1] for r in con.execute('PRAGMA table_info("StringIds")')}
            if {"id", "value"} <= cols:
                strings = dict(con.execute('SELECT id,value FROM "StringIds"'))
        families = {
            "kernel": ("CUPTI_ACTIVITY_KIND_KERNEL", "CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL"),
            "memcpy": ("CUPTI_ACTIVITY_KIND_MEMCPY", "CUPTI_ACTIVITY_KIND_MEMCPY2"),
            "runtime": ("CUPTI_ACTIVITY_KIND_RUNTIME",),
            "driver": ("CUPTI_ACTIVITY_KIND_DRIVER",),
            "memset": ("CUPTI_ACTIVITY_KIND_MEMSET",),
        }
        observed_tables = []
        for kind, candidates in families.items():
            found = [t for t in candidates if t in tables]
            if not found:
                missing.append(kind + " events not exported")
            for table in found:
                observed_tables.append(table)
                columns = {r[1] for r in con.execute('PRAGMA table_info("' + table + '")')}
                if not {"start", "end"} <= columns:
                    missing.append(kind + " event timing columns unavailable")
                    continue
                wanted = [x for x in ("start", "end", "deviceId", "srcDeviceId", "dstDeviceId",
                          "streamId", "correlationId", "bytes", "copyKind", "srcKind", "dstKind",
                          "shortName", "demangledName", "nameId") if x in columns]
                query = 'SELECT ' + ','.join('"' + c + '"' for c in wanted) + ' FROM "' + table + '"'
                for row in con.execute(query):
                    r = dict(row)
                    if r["start"] is None or r["end"] is None or r["end"] < r["start"]:
                        continue
                    name_id = next((r.get(x) for x in ("shortName", "demangledName", "nameId")
                                    if r.get(x) is not None), None)
                    name = strings.get(name_id, name_id if isinstance(name_id, str) else kind)
                    event = {"kind": kind, "start_ns": int(r["start"]), "end_ns": int(r["end"]),
                             "name": safe_symbol(name, kind)}
                    for key in ("deviceId", "srcDeviceId", "dstDeviceId", "streamId", "correlationId", "bytes"):
                        if r.get(key) is not None:
                            event[key] = int(r[key])
                    if kind == "memcpy":
                        event["direction"] = COPY_KINDS.get(r.get("copyKind"), "unknown")
                        event["source_memory"] = memory_kinds.get(r.get("srcKind"), "unavailable")
                        event["destination_memory"] = memory_kinds.get(r.get("dstKind"), "unavailable")
                    events.append(event)

    events.sort(key=lambda e: (e["start_ns"], e["end_ns"], e["kind"]))
    origin = min((e["start_ns"] for e in events), default=0)
    end = max((e["end_ns"] for e in events), default=origin)
    duration = end - origin
    grouped = defaultdict(list)
    group_bytes = defaultdict(int)
    device_intervals = defaultdict(lambda: defaultdict(list))
    kind_counts = defaultdict(int)
    for event in events:
        kind_counts[event["kind"]] += 1
        event["start_ns"] -= origin
        event["end_ns"] -= origin
        event["duration_ns"] = event["end_ns"] - event["start_ns"]
        group = (event["kind"], event.get("deviceId"), event.get("direction", event["name"]),
                 event.get("srcDeviceId"), event.get("dstDeviceId"),
                 event.get("source_memory"), event.get("destination_memory"))
        grouped[group].append(event["duration_ns"])
        group_bytes[group] += event.get("bytes", 0)
        if event["kind"] in {"kernel", "memcpy", "memset"} and "deviceId" in event:
            device_intervals[event["deviceId"]][event["kind"]].append((event["start_ns"], event["end_ns"]))
    groups = []
    for key, samples in sorted(grouped.items(), key=lambda x: sum(x[1]), reverse=True):
        kind, dev, name, src, dst, srcmem, dstmem = key
        item = {"kind": kind, "trace_device": dev, "name_or_direction": name,
                **_distribution(samples)}
        if kind == "memcpy":
            item.update({"bytes": group_bytes[key], "trace_source_device": src,
                         "trace_destination_device": dst, "source_memory": srcmem,
                         "destination_memory": dstmem,
                         "physical_transport": "unresolved; peer-copy event is not proof of direct P2P"})
        if kind in {"runtime", "driver"}:
            item["synchronization_or_wait_api"] = bool(re.search(r"Synchronize|Wait|EventQuery", name))
        groups.append(item)

    devices = []
    for device, kinds in sorted(device_intervals.items()):
        intervals = [i for group in kinds.values() for i in group]
        busy = _union_ns(intervals)
        devices.append({"trace_device": device, "gpu_activity_union_ns": busy,
                        "kernel_union_ns": _union_ns(kinds.get("kernel", [])),
                        "copy_union_ns": _union_ns(kinds.get("memcpy", [])),
                        "no_recorded_gpu_activity_ns": duration - busy,
                        "gpu_activity_fraction_of_trace_span": busy / duration if duration else None,
                        "identity": "trace-local ordinal; correlate with separate hardware inventory"})
    summary = {"schema": SCHEMA, "capture_kind": "profile", "normal_aggregate_eligible": False,
               "status": "analyzed" if events else "unavailable", "trace_span_ns": duration,
               "origin": "first exported CUDA event (relative, not wall-clock/request boundary)",
               "event_counts": dict(kind_counts), "tables_analyzed": observed_tables,
               "device_activity": devices, "activity_groups": groups,
               "timeline": {"file": "timeline.csv", "written": min(len(events), timeline_limit),
                            "total": len(events), "truncated": len(events) > timeline_limit},
               "profiling_overhead": {"present": True, "quantified": False},
               "interpretation": ["Activity durations overlap; sums are not elapsed time or simultaneous throughput",
                  "API host durations can include waiting/launch overhead; correlation is not causality",
                  "No-recorded-activity time is not proof of GPU hardware idle or a PCIe bottleneck",
                  "HtoD/DtoH events show copy records, not proof that cross-card traffic staged through host RAM"],
               "unavailable": missing + ["Physical PCIe path/direct P2P not established by this trace parser",
                  "Paging, shared-memory spill, WDDM residency and PCIe hardware counters unavailable",
                  "CPU utilization/temperature and per-card power/energy require external telemetry",
                  "Layer/tensor/KV placement requires backend loader logs and explicit configuration"]}
    fields = ["kind", "name", "start_ns", "end_ns", "duration_ns", "deviceId", "srcDeviceId",
              "dstDeviceId", "streamId", "correlationId", "bytes", "direction", "source_memory", "destination_memory"]
    with (output_dir / "timeline.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events[:timeline_limit])
    _write_json(output_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nsys", type=Path, default=None)
    parser.add_argument("--analyze-sqlite", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="profile")
    parser.add_argument("application", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.analyze_sqlite:
        result = analyze_sqlite(args.analyze_sqlite, args.output)
        print(json.dumps({"status": result["status"], "events": result["event_counts"]}))
        return
    capture = NsightCapture(args.nsys, args.output, args.label)
    application = args.application[1:] if args.application[:1] == ["--"] else args.application
    result = capture.plan()
    if application:
        # Plan contains local paths; CLI output is operator-private, not shareable metadata.
        result["private_launch_command"] = capture.launch_command(application)
        result["private_start_command"] = capture.start_command()
        result["private_stop_command"] = capture.stop_command()
        result["private_shutdown_command"] = capture.shutdown_command()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
