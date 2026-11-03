"""Deferred CPU-only fixed-output configuration checks, without model loading.

These tests use published configurators and row definitions without private adapters.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import standalone_screen as core

from tools import fixed_output_screen as fixed


class FixedOutputConfigurationTests(unittest.TestCase):
    def test_default_plan_requests_fixed_output_without_claiming_it_was_verified(self):
        with patch.object(core, "verify_idle", side_effect=AssertionError("no engine checks in plan")), \
             patch.object(core.subprocess, "Popen", side_effect=AssertionError("no launches in plan")), \
             patch("builtins.print") as output:
            self.assertEqual(fixed.main(["--plan"]), 0)
        plan = json.loads(output.call_args.args[0])
        self.assertEqual(plan["case_count"], 24)
        for case in plan["cases"]:
            self.assertEqual(case["output_stop_policy"], "ignore_eos_requested_256")
            self.assertEqual(case["max_tokens"], 256)
            self.assertIsNone(case["fixed_output_request"]["actual_output_count_verified"])
            self.assertIsNone(case["fixed_output_request"]["actual_server_ignore_eos_verified"])

    def test_command_adds_only_ignore_eos_with_identical_context_and_sampling(self):
        argv = ["--models", "gemmae2b", "--strategies", "single5070", "--sizes", "5000", "--clients", "1"]
        baseline = core.make_cases(core.parser().parse_args(argv))[0]
        natural = core.server_command(Path("llama-server.exe"), baseline, 19321, (0,))
        with fixed.registered_fixed_output_configuration():
            condition = core.make_cases(core.parser().parse_args(argv))[0]
            command_case = dict(condition, case_id=baseline["case_id"])
            command = core.server_command(Path("llama-server.exe"), command_case, 19321, (0,))
            self.assertEqual(command, natural + ["--ignore-eos"])
            for key in ("context_per_slot", "seed", "temperature", "top_p", "reasoning", "mtp", "kv_type_k", "kv_type_v"):
                self.assertEqual(condition[key], baseline[key])
            self.assertNotEqual(condition["case_id"], baseline["case_id"])
            limited = core.make_cases(core.parser().parse_args(argv + ["--stop-after", "1"]))[0]
            self.assertEqual(condition, limited)
            with self.assertRaises(ValueError):
                core.make_cases(core.parser().parse_args(argv + ["--max-tokens", "128"]))
        self.assertEqual(core.server_command(Path("llama-server.exe"), baseline, 19321, (0,)), natural)

    def test_row_annotations_and_both_configuration_sources_are_preserved(self):
        with fixed.registered_fixed_output_configuration():
            args = core.parser().parse_args(["--models", "lfm25", "--strategies", "row_favor5070", "--sizes", "10000", "--clients", "2"])
            condition = core.make_cases(args)[0]
            self.assertEqual(condition["strategy_definition"]["split_mode"], "row")
            self.assertEqual(condition["strategy_definition"]["main_gpu_physical_index"], 0)
            self.assertEqual(len(condition["configuration_stack"]), 2)
            self.assertEqual(condition["configuration_stack"][0]["sha256"], fixed.rows.wrapper_sha256())
            self.assertEqual(condition["configuration_stack"][1]["sha256"], fixed.wrapper_sha256())
            command = core.server_command(Path("llama-server.exe"), condition, 19321, (0, 1))
            self.assertEqual(command[command.index("--split-mode") + 1], "row")
            self.assertEqual(command.count("--ignore-eos"), 1)
        self.assertEqual(len(core.make_cases(core.parser().parse_args([]))), 150)
        with self.assertRaises(ValueError):
            fixed.configure_command(["server", "--ignore-eos"])


if __name__ == "__main__":
    unittest.main()
