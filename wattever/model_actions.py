"""Model-level edits inside one machine benchmark, with recoverable backups."""
import copy
import datetime
import uuid

from . import endpoints, store
from .consolidation import machine_config


def update_engine(run, cfg, source, *, measurement_scope=None):
    from .runner import merged_config
    groups = run.get("engine_groups") or []
    provider = cfg.get("provider", "lmstudio")
    if not groups and source["config"].get("provider", "lmstudio") == provider:
        return
    if not groups:
        original = source["config"]
        original_provider = original.get("provider", "lmstudio")
        groups = [{"id": "original", "label": original.get("endpoint_label") or
                   ("LM Studio" if original_provider == "lmstudio" else "External endpoint"),
                   "config": copy.deepcopy(original), "provider": copy.deepcopy(source.get("provider", {})),
                   "hardware": copy.deepcopy(source.get("hardware", {})),
                   "measurement_scope": copy.deepcopy(source.get("measurement_scope", {})),
                   "source_run_ids": [source["id"]]}]
        for session in run.get("sessions", []):
            session["engine_group_id"] = "original"
            session.setdefault("source_run_id", source["id"])
    group = next((g for g in groups if g["id"] == cfg.get("engine_group_id")), None)
    if group is None:
        group = {"id": "engine-" + uuid.uuid4().hex[:8],
                 "label": cfg.get("endpoint_label") or ("LM Studio" if provider == "lmstudio" else "External endpoint"),
                 "config": endpoints.public_config(cfg), "provider": endpoints.public_provider(cfg),
                 "hardware": copy.deepcopy(run.get("hardware", {})), "source_run_ids": [run["id"]]}
        groups.append(group)
    else:
        group["config"] = endpoints.public_config(merged_config(group["config"], cfg))
    if measurement_scope is not None:
        group["measurement_scope"] = copy.deepcopy(measurement_scope)
    cfg["engine_group_id"] = group["id"]
    run["execution_config"] = endpoints.public_config(cfg)
    run["engine_groups"] = groups
    run["config"] = machine_config(groups) if len(groups) > 1 else copy.deepcopy(groups[0]["config"])
    run["provider"] = ({"kind": "mixed", "label": "Multiple engines", "model_management": "per_session"}
                       if len(groups) > 1 else copy.deepcopy(groups[0].get("provider", {})))
    if len(groups) > 1:
        run.setdefault("measurement_scope", {}).update(runtime_gpu_placement="per execution session",
            context_control="per execution session", mtp_control="per execution session")
    # Power and transfer evidence belongs to the actual execution sessions.
    run.pop("telemetry", None)


def remove_models(run_id, models):
    run = store.load(run_id)
    if run is None:
        raise ValueError("No such benchmark.")
    if run.get("status") == "running":
        raise ValueError("Wait for the benchmark to stop before removing a model.")
    known = set(run["config"].get("models", [])) | {r.get("model") for r in run["results"]}
    if (not isinstance(models, list) or not models or any(not isinstance(m, str) or m not in known for m in models)):
        raise ValueError("Choose models in this benchmark.")
    removed = set(models)
    if not known - removed:
        raise ValueError("This is the last model. Use Delete benchmark to remove the benchmark.")
    backup = store.backup(run_id)
    count = sum(row.get("model") in removed for row in run["results"])
    run["results"] = [row for row in run["results"] if row.get("model") not in removed]
    run["previous_attempts"] = [row for row in run.get("previous_attempts", []) if row.get("model") not in removed]
    comparison = run.get("storage_comparison")
    if isinstance(comparison, dict):
        for key in ("rows", "baseline_results", "alternate_results"):
            comparison[key] = [row for row in comparison.get(key, []) if row.get("model") not in removed]
        if "model_ids" in comparison:
            comparison["model_ids"] = [m for m in comparison["model_ids"] if m not in removed]
        summary = comparison.get("summary")
        if isinstance(summary, dict) and isinstance(summary.get("models"), list):
            summary["models"] = [item for item in summary["models"] if item.get("model") not in removed]
        if not comparison.get("alternate_results"):
            run.pop("storage_comparison", None)

    def filter_config(cfg):
        if not cfg:
            return
        cfg["models"] = [m for m in cfg.get("models", []) if m not in removed]
        if "model_modes" in cfg:
            cfg["model_modes"] = {m:v for m,v in cfg["model_modes"].items() if m not in removed}
        if "selected_cases" in cfg:
            cfg["selected_cases"] = [c for c in cfg["selected_cases"] if c[0] not in removed]

    filter_config(run["config"])
    filter_config(run.get("execution_config"))
    for session in run.get("sessions", []):
        filter_config(session.get("config"))
    for group in run.get("engine_groups", []):
        filter_config(group["config"])
    groups = [g for g in run.get("engine_groups", []) if g["config"]["models"]]
    if run.get("engine_groups"):
        run["engine_groups"] = groups
        run["sessions"] = [s for s in run.get("sessions", []) if s.get("config", {}).get("models")]
        retained_sessions = {session["id"] for session in run["sessions"]}
        for group in groups:
            for key in ("primary_session_ids", "alternative_session_ids"):
                if key in group:
                    group[key] = [session_id for session_id in group[key] if session_id in retained_sessions]
        run["config"] = machine_config(groups) if len(groups) > 1 else copy.deepcopy(groups[0]["config"])
        if len(groups) == 1:
            run["provider"] = copy.deepcopy(groups[0].get("provider", {}))
    # Rebuild configuration references from the retained measurements.
    run["model_configurations"] = list({r["configuration_id"]:r["model_configuration"]
        for r in run["results"] + run.get("storage_comparison", {}).get("alternate_results", [])
        if r.get("configuration_id") and r.get("model_configuration")}.values())
    run.setdefault("model_edit_history", []).append({"action": "remove_models", "models": sorted(removed),
        "removed_measurements": count, "at": datetime.datetime.now(datetime.timezone.utc).isoformat()})
    from .runner import BenchRunner
    BenchRunner()._finish_session(run)
    store.save(run)
    return {"run": store.summarise(run), "removed_measurements": count,
            "backup_created": bool(backup)}
