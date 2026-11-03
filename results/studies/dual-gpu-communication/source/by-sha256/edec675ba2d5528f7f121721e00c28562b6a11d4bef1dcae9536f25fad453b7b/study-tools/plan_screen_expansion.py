"""Offline selection of at most 48 useful F16 follow-up conditions.

Reads explicitly supplied completed screening exports; never discovers servers.
Default prints a plan. --run launches sequential owned Python configurators;
the frozen core retains all lifecycle/idle/cohort/source gates and measurements.
q8_0 experiments are a separate precision protocol and are not pooled here.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import standalone_screen as core

STRATEGIES = {"layer_equal", "layer_favor5070", "gemma_kv_local", "tensor_equal",
              "row_equal", "row_favor5070", "independent"}
PREFERRED_TIES = {"gemmae2b": "gemma_kv_local", "lfm25": "layer_favor5070"}
SIZES = [5000, 10000, 40000]


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def usable(result):
    requests = result.get("requests", [])
    return (result.get("status") == "ok" and result.get("measured_repeats") == 1 and
        len(requests) == result["condition"]["clients"] and all(
            r.get("status") == "ok" and type(r.get("prompt_tokens")) is int and
            r["prompt_tokens"] > 0 and type(r.get("completion_tokens")) is int and
            r["completion_tokens"] > 0 and r.get("completion_tokens_estimated") is False
            for r in requests))


def protocol_signature(result, runtime):
    c = result["condition"]
    file = runtime.get("model_files", {}).get(c["model"], {})
    if not file.get("header_sha256") or not file.get("file_bytes"):
        raise ValueError("Screened model lacks GGUF header/size identity: " + c["model"])
    required = {"reasoning": "none", "mtp": "none", "temperature": 0.0, "top_p": 1.0,
                "kv_layout": "unified", "cache_prompt": False, "conversation_cache_ram_mib": 0,
                "context_shift": False, "flash_attention": "on"}
    if any(c.get(key) != value for key, value in required.items()):
        raise ValueError("Source protocol cannot be reproduced by the frozen configurator: " + c["model"])
    fields = ("kv_type_k", "kv_type_v", "reasoning", "mtp", "temperature", "top_p", "seed",
              "max_tokens", "batch_size", "ubatch_size", "cpu_threads", "cpu_threads_batch",
              "kv_layout", "cache_prompt", "conversation_cache_ram_mib", "context_shift")
    return core.digest({"condition": {key:c.get(key) for key in fields},
        "model": result.get("model"), "gguf": file, "executable": runtime.get("executable_sha256"),
        "source_sha256": runtime.get("source_sha256"), "host": runtime.get("host_hardware"),
        "gpus": runtime.get("gpu_mapping"), "cuda_overrides": runtime.get("cuda_overrides")})


def load_sources(paths):
    rows, sources, signatures, seen = [], [], {}, {}
    for path in paths:
        path = Path(path)
        raw = path.read_bytes()
        document = json.loads(raw)
        if document.get("capture_kind") != "screening":
            raise ValueError("Only unprofiled screening exports are eligible: " + str(path))
        runtime = document.get("runtime", {})
        conditions = document.get("conditions", [])
        manifest = path.parent / "manifest-private.json"
        planned = None
        if manifest.is_file():
            planned = json.loads(manifest.read_text(encoding="utf-8"))["plan"]["cases"]
        recorded_ids = {r["condition"]["case_id"] for r in conditions}
        complete = planned is not None and recorded_ids == {c["case_id"] for c in planned} and all(
            r.get("status") not in (None, "starting", "running") for r in conditions)
        sources.append({"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                        "recorded": len(conditions), "planned": len(planned) if planned else None,
                        "complete": complete, "source_sha256": runtime.get("source_sha256"),
                        "executable_sha256": runtime.get("executable_sha256")})
        for result in conditions:
            c = result["condition"]
            if c.get("capture_kind") != "screening" or c.get("kv_type_k") != "f16" or c.get("kv_type_v") != "f16":
                continue
            if c.get("output_stop_policy") not in (None, "natural_eos"):
                raise ValueError("Strategy selection inputs must use natural-EOS screens; fixed-output observations stay separate.")
            identity = c["case_id"]
            signature = protocol_signature(result, runtime)
            observation = core.digest({"result": result, "protocol": signature})
            if identity in seen:
                if seen[identity] != observation:
                    raise ValueError("Same condition ID has different observations/runtime; choose one coherent cohort.")
                continue  # Exact condition is not another inference repeat.
            seen[identity] = observation
            if c["model"] in signatures and signatures[c["model"]] != signature:
                raise ValueError("Incompatible weights/protocol/runtime for " + c["model"] + "; keep separate plans.")
            signatures[c["model"]] = signature
            rows.append(result)
    return rows, sources, signatures


def candidate_score(anchor, candidate, strategy, model):
    covered, advantage, terms = 0, 0, []
    evidence = []
    for cell in ((20000, 1), (20000, 2), (80000, 1), (80000, 2)):
        a, b = anchor.get(cell), candidate.get(cell)
        a_ok, b_ok = bool(a and usable(a)), bool(b and usable(b))
        covered += int(b_ok)
        advantage += int(b_ok and not a_ok)
        cell_evidence = {"prompt_target": cell[0], "clients": cell[1],
                         "anchor_usable": a_ok, "candidate_usable": b_ok}
        if a_ok and b_ok:
            if a["condition"]["context_per_slot"] != b["condition"]["context_per_slot"]:
                raise ValueError("Per-request context differs in a matched selection cell.")
            metrics = (("decode_tps", .5), ("prefill_tps", .25)) if cell[1] == 1 else (("aggregate_output_tps", .25),)
            for metric, weight in metrics:
                av = a["requests"][0].get(metric) if cell[1] == 1 else a.get("group", {}).get(metric)
                bv = b["requests"][0].get(metric) if cell[1] == 1 else b.get("group", {}).get(metric)
                if positive(av) and positive(bv):
                    ratio = bv / av
                    terms.append((weight, math.log(ratio)))
                    cell_evidence[metric + "_ratio"] = ratio
        evidence.append(cell_evidence)
    ratio = math.exp(sum(w * x for w, x in terms) / sum(w for w, x in terms)) if terms else None
    return {"strategy": strategy, "usable_long_context_cells": covered,
        "usable_coverage_advantage_cells": advantage, "weighted_performance_ratio": ratio,
        "matched_metric_terms": len(terms),
        "tie_preference_only": strategy == PREFERRED_TIES.get(model), "evidence": evidence}


def build_plan(rows, sources, signatures, models, python, output, exe=core.DEFAULT_EXE, overrides=None, fixed_output=False):
    by_model = {}
    for result in rows:
        c = result["condition"]
        cells = by_model.setdefault(c["model"], {}).setdefault(c["strategy"], {})
        cell = (c["target_tokens"], c["clients"])
        if cell in cells:
            raise ValueError("Multiple separate captures for the same model/strategy/cell; select one coherent source cohort.")
        cells[cell] = result
    segments, decisions = [], []
    for model in models:
        strategies = by_model.get(model, {})
        anchor = strategies.get("single5070", {})
        if not any(usable(r) for r in anchor.values()):
            decisions.append({"model": model, "status": "no_usable_single5070_anchor"})
            continue
        candidates = [candidate_score(anchor, cells, strategy, model) for strategy, cells in strategies.items()
                      if strategy in STRATEGIES]
        candidates = [c for c in candidates if c["usable_long_context_cells"]]
        candidates.sort(key=lambda c:(c["usable_long_context_cells"],
            c["weighted_performance_ratio"] or 0, c["tie_preference_only"], c["strategy"]), reverse=True)
        chosen = candidates[0]["strategy"] if candidates else None
        override = (overrides or {}).get(model)
        if override:
            if override not in {c["strategy"] for c in candidates}:
                raise ValueError("Requested comparator has no usable matched-precision long-context screen: " + model + "=" + override)
            chosen = override
        decisions.append({"model": model, "protocol_signature": signatures.get(model),
            "anchor": "single5070", "selected_candidate": chosen, "ranked_candidates": candidates,
            "candidate_selection": "Explicit operator comparator with screened evidence; not a fastest-strategy claim." if override else "Measured coverage/performance ranking.",
            "selection_rule": "Highest usable20k/80k coverage, then weighted matched performance; preference hints only break ties. Failures remain censored observations, without invented rates or a presumed memory cause."})
        for strategy in ["single5070"] + ([chosen] if chosen else []):
            script = ("untracked/run_fixed_output_screen.py" if fixed_output else
                "untracked/run_row_screen.py" if strategy.startswith("row_") else "tools/standalone_screen.py")
            destination = output / (model + "-" + strategy)
            command = [str(python), script, "--run", "--models", model, "--strategies", strategy,
                "--sizes", *map(str, SIZES), "--clients", "1", "2", "--output", str(destination),
                "--exe", str(exe)]
            reference = next(iter(anchor.values()))["condition"]
            if fixed_output and reference.get("max_tokens") != 256:
                raise ValueError("Fixed256 expansion requires screened max_tokens256 to keep context allocation matched.")
            for flag, field in (("--seed", "seed"), ("--max-tokens", "max_tokens"),
                                ("--batch-size", "batch_size"), ("--ubatch-size", "ubatch_size"),
                                ("--threads", "cpu_threads"), ("--threads-batch", "cpu_threads_batch")):
                if type(reference.get(field)) is not int:
                    raise ValueError("Missing integer protocol setting: " + field)
                command += [flag, str(reference[field])]
            segments.append({"model": model, "strategy": strategy, "condition_count": 6,
                "sizes": SIZES, "clients": [1, 2], "kv_type_k": "f16", "kv_type_v": "f16",
                "output_stop_policy": "ignore_eos_requested_256" if fixed_output else "natural_eos",
                "command": command, "output": str(destination)})
    count = sum(s["condition_count"] for s in segments)
    if count > 48:
        raise ValueError("Expansion exceeded the48-condition cap.")
    return {"schema": "screen-expansion-selection-v1", "capture_kind": "screening", "condition_cap": 48,
        "selector_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "condition_count": count, "measured_repeats": 1, "sources": sources,
        "output_stop_policy": "ignore_eos_requested_256" if fixed_output else "natural_eos",
        "output_stop_comparison": "Screened natural-EOS scores select strategies only. Fixed-output observations are a separate sampling/stop-policy condition and must not be pooled." if fixed_output else "Natural-EOS protocol retained.",
        "provisional": not sources or not all(s["complete"] for s in sources),
        "precision_protocol": "F16 only; q8_0 remains a separate experiment and is never pooled.",
        "selection_caveat": "A one-repeat screen selects follow-ups; it does not prove statistical significance or global optimality.",
        "performance_score_weights": {"single_request_decode": .5, "single_request_client_prefill": .25,
            "two_client_aggregate_output": .25, "method": "Geometric ratios over matched usable20k/80k cells; absent metrics stay absent."},
        "decisions": decisions, "segments": segments}


def verify_fixed_capture(result_path):
    """Read artifacts only; preserve the measured result and keep proof separate."""
    raw = result_path.read_bytes()
    result = json.loads(raw)
    case = result["condition"]
    requests = result.get("requests", [])
    count_ok = (len(requests) == case["clients"] and all(r.get("status") == "ok" and
        type(r.get("completion_tokens")) is int and r["completion_tokens"] == 256 and
        r.get("completion_tokens_estimated") is False for r in requests))
    servers = []
    for path in sorted(result_path.parent.glob("server*/actual-server-private.json")):
        metadata_raw = path.read_bytes()
        metadata = json.loads(metadata_raw)
        settings = metadata.get("props", {}).get("default_generation_settings", {})
        params = settings.get("params", {})
        value = params.get("ignore_eos")
        servers.append({"source": path.parent.name + "/" + path.name,
            "sha256": hashlib.sha256(metadata_raw).hexdigest(),
            "props_ignore_eos": value if type(value) is bool else None,
            "slot_ignore_eos": [s.get("params", {}).get("ignore_eos") for s in metadata.get("slots", [])]})
    expected = 2 if case["strategy"] == "independent" else 1
    props_ok = len(servers) == expected and all(s["props_ignore_eos"] is True for s in servers)
    eligible = (result.get("status") == "ok" and case.get("output_stop_policy") == "ignore_eos_requested_256"
                and count_ok and props_ok)
    proof = {"schema": "fixed-output-verification-v1", "result_sha256": hashlib.sha256(raw).hexdigest(),
        "actual_output_count_verified": count_ok, "actual_server_ignore_eos_verified": props_ok,
        "expected_output_tokens_per_request": 256, "server_evidence": servers,
        "eligible_fixed_output_comparison": eligible,
        "measurement_file_unchanged": True}
    sidecar = result_path.parent / "fixed-output-verification.json"
    if sidecar.exists():
        if json.loads(sidecar.read_text(encoding="utf-8")) != proof:
            raise ValueError("Fixed-output verification evidence changed; preserve the original and investigate.")
    else:
        core.write_json(sidecar, proof)
    if not eligible:
        raise ValueError("Actual256 tokens/server ignore_eos were not both verified; stop before more fixed-output conditions.")
    return proof


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--screen", type=Path, nargs="+", required=True)
    parser.add_argument("--models", choices=core.MODELS, nargs="+", default=list(core.MODELS))
    parser.add_argument("--candidate", action="append", default=[], metavar="MODEL=STRATEGY",
        help="Choose a scientific comparator explicitly; it must have usable F16 screen evidence.")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--exe", type=Path, default=core.DEFAULT_EXE)
    parser.add_argument("--output", type=Path, default=core.INVESTIGATION / "screening-expansion")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--fixed-output", action="store_true", help="Separate --ignore-eos256 condition; screened scores select strategies only.")
    parser.add_argument("--max-new-conditions", type=int)
    args = parser.parse_args(argv)
    if args.fixed_output and args.output == core.INVESTIGATION / "screening-expansion":
        args.output = core.INVESTIGATION / "screening-expansion-fixed256"
    if args.max_new_conditions is not None and args.max_new_conditions < 1:
        raise SystemExit("--max-new-conditions must be positive.")
    rows, sources, signatures = load_sources(args.screen)
    overrides = {}
    for value in args.candidate:
        parts = value.split("=", 1)
        if len(parts) != 2 or parts[0] not in args.models or parts[1] not in STRATEGIES:
            raise SystemExit("--candidate must be a selected MODEL and screened STRATEGY.")
        overrides[parts[0]] = parts[1]
    plan = build_plan(rows, sources, signatures, args.models, args.python, args.output, args.exe, overrides, args.fixed_output)
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    if plan["provisional"]:
        raise SystemExit("Screening inputs are incomplete/unverifiable; finish baseline+row before expansion.")
    current_sources = core.source_fingerprints()
    if any(s["source_sha256"] != current_sources for s in sources):
        raise SystemExit("Frozen measurement sources changed; expansion must remain a separate protocol.")
    executable_hash = hashlib.sha256(args.exe.read_bytes()).hexdigest()
    if any(s["executable_sha256"] != executable_hash for s in sources):
        raise SystemExit("Selected executable differs from screened runtime; keep a separate protocol.")
    core.validate_prior_completion()
    manifest = args.output / "selection-plan.json"
    if manifest.exists():
        if not args.resume or json.loads(manifest.read_text(encoding="utf-8")) != plan:
            raise SystemExit("Existing expansion selection differs or --resume was not supplied.")
    else:
        core.write_json(manifest, plan)
    remaining = args.max_new_conditions
    for segment in plan["segments"]:
        destination = Path(segment["output"])
        def recorded_paths():
            return set((destination / "cases").glob("*/result-private.json")) if destination.exists() else set()
        def recorded():
            return len(recorded_paths())
        if destination.exists() and not args.resume:
            raise SystemExit("Segment output exists; explicitly --resume.")
        if args.fixed_output:
            for result_path in sorted(recorded_paths()):
                verify_fixed_capture(result_path)
        while recorded() < segment["condition_count"]:
            if remaining is not None and remaining <= 0:
                return 0
            before_paths = recorded_paths()
            command = list(segment["command"]) + ["--stop-after", "1"]
            if destination.exists():
                command.append("--resume")
            # One NEW condition per child keeps the full six-case child plan
            # resumable and bounds cancellation to the current condition. Never
            # kill the Python owner and orphan its model process on Ctrl+C.
            interrupted = False
            child = subprocess.Popen(command, cwd=ROOT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            while True:
                try:
                    returncode = child.wait(timeout=1)
                    break
                except subprocess.TimeoutExpired:
                    continue
                except KeyboardInterrupt:
                    interrupted = True
                    print("Stop requested; waiting for the current owned condition and verified cleanup.", file=sys.stderr)
            if returncode:
                raise SystemExit("Owned configurator failed; stop expansion and inspect retained segment output.")
            new_paths = recorded_paths() - before_paths
            new = len(new_paths)
            if new != 1:
                raise SystemExit("Expected exactly one new terminal condition; inspect the segment before continuing.")
            if args.fixed_output:
                verify_fixed_capture(next(iter(new_paths)))
            if remaining is not None:
                remaining -= new
            if interrupted:
                return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
