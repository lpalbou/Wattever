"""Configure a separate q8_0 KV experiment around the frozen screening core.

Plan only by default. The default 16 conditions cover LFM2.5/Qwen27, 20k/80k,
one/two clients and single5070/independent execution. Root/operator decides which
conditions to run after the base and classic-row experiments. Only requested
precision metadata and cache command flags change; measurement methods, fixed
contexts, gates, actual placement evidence, energy and cleanup remain inherited.

Example first-condition review:
  <flash-venv-python> untracked/run_kv_screen.py --run --stop-after 1 \
      --output untracked/communication-investigation/screening-kv-q8
Continue the identical full plan with --resume after inspecting actual KV logs.
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

MODEL_KEYS = ("lfm25", "qwen27")
STRATEGY_KEYS = ("single5070", "independent")
KV_TYPE = "q8_0"


def wrapper_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def annotate_kv_cases(cases, source_sha256):
    annotated = []
    for original in cases:
        case = dict(original)
        case.update(kv_type_k=KV_TYPE, kv_type_v=KV_TYPE,
            configuration_wrapper="untracked/run_kv_screen.py",
            configuration_wrapper_sha256=source_sha256,
            kv_precision_treatment={
                "requested_type_k": KV_TYPE,
                "requested_type_v": KV_TYPE,
                "baseline_requested_type_k": "f16",
                "baseline_requested_type_v": "f16",
                "actual_precision_verified": None,
                "actual_precision_verification": "Requested flags are not effective-precision proof. Verify effective K/V types in retained cache startup/buffer logs or explicit runtime cache metadata, including every applicable attention cache. GGUF weight quantization/header alone does not prove KV precision. Ignored, unsupported or inapplicable requests are not verified q8_0 conditions.",
                "fallback_policy": "No precision or context fallback. Unsupported requests remain failures; inherited automatic GPU layer fitting may change actual CPU offload, which stays explicit in placement evidence.",
                "comparison_policy": "Separate precision condition from F16; preserve the same weights, per-request context, sampling, reasoning/MTP and canonical prompt recipe.",
                "canonical_prompt_recipe": "Unchanged core key: model, target, client, seed and corpus SHA256; actual prompt hashes and API usage remain authoritative.",
                "frozen_core_plan_note": "The inherited top-level F16 comparability sentence describes the frozen core baseline. This wrapper's explicit per-condition q8_0 request fields define this separate treatment; effective precision remains unverified until runtime evidence is checked.",
            })
        case.pop("case_id", None)
        case["case_id"] = core.digest(case)[:16]
        annotated.append(case)
    return annotated


def configure_cache_command(command):
    configured = list(command)
    for flag in ("--cache-type-k", "--cache-type-v"):
        if configured.count(flag) != 1:
            raise ValueError("Frozen command must contain exactly one " + flag)
        index = configured.index(flag) + 1
        if index >= len(configured) or configured[index] != "f16":
            raise ValueError("Frozen F16 cache command contract changed: " + flag)
        configured[index] = KV_TYPE
    return configured


@contextmanager
def registered_kv_configuration():
    original_parser = core.parser
    original_make_cases = core.make_cases
    original_server_command = core.server_command
    source_sha256 = wrapper_sha256()

    def parser():
        result = original_parser()
        result.description = __doc__
        for action in result._actions:
            if action.dest == "models":
                action.choices = list(MODEL_KEYS)
                action.default = list(MODEL_KEYS)
            elif action.dest == "strategies":
                action.choices = list(STRATEGY_KEYS)
                action.default = list(STRATEGY_KEYS)
            elif action.dest == "sizes":
                action.default = [20000, 80000]
        result.set_defaults(output=core.INVESTIGATION / (
            "screening-kv-q8-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")))
        return result

    def make_cases(args):
        if any(model not in MODEL_KEYS for model in args.models):
            raise ValueError("This focused KV configurator accepts only LFM2.5 and Qwen27.")
        if any(strategy not in STRATEGY_KEYS for strategy in args.strategies):
            raise ValueError("This focused KV configurator accepts only single5070 and independent.")
        return annotate_kv_cases(original_make_cases(args), source_sha256)

    def server_command(*args, **kwargs):
        return configure_cache_command(original_server_command(*args, **kwargs))

    try:
        core.parser = parser
        core.make_cases = make_cases
        core.server_command = server_command
        yield
    finally:
        core.parser = original_parser
        core.make_cases = original_make_cases
        core.server_command = original_server_command


def main(argv=None):
    with registered_kv_configuration():
        return core.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
