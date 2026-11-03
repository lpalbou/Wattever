"""Register classic llama.cpp row strategies around the frozen screening core.

Plan only by default. All engine ownership, prior-cohort/idle gates, timings,
fixed contexts, one measured repeat, all-card power and cleanup come unchanged
from tools.standalone_screen. This configuration wrapper does not load weights.

Example (after the preceding cohort/report finishes):
  <flash-venv-python> tools/classic_row_screen.py --run --stop-after 1 \
      --models gemmae2b --sizes 512 20000 80000 --clients 1 2 \
      --output untracked/communication-investigation/screening-row
Use the identical selection with --resume to continue the saved full plan.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import standalone_screen as core


ROW_STRATEGIES = {
    "row_equal": core.Strategy((0, 1), "row", "1,1"),
    "row_favor5070": core.Strategy((0, 1), "row", "2,1"),
}


def wrapper_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def annotate_row_cases(cases, source_sha256):
    annotated = []
    for original in cases:
        case = dict(original)
        strategy = ROW_STRATEGIES[case["strategy"]]
        case["configuration_wrapper"] = "tools/classic_row_screen.py"
        case["configuration_wrapper_sha256"] = source_sha256
        case["strategy_definition"] = {
            "execution_mode": "classic row weight partition",
            "split_mode": strategy.split,
            "tensor_split_fractions": strategy.fractions,
            "physical_gpu_indices": list(strategy.physical_indices),
            "visible_to_physical": {"0": 0, "1": 1},
            "main_gpu_visible_index": 0,
            "main_gpu_physical_index": 0,
            "main_gpu_role": "PNY RTX5070 Ti; pinned CUDA order is PCI_BUS_ID",
            "kv_intermediate_placement_policy": "Classic row mode places KV and intermediate results on the configured main GPU according to pinned llama.cpp help; actual allocation must be verified from startup/trace.",
            "experimental_tensor_kv_sharding": False,
            "weight_allocation_policy": "Inherited --gpu-layers auto --fit on maximizes GPU allocation with reserved VRAM; any actual CPU offload is retained in placement evidence.",
            "precision_context_policy": "Inherited fixed per-request context, F16 K/V and flash-attention on; never silently reduce context or change precision.",
            "architecture_support": "Runtime checked per model; architecture/KV/flash-attention restrictions remain explicit failures. No support or direct P2P transport is assumed.",
            "placement_evidence_limit": "Per-layer weight assignment alone cannot establish row tensor fractions or actual shared-KV locations; inspect retained startup evidence and diagnostic traces.",
        }
        case.pop("case_id", None)
        case["case_id"] = core.digest(case)[:16]
        annotated.append(case)
    return annotated


@contextmanager
def registered_row_configuration():
    """Configuration-only overrides, restored even if a real run is interrupted."""
    original_strategies = dict(core.STRATEGIES)
    original_parser = core.parser
    original_make_cases = core.make_cases
    source_sha256 = wrapper_sha256()

    def parser():
        result = original_parser()
        result.description = __doc__
        for action in result._actions:
            if action.dest == "strategies":
                action.choices = list(ROW_STRATEGIES)
                action.default = ["row_equal"]
        result.set_defaults(output=core.INVESTIGATION / (
            "screening-row-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
        return result

    def make_cases(args):
        if any(strategy not in ROW_STRATEGIES for strategy in args.strategies):
            raise ValueError("This wrapper accepts only classic row strategies.")
        return annotate_row_cases(original_make_cases(args), source_sha256)

    try:
        core.STRATEGIES.update(ROW_STRATEGIES)
        core.parser = parser
        core.make_cases = make_cases
        yield
    finally:
        core.STRATEGIES.clear()
        core.STRATEGIES.update(original_strategies)
        core.parser = original_parser
        core.make_cases = original_make_cases


def main(argv=None):
    with registered_row_configuration():
        return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
