"""Consolidate machine results while retaining each engine's execution evidence."""
import copy
import re
from datetime import datetime, timezone

from . import store


def workload(configs):
    cfg = copy.deepcopy(configs[0])
    cfg.pop("selected_cases", None)
    cfg.pop("model_modes", None)
    for key in ("models", "sizes", "concurrency_levels", "mtp_modes"):
        cfg[key] = list(dict.fromkeys(v for c in configs for v in c.get(key, [])))
        if key in ("sizes", "concurrency_levels"):
            cfg[key].sort()
    cfg["repeats"] = max(c.get("repeats", 1) for c in configs)
    metadata = [c.get("reproducibility") or {} for c in configs]
    cfg["reproducibility"] = {k:v for k,v in metadata[0].items()
                              if all(m.get(k) == v for m in metadata)}
    return cfg


def managed_config(run):
    if (run.get("config") or {}).get("provider") != "mixed":
        return run.get("config") or {}
    group = next((g for g in run.get("engine_groups", [])
                  if g.get("config", {}).get("provider") == "lmstudio"), None)
    if not group:
        raise ValueError("This machine benchmark has no managed LM Studio execution group.")
    return group["config"]


def machine_config(groups):
    configs = [g["config"] for g in groups]
    aggregate = workload(configs)
    # Only common settings belong at machine level; engine-specific settings
    # remain in their groups and original execution sessions.
    common = {k:copy.deepcopy(v) for k,v in configs[0].items()
              if all(k in c and c[k] == v for c in configs)}
    for key in ("models", "sizes", "concurrency_levels", "mtp_modes", "repeats", "reproducibility"):
        common[key] = aggregate[key]
    common.pop("model_modes", None)
    common.pop("selected_cases", None)
    common.update(provider="mixed", context_policy="per_session", gpu_offload="per_session", disable_thinking=None)
    return common


