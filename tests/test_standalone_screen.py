"""CPU-only screen planner, fake lifecycle and concurrency-accounting checks."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import standalone_screen as screen


class PlannerTests(unittest.TestCase):
    def test_stop_after_is_operator_control_not_condition_or_plan_identity(self):
        base = ["--plan", "--models", "gemmae2b", "--strategies", "single5070", "--sizes", "512", "--clients", "1"]
        first = screen.make_cases(screen.parser().parse_args(base))
        second = screen.make_cases(screen.parser().parse_args(base + ["--stop-after", "1"]))
        self.assertEqual(first, second)
        with patch("builtins.print") as output:
            screen.main(base)
            first_plan = json.loads(output.call_args.args[0])
            screen.main(base + ["--stop-after", "1"])
            second_plan = json.loads(output.call_args.args[0])
        self.assertEqual(first_plan, second_plan)

    def test_resume_stop_after_counts_only_new_conditions_and_preserves_full_manifest(self):
        args = screen.parser().parse_args(["--models", "gemmae2b", "--strategies", "single5070", "single5060",
            "--sizes", "512", "20000", "--clients", "1", "--stop-after", "1"])
        cases = screen.make_cases(args)
        executed = []
        exported_sizes = []
        def fake_case(case, args, output, psutil_module):
            self.assertNotIn(case["case_id"], executed)
            executed.append(case["case_id"])
            result = {"condition": case, "status": "ok", "measurement_attempts": 1,
                      "measured_repeats": 1, "cleanup": [{"exit_confirmed": True}]}
            screen.write_json(output / "cases" / case["case_id"] / "result-private.json", result)
            return result
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(screen, "run_case", side_effect=fake_case), \
             patch.object(screen, "publish", side_effect=lambda output, rows:exported_sizes.append(len(rows))), \
             patch("builtins.print"):
            output = Path(directory)
            manifest = output / "manifest-private.json"
            screen.write_json(manifest, {"plan": {"cases": cases}})
            original = manifest.read_bytes()
            self.assertEqual(screen.execute_case_plan(cases, args, output, None), 1)
            self.assertEqual(len(executed), 1)
            self.assertEqual(screen.execute_case_plan(cases, args, output, None), 1)
            self.assertEqual(len(executed), 2)
            args.stop_after = None
            self.assertEqual(screen.execute_case_plan(cases, args, output, None), 2)
            self.assertEqual(len(executed), 4)
            self.assertEqual(screen.execute_case_plan(cases, args, output, None), 0)
            self.assertEqual(manifest.read_bytes(), original)
            progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["planned_conditions"], 4)
            self.assertEqual(progress["completed_conditions"], 4)
            self.assertEqual(progress["status"], "completed")
        self.assertEqual(exported_sizes, [1, 2, 3, 4])

    def test_source_fingerprints_gate_resume_and_include_nsight_only_for_profile(self):
        names = ["tools/standalone_screen.py", "tools/diagnostic_telemetry.py",
                 "wattever/lmstudio.py", "wattever/power.py", "tools/nsight_capture.py"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fixture:" + name, encoding="utf-8")
            before = screen.source_fingerprints(root=root)
            self.assertEqual(set(before), set(names[:4]))
            self.assertEqual(set(screen.source_fingerprints(profile=True, root=root)), set(names))
            existing = {"source_sha256": before}
            screen.validate_resume_manifest(existing, {"source_sha256": before})
            (root / "wattever/power.py").write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source mismatch"):
                screen.validate_resume_manifest(existing, {"source_sha256": screen.source_fingerprints(root=root)})

    def test_default_plan_has_one_repeat_and_150_conditions(self):
        args = screen.parser().parse_args([])
        cases = screen.make_cases(args)
        self.assertEqual(len(cases), 150)
        self.assertTrue(all(c["measured_repeats"] == 1 for c in cases))
        for target in (512, 20000, 80000):
            self.assertEqual(len({c["context_per_slot"] for c in cases if c["target_tokens"] == target}), 1)
        self.assertTrue(all(c["kv_type_k"] == "f16" and c["kv_type_v"] == "f16" for c in cases))

    def test_plan_never_launches_or_observes_engines(self):
        with patch.object(screen, "verify_idle", side_effect=AssertionError("no observer in plan")), \
             patch.object(screen.subprocess, "Popen", side_effect=AssertionError("no child in plan")), \
             patch("builtins.print") as output:
            self.assertEqual(screen.main(["--plan", "--models", "gemmae2b", "--sizes", "512", "--clients", "1"]), 0)
            self.assertEqual(json.loads(output.call_args.args[0])["case_count"], 7)

    def test_tensor_fixed_precision_and_no_fit(self):
        args = screen.parser().parse_args(["--models", "qwen35", "--strategies", "tensor_equal", "--clients", "2"])
        case = screen.make_cases(args)[0]
        command = screen.server_command(Path("llama-server.exe"), case, 9321, (0, 1))
        def value(flag):
            return command[command.index(flag) + 1]
        self.assertEqual(value("--fit"), "off")
        self.assertEqual(value("--gpu-layers"), "all")
        self.assertEqual(value("--ctx-size"), str(case["context_per_slot"] * 2))
        self.assertEqual(value("--cache-type-k"), "f16")
        self.assertEqual(value("--cache-type-v"), "f16")
        self.assertEqual(value("--reasoning"), "off")
        self.assertEqual(value("--spec-type"), "none")
        self.assertEqual(value("--host"), "127.0.0.1")

    def test_context_and_strata_unknown_states_fail_closed(self):
        case = {"clients": 2, "context_per_slot": 22016}
        with self.assertRaises(RuntimeError):
            screen.validate_slots([{"n_ctx": 11008}, {"n_ctx": 11008}], case)
        screen.validate_slots([{"n_ctx": 22016}, {"n_ctx": 22016}], case)
        for state in ({}, {"state": "Server ready", "uncertain": True},
                      {"state": "Server ready", "uncertain": False, "processes": [{"role": "engine"}]}):
            with self.assertRaises(RuntimeError):
                screen.validate_strata_observation(state)
        screen.validate_strata_observation({"state": "Stopped", "uncertain": False, "processes": []})

    def test_prior_completion_gate_requires_finished_native_cases_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screen.write_json(root / "progress.json", {"stages": [{"status": "completed"}, {"status": "pending"}], "planned_cases": 180})
            screen.write_json(root / "finalization.json", {"partial": False, "cases": 180})
            with self.assertRaisesRegex(RuntimeError, "36 Strata"):
                screen.validate_prior_completion(root)
            screen.write_json(root / "progress.json", {"stages": [{"status": "completed"}], "planned_cases": 180})
            screen.write_json(root / "finalization.json", {"partial": True, "cases": 144})
            with self.assertRaisesRegex(RuntimeError, "partial"):
                screen.validate_prior_completion(root)

    def test_group_energy_is_not_sum_of_overlapping_request_windows(self):
        requests = [{"status": "ok", "completion_tokens": 100, "gpu_energy_j": 800},
                    {"status": "ok", "completion_tokens": 200, "gpu_energy_j": 900}]
        actual_window = {"gpu_energy_j": 1000, "gpu_power_complete": True, "inventory_complete": True,
                         "gpus": [{"index": 0, "gpu_energy_j": 600}, {"index": 1, "gpu_energy_j": 400}]}
        group = screen.group_statistics(requests, 5, 15, actual_window)
        self.assertEqual(group["gpu_energy_j"], 1000)
        self.assertEqual(group["aggregate_output_tps"], 30)
        self.assertAlmostEqual(group["gpu_energy_per_output_token_including_prefill_j"], 1000 / 300)
        requests[1]["completion_tokens_estimated"] = True
        self.assertIsNone(screen.group_statistics(requests, 5, 15, actual_window)["aggregate_output_tps"])
        actual_window["inventory_complete"] = False
        self.assertIsNone(screen.group_statistics(requests, 5, 15, actual_window)["gpu_energy_j"])

    def test_gemma_locality_requires_all_seed_and_tail_assignments(self):
        self.assertFalse(screen.placement_evidence("tensor split 1,2")["gemma_kv_locality_verified"])
        local = "\n".join("load_tensors: layer %d assigned to device CUDA1" % i for i in range(13, 35))
        self.assertTrue(screen.placement_evidence(local)["gemma_weight_layers_13_to_34_colocated"])
        self.assertIsNone(screen.placement_evidence(local)["gemma_kv_locality_verified"])
        self.assertTrue(screen.placement_evidence(local, (1, 0))["gemma_weight_colocation_on_5070"])
        self.assertFalse(screen.placement_evidence(local, (0, 1))["gemma_weight_colocation_on_5070"])
        different = local.replace("layer 14 assigned to device CUDA1", "layer 14 assigned to device CUDA0")
        self.assertFalse(screen.placement_evidence(different)["gemma_weight_layers_13_to_34_colocated"])

    def test_invalid_or_cached_prompt_is_not_silently_comparable(self):
        case = {"max_tokens": 256, "context_per_slot": 1536}
        missing = screen.validate_request_evidence({"status": "ok", "completion_tokens": 100}, case)
        self.assertFalse(missing["actual_prompt_count_verified"])
        cached = screen.validate_request_evidence({"status": "ok", "prompt_tokens": 512,
            "completion_tokens": 100, "completion_tokens_estimated": False, "reported_timings": {"cache_n": 500}}, case)
        self.assertTrue(cached["positive_cache_reuse_observed"])
        self.assertEqual(cached["prefill_comparison_eligibility"], "excluded_cache_reuse")
        unknown = screen.validate_request_evidence({"status": "ok", "prompt_tokens": 512,
            "completion_tokens": 100, "completion_tokens_estimated": False}, case)
        self.assertIsNone(unknown["cold_prefill_counter_verified"])

    def test_resume_halts_on_unconfirmed_child_exit(self):
        case = {"strategy": "independent"}
        saved = {"condition": case, "status": "interrupted", "measurement_attempts": 1,
                 "cleanup": [{"exit_confirmed": True}, {"exit_confirmed": False}]}
        with self.assertRaisesRegex(RuntimeError, "unconfirmed"):
            screen.validate_saved_result(saved, case)
        saved["cleanup"][1]["exit_confirmed"] = True
        screen.validate_saved_result(saved, case)

    def test_backend_duration_kept_separate_from_client_ttft(self):
        evidence = screen.backend_timing_evidence("slot print_timings: prompt eval time = 200.00 ms / 512 tokens (2560.00 tokens per second)")
        self.assertEqual(evidence["parsed"][0]["elapsed_ms"], 200)
        self.assertIn("no retrospective subtraction", evidence["clock_alignment"])


class FakePsutil:
    """Birth/listener fixture only; actual harmless processes remain Popen-owned."""
    class NoSuchProcess(Exception):
        pass

    def __init__(self):
        self.pid = None
        self.port = None

    def Process(self, pid):
        return SimpleNamespace(create_time=lambda: 42.0)

    def net_connections(self, kind):
        return [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=self.port), pid=self.pid)]


class OwnedLifecycleTests(unittest.TestCase):
    def fake_command(self, port):
        # Harmless local HTTP process. No model, CUDA library or Strata import.
        # Isolate framework/site startup hooks on macOS runners; the fixture
        # needs only a minimal HTTP response, not http.server's import graph.
        code = (
            "import socket,sys\n"
            "listener=socket.socket()\n"
            "listener.bind(('127.0.0.1',int(sys.argv[-1])))\n"
            "listener.listen(4)\n"
            "body=b'{\"status\":\"ok\"}'\n"
            "reply=b'HTTP/1.0 200 OK\\r\\nContent-Length: '+str(len(body)).encode()+b'\\r\\nConnection: close\\r\\n\\r\\n'+body\n"
            "while True:\n"
            " connection,address=listener.accept()\n"
            " with connection:\n"
            "  connection.recv(4096)\n"
            "  connection.sendall(reply)\n")
        return [sys.executable, "-I", "-S", "-c", code, "--port", str(port)]

    def test_owned_fake_child_is_stopped_and_other_listener_preserved(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen(1)
            other_port = other.getsockname()[1]
            port = screen.free_port()
            fake = FakePsutil()
            fake.port = port
            server = screen.OwnedServer(self.fake_command(port), (1,), directory, fake)
            try:
                server.start()
                fake.pid = server.process.pid
                try:
                    server.ready(timeout=20)
                except Exception as exc:
                    details=server.log_path.read_text(encoding="utf-8",errors="replace")
                    self.fail("Owned fake readiness failed: %r; child=%r; log=%s" % (exc,server.process.poll(),details))
                self.assertIsNone(server.process.poll())
                self.assertEqual(screen.http_json(server.base, "/health")["status"], "ok")
            finally:
                cleanup = server.stop()
            self.assertTrue(cleanup["exit_confirmed"])
            self.assertIsNotNone(server.process.poll())
            with socket.create_connection(("127.0.0.1", other_port), timeout=1):
                pass

    def test_occupied_port_is_not_stolen(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket() as other:
            other.bind(("127.0.0.1", 0))
            other.listen(1)
            port = other.getsockname()[1]
            server = screen.OwnedServer(self.fake_command(port), (0,), directory, FakePsutil())
            with self.assertRaises(OSError):
                server.start()
            self.assertIsNone(server.process)
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass


class FakeCaptureIntegrationTests(unittest.TestCase):
    def test_independent_two_clients_one_energy_window_and_cleanup_despite_counter_failure(self):
        args = screen.parser().parse_args(["--models", "gemmae2b", "--strategies", "independent", "--sizes", "512", "--clients", "2"])
        case = screen.make_cases(args)[0]
        servers = []
        measurements = []
        energy_windows = []

        class Server:
            def __init__(self, command, indices, directory, psutil_module):
                self.directory = Path(directory)
                self.log_path = self.directory / "server.log"
                self.indices = indices
                self.base = "http://127.0.0.1:" + str(12000 + len(servers))
                self.process = SimpleNamespace(pid=1200 + len(servers))
                self.engine_pid = self.process.pid
                self.stopped = False
                servers.append(self)

            def start(self):
                self.directory.mkdir(parents=True)
                self.log_path.write_text("load_tensors: offloaded 36/36 layers to GPU\n", encoding="utf-8")

            def ready(self, timeout):
                pass

            def stop(self):
                self.stopped = True
                return {"pid": self.process.pid, "exit_confirmed": True}

        class Power:
            def __init__(self, **kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def window(self, t0, t1):
                energy_windows.append((t0, t1))
                return {"inventory_complete": True, "gpu_power_complete": True,
                        "gpu_energy_j": 10, "gpus": [{"index": 0}, {"index": 1}]}

            def scope_metadata(self):
                return {"gpu_indices": [0, 1], "inventory_complete": True}

            def history(self):
                return []

            def summary(self):
                return {}

        class Telemetry:
            def __init__(self, **kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                raise RuntimeError("fixture supplementary-counter cleanup failure")

            def window(self, t0, t1):
                return {"available": False, "reason": "CPU-only fixture"}

        def measured(*positional, **kwargs):
            measurements.append({"max_tokens": positional[2], **kwargs})
            return {"t_start": 1, "t_first_token": 2, "t_end": 3,
                    "completion_tokens": 100, "completion_tokens_estimated": False,
                    "prompt_tokens": 512, "decode_token_count": 99}

        def http(base, route, *positional, **kwargs):
            return [{"n_ctx": case["context_per_slot"]}] if route == "/slots" else {}

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(screen, "OwnedServer", Server), \
             patch.object(screen, "verify_idle", return_value={}), \
             patch.object(screen, "verify_no_standalone_engines", return_value={}), \
             patch.object(screen, "http_json", side_effect=http), \
             patch.object(screen, "canonical_prompt", return_value={"prompt": "fixture", "prompt_sha256": "fixture"}), \
             patch.object(screen.time, "sleep"), \
             patch("wattever.power.PowerSampler", Power), \
             patch("wattever.lmstudio.measure_completion", side_effect=measured), \
             patch("tools.diagnostic_telemetry.DiagnosticTelemetry", Telemetry):
            with self.assertRaisesRegex(RuntimeError, "Capture cleanup failed"):
                screen.run_case(case, args, Path(directory), FakePsutil())
            result = json.loads((Path(directory) / "cases" / case["case_id"] / "result-private.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["measured_repeats"], 1)
        self.assertEqual(len(result["warmups"]), 2)
        self.assertEqual([m["max_tokens"] for m in measurements].count(8), 2)
        self.assertEqual([m["max_tokens"] for m in measurements].count(256), 2)
        self.assertEqual(len(energy_windows), 1)
        self.assertEqual(result["group"]["gpu_energy_j"], 10)
        self.assertTrue(all(s.stopped for s in servers))
        self.assertEqual(len(result["capture_cleanup_errors"]), 1)


class ProfiledOwnershipTests(unittest.TestCase):
    def make_capture(self, events):
        class Capture:
            def __init__(self, **kwargs):
                pass

            def _check_version(self):
                events.append("version_preflight")

            def launch_command(self, command):
                events.append("build_wrapper")
                return command

            def adopt_launcher(self, process):
                events.append("adopt_launcher")

            def start(self, phase):
                events.append("trace_start")

            def stop(self):
                events.append("trace_stop")

            def close(self, **kwargs):
                events.append("session_close_kill_false")
        return Capture

    def make_server(self, directory, events):
        command = [sys.executable, "--alias", "fake-profile-case", "--port", "19321"]
        with patch("tools.nsight_capture.NsightCapture", self.make_capture(events)):
            return screen.ProfiledServer(command, (1, 0), directory, FakePsutil(), Path("fake-nsys.exe"))

    def test_version_preflight_precedes_any_popen(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            server = self.make_server(directory, events)
            def base_start():
                events.append("owned_popen")
                server.process = SimpleNamespace(pid=91)
            with patch.object(screen.OwnedServer, "start", side_effect=base_start):
                server.start()
        self.assertEqual(events, ["version_preflight", "build_wrapper", "owned_popen", "adopt_launcher"])

    def test_healthy_foreign_listener_is_rejected_without_http_call(self):
        events = []
        with tempfile.TemporaryDirectory() as directory:
            server = self.make_server(directory, events)
            server.process = SimpleNamespace(pid=91, poll=lambda: None)
            server.born = 42
            child = SimpleNamespace(pid=92, create_time=lambda:43,
                exe=lambda:sys.executable, cmdline=lambda:[sys.executable, "--alias", "fake-profile-case"],
                is_running=lambda:True)
            class Processes(FakePsutil):
                def Process(self, pid):
                    if pid == 91:
                        return SimpleNamespace(create_time=lambda:42, children=lambda recursive:[child])
                    return child

                def net_connections(self, kind):
                    return [SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(port=19321), pid=999)]
            server.psutil = Processes()
            with patch.object(screen, "http_json", side_effect=AssertionError("must not contact foreign listener")):
                with self.assertRaisesRegex(RuntimeError, "not the verified"):
                    server.ready(2)
            self.assertEqual(server.engine_pid, 92)

    def test_trace_stops_then_owned_target_then_session_then_launcher(self):
        events = []
        running = [True]
        with tempfile.TemporaryDirectory() as directory:
            server = self.make_server(directory, events)
            def terminate():
                events.append("target_terminate")
                running[0] = False
            child = SimpleNamespace(pid=92, create_time=lambda:43,
                is_running=lambda:running[0], terminate=terminate, wait=lambda timeout:0)
            server.engine, server.engine_born = child, 43
            server.psutil = SimpleNamespace(Process=lambda pid:child, NoSuchProcess=FakePsutil.NoSuchProcess)
            server.collection_started = True
            def base_stop():
                events.append("launcher_stop")
                return {"exit_confirmed": True, "pid": 91}
            with patch.object(screen.OwnedServer, "stop", side_effect=base_stop):
                result = server.stop()
        self.assertTrue(result["model_exit_confirmed"])
        self.assertTrue(result["exit_confirmed"])
        self.assertEqual(events, ["trace_stop", "target_terminate", "session_close_kill_false", "launcher_stop"])


if __name__ == "__main__":
    unittest.main()
