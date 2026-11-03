"""Synthetic SQLite and command-plan tests; never invokes CUDA/Nsight."""
import csv
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.nsight_capture import NsightCapture, NsightUnavailable, analyze_sqlite, safe_symbol


class NsightPlanTests(unittest.TestCase):
    def test_absent_session_is_benign_only_after_owned_exit_and_scoped_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = NsightCapture(__file__, tmp, execute=True)
            capture.launcher = SimpleNamespace(poll=lambda:0)
            results = [subprocess.CompletedProcess([], 1, "", "There are no active sessions.\n"),
                       subprocess.CompletedProcess([], 1, "", "Session is not found.\n")]
            with patch.object(capture, "_check_version"), patch("tools.nsight_capture.subprocess.run", side_effect=results) as run:
                capture.close(owned_target_exit_confirmed=True)
            self.assertEqual(run.call_args_list[1].args[0], capture.status_command())
            self.assertIn("--session=" + capture.session, run.call_args_list[1].args[0])
            self.assertIn("--kill=false", run.call_args_list[0].args[0])
            self.assertTrue(capture._closure_verification["scoped_session_absent"])
            self.assertEqual(capture.commands[0]["returncode"], 1)
            self.assertTrue(capture.commands[0]["already_closed_session_verified"])
            self.assertEqual(capture.failures, [])

    def test_absent_warning_without_owned_exit_remains_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture = NsightCapture(__file__, tmp, execute=True)
            capture.launcher = SimpleNamespace(poll=lambda:0)
            with patch.object(capture, "_check_version"), patch("tools.nsight_capture.subprocess.run",
                return_value=subprocess.CompletedProcess([], 1, "", "There are no active sessions.")) as run:
                with self.assertRaises(NsightUnavailable):
                    capture.close()
            self.assertEqual(run.call_count, 1)
            self.assertIsNone(capture._closure_verification)

    def test_unknown_or_active_scoped_session_does_not_suppress_shutdown_failure(self):
        for status in (subprocess.CompletedProcess([], 0, "Session is running.", ""),
                       subprocess.CompletedProcess([], 1, "", "Access is denied.")):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                capture = NsightCapture(__file__, tmp, execute=True)
                capture.launcher = SimpleNamespace(poll=lambda:0)
                with patch.object(capture, "_check_version"), patch("tools.nsight_capture.subprocess.run", side_effect=[
                    subprocess.CompletedProcess([], 1, "", "There are no active sessions."), status]):
                    with self.assertRaises(NsightUnavailable):
                        capture.close(owned_target_exit_confirmed=True)
                self.assertIsNone(capture._closure_verification)
    def test_unique_scoped_commands_and_disabled_execution(self):
        # Any ordinary existing file is sufficient for a nonexecuting plan.
        with tempfile.TemporaryDirectory() as tmp:
            cap = NsightCapture(__file__, tmp)
            command = cap.launch_command(["fake-owned-server.exe", "--port", "19001"])
            self.assertIn("--system-wide=false", command)
            self.assertIn("--cuda-event-trace=false", command)
            self.assertIn("--trace=cuda,nvtx", command)
            self.assertIn("--sample=none", cap.start_command())
            self.assertIn("--cpuctxsw=none", cap.start_command())
            self.assertIn("--discard-environment=true", cap.start_command())
            self.assertIn("--kill=false", cap.shutdown_command())
            self.assertFalse(cap.plan()["normal_aggregate_eligible"])
            self.assertFalse(cap.plan()["execution_enabled"])
            with self.assertRaises(ValueError):
                cap.launch(["fake-owned-server.exe"])
            self.assertFalse(cap.directory.exists())
            other = NsightCapture(__file__, tmp)
            self.assertNotEqual(cap.session, other.session)
            self.assertNotEqual(cap.directory, other.directory)

    def test_symbols_redact_paths_and_spreadsheet_formulas(self):
        for value in (r"C:\Users\secret\model.gguf", "https://private.example/key", "=secret()", "+secret()"):
            clean = safe_symbol(value)
            self.assertTrue(clean.startswith("symbol_"))
            self.assertNotIn("secret", clean)
        self.assertEqual(safe_symbol("cudaStreamSynchronize"), "cudaStreamSynchronize")
        self.assertEqual(safe_symbol("void kernel<float>(int)"), "void kernel<float>(int)")