def consolidate(run_ids, label):
    if not isinstance(run_ids, list) or not 2 <= len(run_ids) <= 100 or any(
            not isinstance(i, str) for i in run_ids) or len(set(run_ids)) != len(run_ids):
        raise ValueError("Choose 2..100 distinct benchmark IDs, with the destination first.")
    if not isinstance(label, str) or not label.strip() or len(label) > 160:
        raise ValueError("Choose a machine benchmark label of 1..160 characters.")
    runs = [store.load(i) for i in run_ids]
    if any(r is None for r in runs):
        raise ValueError("A selected benchmark does not exist.")
    first = runs[0]
    hardware_class = (first.get("classification") or {}).get("hardware_class")
    if not hardware_class or hardware_class == "remote-unverified":
        raise ValueError("Matching local hardware is required.")
    for run in runs:
        if run.get("status") == "running":
            raise ValueError("Stop selected benchmarks before consolidating them.")
        if (run.get("classification") or {}).get("hardware_class") != hardware_class or any(
                run.get("gpu", {}).get(k) != first.get("gpu", {}).get(k)
                for k in ("name", "vram_total_mb", "driver")):
            raise ValueError("Hardware or GPU driver differs.")
        if run.get("corpus", {}).get("sha256") != first.get("corpus", {}).get("sha256") or (
                run.get("measurement_mode", "helper") != first.get("measurement_mode", "helper")) or (
                run.get("application", {}).get("protocol_version") != first.get("application", {}).get("protocol_version")):
            raise ValueError("Corpus or measurement protocol differs.")
        if run.get("config", {}).get("provider") == "openai" and not run["config"].get("endpoint_inference_local"):
            raise ValueError("Confirm local inference before consolidating an endpoint run.")

    result = copy.deepcopy(first)
    groups = copy.deepcopy(result.get("engine_groups") or [])
    rows, sessions, sources, attempts = [], [], [], []
    identities, session_ids, cases = {}, set(), set()
    for original in runs:
        run = copy.deepcopy(original)
        cfg = run["config"]
        if cfg.get("provider") == "mixed":
            if run["id"] != first["id"]:
                raise ValueError("An existing consolidated benchmark must be the destination.")
            current_groups = {g["id"] for g in groups}
        else:
            engine = cfg.get("reproducibility", {}).get("engine") or (
                "LM Studio" if cfg.get("provider", "lmstudio") == "lmstudio" else run.get("provider", {}).get("label") or "Endpoint")
            group = next((g for g in groups if g["label"] == engine and
                          g["config"].get("provider", "lmstudio") == cfg.get("provider", "lmstudio")), None)
            if group is None:
                gid = re.sub(r"[^a-z0-9]+", "-", engine.lower()).strip("-") or "engine"
                if any(g["id"] == gid for g in groups):
                    gid += "-" + str(len(groups) + 1)
                group = {"id":gid, "label":engine, "config":copy.deepcopy(cfg),
                         "provider":copy.deepcopy(run.get("provider") or {}),
                         "measurement_scope":copy.deepcopy(run.get("measurement_scope") or {}),
                         "hardware":copy.deepcopy(run.get("hardware") or {}),
                         "source_run_ids":[], "idle":copy.deepcopy(run.get("idle") or {})}
                groups.append(group)
            else:
                group["config"] = workload([group["config"], cfg])
            group["source_run_ids"].append(run["id"])
            current_groups = {group["id"]}
        run_sessions = run.get("sessions") or []
        if not run_sessions or any(not r.get("session_id") for r in run.get("results", [])):
            raise ValueError("Each source measurement must have an execution session.")
        for session in run_sessions:
            if session["id"] in session_ids:
                raise ValueError("Execution session IDs overlap; source runs cannot be safely consolidated.")
            session_ids.add(session["id"])
            if cfg.get("provider") != "mixed":
                session["engine_group_id"] = group["id"]
            if session.get("engine_group_id") not in current_groups:
                raise ValueError("Execution session has no valid engine group.")
            session.setdefault("source_run_id", run["id"])
            if run.get("runtime_observations"):
                session.setdefault("runtime_observations", copy.deepcopy(run["runtime_observations"]))
            sessions.append(session)
        known_sessions = {s["id"] for s in run_sessions}
        for row in run.get("results", []):
            if row["session_id"] not in known_sessions:
                raise ValueError("A measurement references a missing execution session.")
            key = (row.get("configuration_id"), row.get("model"), row.get("mtp"),
                   row.get("target_tokens"), row.get("concurrency", 1), row.get("repeat", 0))
            if key in cases:
                raise ValueError("Measurement cases overlap; repair or deduplicate them before consolidation.")
            cases.add(key)
            rows.append(row)
        attempts.extend(run.get("previous_attempts") or [])
        for configuration in run.get("model_configurations") or []:
            identities[configuration["configuration_id"]] = configuration
        if cfg.get("provider") == "mixed":
            sources.extend(run.get("source_runs") or [])
        else:
            sources.append({k:v for k,v in run.items() if k not in (
                "results", "sessions", "model_configurations", "previous_attempts", "comparison_group", "source_runs")})

    result.update(label=label.strip(), results=rows, sessions=sessions, engine_groups=groups,
                  source_runs=sources, model_configurations=list(identities.values()))
    if attempts:
        result["previous_attempts"] = attempts
    result["config"] = machine_config(groups)
    result["provider"] = {"kind":"mixed", "label":" + ".join(g["label"] for g in groups),
                          "model_management":"per_session"}
    result.setdefault("measurement_scope", {}).update(runtime_gpu_placement="per execution session",
                                        context_control="per execution session", mtp_control="per execution session")
    result.pop("comparison_group", None)
    result.pop("telemetry", None)  # Per-engine timelines remain in source_runs.
    finished = [datetime.fromisoformat(r["finished_at"].replace("Z", "+00:00")) for r in runs if r.get("finished_at")]
    if finished:
        result["finished_at"] = max(finished).isoformat()
    result["remaining_tests"] = sum(r.get("remaining_tests", 0) for r in runs)
    result["status"] = "completed" if not result["remaining_tests"] else "interrupted"
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    result.setdefault("consolidation_history", []).append({"at":result["updated_at"],
        "source_run_ids":run_ids, "source_case_counts":{r["id"]:len(r.get("results", [])) for r in runs},
        "measurements_changed":False})
    from .validation import validate_run
    errors = validate_run(result)
    if errors:
        raise ValueError("Consolidated result is invalid: " + "; ".join(errors))
    for run in runs:
        store.backup(run["id"])
    store.save(result)
    for run in runs[1:]:
        store.delete(run["id"])
    return result
