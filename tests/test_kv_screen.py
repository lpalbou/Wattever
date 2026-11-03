"""CPU-only KV configuration checks; no CUDA, model or service operations.

These tests use the published study configurator; no private adapters are imported.
"""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import standalone_screen as core

from tools import kv_cache_screen as kv_screen


class KVConfigurationTests(unittest.TestCase):
    def test_default_16_condition_plan_has_unverified_requested_q8_and_no_execution(self):
        with patch.object(core, "verify_idle", side_effect=AssertionError("plan must not observe engines")), \
             patch.object(core.subprocess, "Popen", side_effect=AssertionError("plan must not launch")), \
             patch("builtins.print") as output:
            self.assertEqual(kv_screen.main(["--plan"]), 0)
        plan = json.loads(output.call_args.args[0])
        self.assertEqual(plan["case_count"], 16)
        self.assertEqual({c["model"] for c in plan["cases"]}, {"lfm25", "qwen27"})
        self.assertEqual({c["strategy"] for c in plan["cases"]}, {"single5070", "independent"})
        self.assertEqual({c["target_tokens"] for c in plan["cases"]}, {20000, 80000})
        for case in plan["cases"]:
            self.assertEqual(case["kv_type_k"], "q8_0")
            self.assertEqual(case["kv_type_v"], "q8_0")
            self.assertIsNone(case["kv_precision_treatment"]["actual_precision_verified"])
            self.assertEqual(case["configuration_wrapper_sha256"], kv_screen.wrapper_sha256())
            self.assertEqual(case["measured_repeats"], 1)

    def test_only_two_cache_arguments_change_and_context_prompt_recipe_stays_fixed(self):
        args = core.parser().parse_args(["--models", "qwen27", "--strategies", "single5070", "--sizes", "80000", "--clients", "1"])
        baseline = core.make_cases(args)[0]
        base_command = core.server_command(Path("llama-server.exe"), baseline, 19321, (0,))
        with kv_screen.registered_kv_configuration():
            q8 = core.make_cases(args)[0]
            # A common alias isolates command configuration differences from
            # the intentionally different precision condition identifier.
            command_case = dict(q8, case_id=baseline["case_id"])
            command = core.server_command(Path("llama-server.exe"), command_case, 19321, (0,))
            changed = [i for i, pair in enumerate(zip(base_command, command)) if pair[0] != pair[1]]
            self.assertEqual(changed, [base_command.index("--cache-type-k") + 1, base_command.index("--cache-type-v") + 1])
            self.assertEqual(q8["context_per_slot"], baseline["context_per_slot"])
            for key in ("seed", "temperature", "top_p", "reasoning", "mtp", "max_tokens", "model", "target_tokens", "clients"):
                self.assertEqual(q8[key], baseline[key])
            self.assertNotEqual(q8["case_id"], baseline["case_id"])
        self.assertEqual(core.server_command(Path("llama-server.exe"), baseline, 19321, (0,)), base_command)

    def test_command_contract_and_wrapper_hash_fail_or_separate_conditions(self):
        with self.assertRaises(ValueError):
            kv_screen.configure_cache_command(["server", "--cache-type-k", "f16"])
        with self.assertRaises(ValueError):
            kv_screen.configure_cache_command(["server", "--cache-type-k", "q4_0", "--cache-type-v", "f16"])
        with kv_screen.registered_kv_configuration():
            case = core.make_cases(core.parser().parse_args([]))[0]
            changed = kv_screen.annotate_kv_cases([case], "different-wrapper-source")[0]
            self.assertNotEqual(case["case_id"], changed["case_id"])
            limited = core.make_cases(core.parser().parse_args(["--stop-after", "1"]))[0]
            self.assertEqual(case, limited)


if __name__ == "__main__":
    unittest.main()
