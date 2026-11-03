"""Separate fixed-budget output configuration around the frozen screening core.

Default plans 5k/10k/40k with one/two clients on single5070. Select screened base
or classic-row strategies explicitly. Only --ignore-eos is appended to the server
command; weights, KV, context, prompts, seed, reasoning, MTP and measurement
methods remain inherited. The request is256 output tokens; actual counts and
effective server ignore_eos must be verified from the resulting artifacts.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import standalone_screen as core

_row_spec = importlib.util.spec_from_file_location("fixed_output_row_definitions", Path(__file__).with_name("run_row_screen.py"))
rows = importlib.util.module_from_spec(_row_spec)
sys.modules[_row_spec.name] = rows
_row_spec.loader.exec_module(rows)

OUTPUT_STOP_POLICY = "ignore_eos_requested_256"


def wrapper_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def annotate_cases(cases, source_sha256):
    annotated = []
    for original in cases:
        if original["max_tokens"] != 256:
            raise ValueError("This fixed-output protocol requires max_tokens256.")
        case = dict(original)
        stack = []
        if case["strategy"] in rows.ROW_STRATEGIES:
            case = rows.annotate_row_cases([case], rows.wrapper_sha256())[0]
            stack.append({"path": case["configuration_wrapper"], "sha256": case["configuration_wrapper_sha256"]})
        stack.append({"path": "untracked/run_fixed_output_screen.py", "sha256": source_sha256})
        case.update(output_stop_policy=OUTPUT_STOP_POLICY,
            configuration_wrapper="untracked/run_fixed_output_screen.py",
            configuration_wrapper_sha256=source_sha256, configuration_stack=stack,
            sampling_condition={"temperature":case["temperature"], "top_p":case["top_p"],
                "seed":case["seed"], "ignore_eos_requested":True, "requested_max_tokens":256},
            fixed_output_request={"requested_output_tokens":256, "ignore_eos_requested":True,
                "actual_output_count_verified":None, "actual_server_ignore_eos_verified":None,
                "verification_policy":"Every measured request must report exactly256 completion tokens with server usage, and every owned server's retained props/slot params must explicitly report ignore_eos=true. The command alone is not proof.",
                "comparison_policy":"Separate output-stop condition from natural-EOS screens. Screen scores select strategies only; do not pool their throughput or energy with this protocol."})
        case.pop("case_id", None)
        case["case_id"] = core.digest(case)[:16]
        annotated.append(case)
    return annotated


def configure_command(command):
    configured = list(command)
    if "--ignore-eos" in configured:
        raise ValueError("Frozen command already contains --ignore-eos; review the configuration contract.")
    return configured + ["--ignore-eos"]


@contextmanager
def registered_fixed_output_configuration():
    original_strategies = dict(core.STRATEGIES)
    original_parser, original_make_cases, original_server_command = core.parser, core.make_cases, core.server_command
    source_sha256 = wrapper_sha256()

    def parser():
        result = original_parser()
        result.description = __doc__
        for action in result._actions:
            if action.dest == "strategies":
                action.default = ["single5070"]
            elif action.dest == "sizes":
                action.default = [5000, 10000, 40000]
        result.set_defaults(output=core.INVESTIGATION / (
            "screening-fixed256-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
        return result

    def make_cases(args):
        return annotate_cases(original_make_cases(args), source_sha256)

    def server_command(*args, **kwargs):
        return configure_command(original_server_command(*args, **kwargs))

    try:
        core.STRATEGIES.update(rows.ROW_STRATEGIES)
        core.parser, core.make_cases, core.server_command = parser, make_cases, server_command
        yield
    finally:
        core.STRATEGIES.clear()
        core.STRATEGIES.update(original_strategies)
        core.parser, core.make_cases, core.server_command = original_parser, original_make_cases, original_server_command


def main(argv=None):
    with registered_fixed_output_configuration():
        return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
