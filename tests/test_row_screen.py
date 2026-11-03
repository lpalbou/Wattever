"""CPU-only plan/registration checks; never starts a model or CUDA process."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import standalone_screen as core

from tools import classic_row_screen as row_screen


class RowConfigurationTests(unittest.TestCase):
    def test_default_is_24_row_equal_conditions_and_no_execution(self):
        with patch.object(core, "verify_idle", side_effect=AssertionError("plan must not observe an engine")), \
             patch.object(core.subprocess, "Popen", side_effect=AssertionError("plan must not launch")), \
             patch("builtins.print") as printed:
            self.assertEqual(row_screen.main(["--plan"]), 0)
        plan = json.loads(printed.call_args.args[0])
        self.assertEqual(plan["case_count"], 24)
        self.assertEqual({case["strategy"] for case in plan["cases"]}, {"row_equal"})
        for case in plan["cases"]:
            self.assertEqual(case["measured_repeats"], 1)
            self.assertEqual(case["strategy_definition"]["main_gpu_physical_index"], 0)
            self.assertFalse(case["strategy_definition"]["experimental_tensor_kv_sharding"])
            self.assertEqual(case["configuration_wrapper_sha256"], row_screen.wrapper_sha256())

    def test_registration_restores_base_150_plan_and_only_row_cli_choices(self):
        initial = dict(core.STRATEGIES)
        with row_screen.registered_row_configuration():
            with patch("sys.stderr"):
                with self.assertRaises(SystemExit):
                    core.parser().parse_args(["--strategies", "layer_equal"])
            self.assertEqual(len(core.make_cases(core.parser().parse_args(["--strategies", "row_equal", "row_favor5070"]))), 48)
        self.assertEqual(core.STRATEGIES, initial)
        self.assertEqual(len(core.make_cases(core.parser().parse_args([]))), 150)

    def test_configuration_command_and_wrapper_hash_are_distinct_conditions(self):
        with row_screen.registered_row_configuration():
            args = core.parser().parse_args(["--models", "gemmae2b", "--sizes", "512", "--clients", "1"])
            case = core.make_cases(args)[0]
            command = core.server_command(Path("llama-server.exe"), case, 19321, (0, 1))
            def value(flag):
                return command[command.index(flag) + 1]
            self.assertEqual(value("--split-mode"), "row")
            self.assertEqual(value("--tensor-split"), "1,1")
            self.assertEqual(value("--main-gpu"), "0")
            self.assertEqual(value("--fit"), "on")
            self.assertEqual(value("--gpu-layers"), "auto")
            self.assertEqual(value("--cache-type-k"), "f16")
            self.assertEqual(value("--host"), "127.0.0.1")
            changed = row_screen.annotate_row_cases([case], "changed-wrapper-source")[0]
            self.assertNotEqual(case["case_id"], changed["case_id"])
            limited = core.make_cases(core.parser().parse_args(["--models", "gemmae2b", "--sizes", "512", "--clients", "1", "--stop-after", "1"]))[0]
            self.assertEqual(case, limited)


if __name__ == "__main__":
    unittest.main()