class NsightSqliteTests(unittest.TestCase):
    def test_memory_types_come_from_exported_enum_not_cupti_numeric_assumptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            db=root/"trace.sqlite"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript('''
                    CREATE TABLE ENUM_CUDA_MEM_KIND(id INTEGER,name TEXT,label TEXT);
                    INSERT INTO ENUM_CUDA_MEM_KIND VALUES
                      (0,'CUDA_MEMOPR_MEMORY_KIND_PAGEABLE','Pageable'),
                      (42,'CUDA_MEMOPR_MEMORY_KIND_PINNED','Pinned'),
                      (2,'CUDA_MEMOPR_MEMORY_KIND_DEVICE','Device');
                    CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY(start INTEGER,end INTEGER,deviceId INTEGER,bytes INTEGER,copyKind INTEGER,srcKind INTEGER,dstKind INTEGER);
                    INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES
                      (0,10,0,4096,1,42,2),(12,20,0,256,2,2,0),(22,30,0,128,2,2,77);
                ''')
            result=analyze_sqlite(db,root/"public")
            groups=result["activity_groups"]
            htod=next(g for g in groups if g["name_or_direction"]=="HtoD")
            self.assertEqual((htod["source_memory"],htod["destination_memory"]),("pinned","device"))
            dtoh=[g["destination_memory"] for g in groups if g["name_or_direction"]=="DtoH"]
            self.assertCountEqual(dtoh,["pageable","unavailable"])

    def test_overlap_copy_transport_and_sanitized_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "private.sqlite"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript('''
                    CREATE TABLE StringIds(id INTEGER,value TEXT);
                    INSERT INTO StringIds VALUES(1,'kernel_gdn'),(2,'cudaStreamSynchronize'),(3,'C:\\private\\model');
                    CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL(start INTEGER,end INTEGER,deviceId INTEGER,streamId INTEGER,shortName INTEGER);
                    INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES(100,110,0,1,1),(105,115,0,2,3);
                    CREATE TABLE CUPTI_ACTIVITY_KIND_MEMCPY(start INTEGER,end INTEGER,deviceId INTEGER,srcDeviceId INTEGER,dstDeviceId INTEGER,bytes INTEGER,copyKind INTEGER,srcKind INTEGER,dstKind INTEGER);
                    INSERT INTO CUPTI_ACTIVITY_KIND_MEMCPY VALUES(112,118,0,0,1,4096,10,3,3);
                    CREATE TABLE CUPTI_ACTIVITY_KIND_RUNTIME(start INTEGER,end INTEGER,nameId INTEGER);
                    INSERT INTO CUPTI_ACTIVITY_KIND_RUNTIME VALUES(100,120,2);
                ''')
            result = analyze_sqlite(db, root / "shareable")
            device = result["device_activity"][0]
            self.assertEqual(result["trace_span_ns"], 20)
            self.assertEqual(device["kernel_union_ns"], 15)
            self.assertEqual(device["gpu_activity_union_ns"], 18)
            self.assertEqual(device["no_recorded_gpu_activity_ns"], 2)
            self.assertEqual(device["gpu_activity_fraction_of_trace_span"], .9)
            copy = next(x for x in result["activity_groups"] if x["kind"] == "memcpy")
            self.assertEqual(copy["bytes"], 4096)
            self.assertEqual(copy["name_or_direction"], "PtoP")
            self.assertTrue(copy["physical_transport"].startswith("unresolved"))
            self.assertFalse(result["normal_aggregate_eligible"])
            public = (root / "shareable/summary.json").read_text()
            self.assertNotIn("C:\\private", public)
            with (root / "shareable/timeline.csv").open(encoding="utf-8", newline="") as handle:
                timeline = list(csv.DictReader(handle))
            self.assertEqual(len(timeline), 4)
            self.assertTrue(all(int(x["start_ns"]) >= 0 for x in timeline))
            self.assertTrue(any(x["name"].startswith("kernel_") for x in timeline))
            with self.assertRaises(FileExistsError):
                analyze_sqlite(db, root / "shareable")

    def test_missing_data_and_truncation_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "private.sqlite"
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("CREATE TABLE irrelevant(secret TEXT)")
                con.execute("INSERT INTO irrelevant VALUES ('never export me')")
            result = analyze_sqlite(db, root / "empty")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["event_counts"], {})
            self.assertIn("kernel events not exported", result["unavailable"])
            self.assertNotIn("never export me", json.dumps(result))
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript("CREATE TABLE CUPTI_ACTIVITY_KIND_KERNEL(start INTEGER,end INTEGER,deviceId INTEGER);"
                                  "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL VALUES(0,1,0),(2,3,0);")
            result = analyze_sqlite(db, root / "limited", timeline_limit=1)
            self.assertEqual(result["event_counts"]["kernel"], 2)
            self.assertTrue(result["timeline"]["truncated"])
            self.assertEqual(result["timeline"]["written"], 1)


if __name__ == "__main__":
    unittest.main()
