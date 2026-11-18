"""Merge benchmarks of one machine while retaining each engine's execution evidence.

Three questions are answered here, in this order:

* which saved benchmarks ran on the same computer (`facts`, `suggestions`);
* what stands between a selection and one benchmark, in plain words (`merge_plan`);
* the merge itself, and how to take it back (`consolidate`, `undo_merge`).

Decisions use `facts`: a small description of a benchmark that is cached per
file, so planning never re-reads multi-megabyte result files.
"""
import copy
import json
import math
import os
import re
import shutil
import threading
from collections import Counter
from datetime import datetime, timezone

from . import store

REMOTE = "remote-unverified"


def _known_text(value):
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    return normalized if normalized not in ("", "unknown", "unreported", "unspecified", "notreported", "na", "none", "null") else None


def _board_multiset(identity):
    """Operator board identity only; sensor order and provenance prose are not identity."""
    if isinstance(identity, dict):
        cards = identity.get("gpus")
        if cards is None and "manufacturer" in identity:
            cards = [identity]
    else:
        cards = identity
    if not isinstance(cards, list) or not cards:
        return None
    normalized = []
    aliases = {"asustekcomputerinc": "asus", "asustekcomputer": "asus",
               "pnytechnologiesinc": "pny", "pnytechnologies": "pny"}
    for card in cards:
        if not isinstance(card, dict):
            return None
        manufacturer, model = _known_text(card.get("manufacturer")), _known_text(card.get("model"))
        memory = card.get("memory_gib_nominal")
        if not manufacturer or not model or type(memory) not in (int, float):
            return None  # Partial/legacy annotations do not establish a full multiset.
        try:
            memory = float(memory)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(memory) or memory <= 0:
            return None
        normalized.append((aliases.get(manufacturer, manufacturer), model, memory))
    return tuple(sorted(normalized))  # Preserve duplicate cards without using sensor indices.


def _board_evidence(run):
    identities, variants = set(), set()
    pending, seen = [run], set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict) or id(node) in seen:
            continue
        seen.add(id(node))
        for identity in (node.get("gpu_board_identity"), (node.get("hardware") or {}).get("gpu_board_identity")):
            signature = _board_multiset(identity)
            if signature is not None:
                identities.add(signature)
        variant = _known_text((node.get("classification") or {}).get("board_variant"))
        if variant:
            variants.add(variant)
        for key in ("sessions", "source_runs"):
            pending.extend(node.get(key) or [])
    if len(identities) > 1:
        raise ValueError("Known GPU board identities conflict within a selected benchmark.")
    if not identities and len(variants) > 1:
        raise ValueError("Known GPU board variants conflict within a selected benchmark.")
    return next(iter(identities), None), variants


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


# ---------------------------------------------------------------------------
# Which machine, which engine


def _unattributed(run):
    """An endpoint benchmark recorded without stating where inference ran."""
    cfg = run.get("config") or {}
    return ((cfg.get("provider") == "openai" and not cfg.get("endpoint_inference_local"))
            or (run.get("classification") or {}).get("hardware_class") == REMOTE)


def _client(run):
    """The computer that sent the requests of an unattributed endpoint benchmark."""
    client = run.get("measurement_client")
    hardware = client.get("hardware") if isinstance(client, dict) else None
    if not isinstance(hardware, dict) or not isinstance(hardware.get("gpus"), list) or not isinstance(hardware.get("cpu"), dict):
        return None
    return client


def engine_label(run):
    """Name the engine, preferring what was reported or detected over a route label."""
    cfg = run.get("config") or {}
    reported = (cfg.get("reproducibility") or {}).get("engine")
    if reported:
        return reported
    if cfg.get("provider", "lmstudio") == "lmstudio":
        return "LM Studio"
    from .endpoints import safe_identity_metadata
    service = safe_identity_metadata(cfg.get("endpoint_identity")).get("service")
    if service not in (None, "", "unknown"):
        return service
    return (run.get("provider") or {}).get("label") or "Endpoint"


def _mixed(run):
    return (run.get("config") or {}).get("provider") == "mixed"


def _machine_name(hardware, host, gpu):
    cards = [re.sub(r"^NVIDIA (?:GeForce )?", "", str(card.get("name"))) for card in hardware.get("gpus") or []
             if isinstance(card, dict) and card.get("name")]
    if not cards and gpu.get("name"):
        cards = [re.sub(r"^NVIDIA (?:GeForce )?", "", str(gpu["name"]))]
    parts = [" + ".join("%d× %s" % (count, name) if count > 1 else name for name, count in Counter(cards).items())]
    cpu = (hardware.get("cpu") or {}).get("name") or host.get("cpu") or ""
    cpu = re.sub(r"\s+", " ", re.sub(r"\((?:R|TM)\)|\bCPU\b|@.*$", "", str(cpu))).strip()
    if cpu and cpu not in cards:
        parts.append(cpu)
    memory = (hardware.get("system") or {}).get("memory_bytes")
    if type(memory) in (int, float) and memory > 0:
        parts.append("%d GB RAM" % round(memory / 1024 ** 3))
    return " · ".join(part for part in parts if part) or "unknown machine"


def _when(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _case(row):
    return (row.get("configuration_id"), row.get("model"), row.get("mtp"),
            row.get("target_tokens"), row.get("concurrency", 1), row.get("repeat", 0))


def _better(candidate, current):
    """A completed measurement beats an incomplete one; otherwise the most recent wins."""
    return ((candidate.get("status") == "ok", _when(candidate.get("timestamp")))
            > (current.get("status") == "ok", _when(current.get("timestamp"))))


def facts(run):
    """Everything a merge decision needs to know about one benchmark."""
    cfg = run.get("config") or {}
    hardware, host, gpu = run.get("hardware") or {}, run.get("host") or {}, run.get("gpu") or {}
    stored = (run.get("classification") or {}).get("hardware_class")
    attribution = "local"
    if _unattributed(run):
        client = _client(run)
        attribution = "client" if client else "unknown"
        if client:
            hardware, host, gpu = client["hardware"], client.get("host") or {}, client.get("gpu") or {}
            stored = store.hardware_class(hardware)
        else:
            stored = None
    elif cfg.get("provider") == "openai":
        attribution = "declared"
    try:
        boards, boards_error = _board_evidence(dict(run, hardware=hardware)), None
    except ValueError as exc:
        boards, boards_error = None, str(exc)
    rows, sessions = run.get("results") or [], run.get("sessions") or []
    session_ids = [session.get("id") for session in sessions if isinstance(session, dict)]
    engines = ([group.get("label") or "Engine" for group in run.get("engine_groups") or []] if _mixed(run)
               else [engine_label(run)])
    return {
        "id": run.get("id"), "label": run.get("label") or run.get("id"), "created_at": run.get("created_at") or "",
        "status": run.get("status"), "tests": len(rows), "models": sorted({row.get("model") for row in rows if row.get("model")}),
        "engines": engines, "provider": cfg.get("provider", "lmstudio"), "mixed": _mixed(run),
        "attribution": attribution,
        "loopback": (run.get("provider") or {}).get("inference_location") == "loopback",
        "machine": stored if stored and stored != REMOTE else None,
        "machine_name": _machine_name(hardware, host, gpu),
        "gpu": [gpu.get(key) for key in ("name", "vram_total_mb", "driver")],
        "corpus": (run.get("corpus") or {}).get("sha256"),
        "mode": run.get("measurement_mode", "helper"),
        "protocol": (run.get("application") or {}).get("protocol_version"),
        "boards": boards, "boards_error": boards_error,
        "session_ids": session_ids,
        "sessions_complete": bool(sessions) and all(row.get("session_id") in set(session_ids) for row in rows),
        "cases": [(_case(row), row.get("status") == "ok", row.get("timestamp")) for row in rows],
        "settings": {key: cfg.get(key) for key in ("max_tokens", "disable_thinking", "gpu_offload")},
    }


_FACTS = {}
_FACTS_LOCK = threading.Lock()


def library_facts():
    """Facts for every saved benchmark; a file is re-read only after it changes."""
    store.ensure_dirs()
    found, seen = [], set()
    for name in sorted(os.listdir(store.RUNS_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(store.RUNS_DIR, name)
        try:
            status = os.stat(path)
        except OSError:
            continue
        stamp = (status.st_mtime_ns, status.st_size)
        seen.add(path)
        with _FACTS_LOCK:
            cached = _FACTS.get(path)
        if cached and cached[0] == stamp:
            found.append(cached[1])
            continue
        try:
            run = store.load(name[:-5])
        except ValueError:
            continue
        if not run:
            continue
        item = facts(run)
        with _FACTS_LOCK:
            _FACTS[path] = (stamp, item)
        found.append(item)
    with _FACTS_LOCK:
        for path in [path for path in _FACTS if os.path.dirname(path) == store.RUNS_DIR and path not in seen]:
            del _FACTS[path]
    return found


def localized(run, declared_at):
    """Attribute an endpoint benchmark to the computer that launched it.

    This records an operator statement made after the run: the endpoint was
    served by the same machine as the benchmark client. Power was not sampled
    for such a run and remains unavailable; nothing is inferred or measured.
    """
    client = _client(run)
    if not client:
        raise ValueError("This endpoint benchmark has no record of the computer that ran it.")
    run = copy.deepcopy(run)
    hardware, host = copy.deepcopy(client["hardware"]), copy.deepcopy(client.get("host") or {})
    assertion = {"source": "user_reported", "statement": "inference ran on the benchmark client",
                 "declared_at": declared_at, "declared_during": "merge", "power_sampled": False}
    scope = {"hardware_attribution": "user_reported after the run: inference ran on the benchmark client; power was not sampled",
             "inference_local_source": "user_reported",
             "power_boundary": "not sampled; local inference was declared after the run"}
    run.update(hardware=hardware, host=host, gpu=copy.deepcopy(client.get("gpu") or {}),
               classification=store.classification(hardware, host.get("platform")),
               inference_local_assertion=assertion)
    run.setdefault("config", {})["endpoint_inference_local"] = True
    run.setdefault("measurement_scope", {}).update(scope)
    for session in run.get("sessions") or []:
        # Session settings stay as executed; only the hardware attribution is restated.
        if ((session.get("hardware") or {}).get("os") or {}).get("name") == "unknown remote host":
            session["hardware"] = copy.deepcopy(hardware)
        if isinstance(session.get("measurement_scope"), dict):
            session["measurement_scope"].update(scope)
        session["inference_local_assertion"] = assertion
    run.pop("measurement_client", None)
    return run


# ---------------------------------------------------------------------------
# What stands in the way


def _quoted(item):
    return "“%s”" % item["label"]


def _ordered(items):
    """Destination first: an already merged benchmark, else the largest locally measured one."""
    if not items:
        return []
    first = min(items, key=lambda item: (not item["mixed"], item["attribution"] in ("client", "unknown"),
                                         -item["tests"], item["created_at"], item["id"]))
    return [first] + sorted((item for item in items if item is not first), key=lambda item: (item["created_at"], item["id"]))


def _inspect(items, assume_local=()):
    """Issues for an ordered selection: `block` cannot be merged, `confirm` needs the
    operator's word, `warn` is worth knowing. Each names the benchmarks it concerns."""
    issues = []

    def add(level, code, message, *concerned):
        issues.append({"level": level, "code": code, "message": message, "run_ids": [item["id"] for item in concerned]})

    for item in items:
        if item["boards_error"]:
            add("block", "boards", item["boards_error"], item)
    for index, item in enumerate(items):
        if not item["boards"]:
            continue
        identity, variants = item["boards"]
        for other in items[index + 1:]:
            if not other["boards"]:
                continue
            other_identity, other_variants = other["boards"]
            if identity is not None and other_identity is not None:
                if identity != other_identity:
                    add("block", "boards", "Known GPU board identities differ; keep these hardware setups separate.", other)
                continue  # Matching strong identity takes priority over variant-label aliases.
            if variants and other_variants and len(variants | other_variants) > 1:
                add("block", "boards", "Known GPU board variants differ; keep these hardware setups separate.", other)
    for item in items:
        if item["status"] == "running":
            add("block", "running", "%s is marked as running. A benchmark in progress, or one interrupted without a final "
                "status, cannot be merged." % _quoted(item), item)
        if item["attribution"] == "unknown":
            add("block", "unknown_machine", "%s used an endpoint and kept no record of the computer that ran it, "
                "so its machine cannot be matched. Matching local hardware is required." % _quoted(item), item)
        elif item["attribution"] != "client" and not item["machine"]:
            add("block", "unknown_machine", "%s has no hardware record (an older file format). Matching local hardware "
                "is required." % _quoted(item), item)
        elif item["attribution"] == "client" and item["id"] not in assume_local:
            add("confirm", "confirm_local", "Confirm local inference before consolidating an endpoint run: %s was recorded "
                "without stating where inference ran." % _quoted(item), item)
        if not item["sessions_complete"]:
            add("block", "sessions", "%s has no execution session records. Each source measurement must have an execution session." % _quoted(item), item)
    merged = [item for item in items if item["mixed"]]
    for item in merged[1:]:
        add("block", "already_merged", "%s is already a merged benchmark, like %s. An existing consolidated benchmark must be the "
            "destination: merge single-engine benchmarks into one of them." % (_quoted(item), _quoted(merged[0])), item)
    if merged and items[0] is not merged[0]:
        add("block", "destination", "An existing consolidated benchmark must be the destination.", merged[0])
    reference = next((item for item in items if item["machine"]), None)
    for item in items:
        if item is reference or not item["machine"] or not reference:
            continue
        if item["machine"] != reference["machine"]:
            add("block", "machine", "Hardware differs: %s ran on %s, not on %s." % (
                _quoted(item), item["machine_name"], reference["machine_name"]), item)
        elif item["gpu"] != reference["gpu"]:
            add("block", "gpu", "Hardware or GPU driver differs: %s used %s (driver %s); %s used %s (driver %s)." % (
                _quoted(item), item["gpu"][0], item["gpu"][2], _quoted(reference), reference["gpu"][0], reference["gpu"][2]), item)
        if item["corpus"] != reference["corpus"] or item["protocol"] != reference["protocol"]:
            add("block", "protocol", "Corpus or measurement protocol differs between %s and %s." % (_quoted(item), _quoted(reference)), item)
        elif item["mode"] != reference["mode"]:
            add("block", "protocol", "Corpus or measurement protocol differs: browser timings and desktop helper "
                "measurements cannot share one benchmark (%s)." % _quoted(item), item)
    known = {}
    for item in items:
        for session_id in item["session_ids"]:
            if session_id in known and known[session_id] is not item:
                add("block", "sessions", "Execution session IDs overlap between %s and %s; these files cannot be safely "
                    "consolidated." % (_quoted(item), _quoted(known[session_id])), item)
                break
            known[session_id] = item
    labels = {"max_tokens": "answer length", "disable_thinking": "reasoning setting", "gpu_offload": "model placement"}
    by_engine = {}
    for item in items:
        if not item["mixed"]:
            by_engine.setdefault((item["provider"], item["engines"][0]), []).append(item)
    for (_, engine), members in by_engine.items():
        differing = [labels[key] for key in labels if len({json.dumps(member["settings"][key]) for member in members}) > 1]
        if differing:
            add("warn", "settings", "%s benchmarks used a different %s. Each session keeps its own settings, but charts "
                "show them as one engine." % (engine, " and ".join(differing)), *members)
    return issues


def _stopping(issues):
    return [issue for issue in issues if issue["level"] in ("block", "confirm")]


def _blocks(issues):
    return sum(issue["level"] == "block" for issue in issues)


def _superseded(items):
    kept, replaced = set(), 0
    for item in items:
        for case, _, _ in item["cases"]:
            replaced += case in kept
            kept.add(case)
    return replaced


def _ids(run_ids, minimum):
    if not isinstance(run_ids, list) or not minimum <= len(run_ids) <= 100 or any(
            not isinstance(i, str) for i in run_ids) or len(set(run_ids)) != len(run_ids):
        raise ValueError("Choose %d..100 distinct benchmark IDs, with the destination first." % minimum)
    return run_ids


def _assumed(assume_local):
    if assume_local is None:
        return set()
    if not isinstance(assume_local, list) or any(not isinstance(i, str) for i in assume_local):
        raise ValueError("assume_local must be a list of benchmark IDs.")
    return set(assume_local)


def merge_plan(run_ids, assume_local=None):
    """Preview a merge without touching any file.

    Returns the benchmarks in merge order (destination first), every issue in
    plain words, what the result would contain, and the other benchmarks of the
    same machine that could join.
    """
    ids, assumed = _ids(run_ids, 1), _assumed(assume_local)
    library = {item["id"]: item for item in library_facts()}
    if any(i not in library for i in ids):
        raise ValueError("A selected benchmark does not exist.")
    items = _ordered([library[i] for i in ids])
    issues = _inspect(items, assumed)
    engines = {}
    for item in items:
        for engine in item["engines"]:
            entry = engines.setdefault(engine, {"label": engine, "benchmarks": 0})
            entry["benchmarks"] += 1
    superseded = _superseded(items)
    reference = next((item for item in items if item["machine"]), None)
    also = []
    if reference:
        blocked = _blocks(issues)
        for item in sorted(library.values(), key=lambda item: item["created_at"], reverse=True):
            if item["id"] not in ids and item["machine"] == reference["machine"] and _blocks(
                    _inspect(_ordered(items + [item]), assumed | {item["id"]})) <= blocked:
                also.append(_public(item, [], assumed))
    return {
        "ok": len(items) > 1 and not _stopping(issues),
        "run_ids": [item["id"] for item in items],
        "label": items[0]["label"],
        "machine": reference["machine_name"] if reference else None,
        "runs": [_public(item, issues, assumed) for item in items],
        "issues": issues,
        "engines": list(engines.values()),
        "models": len({model for item in items for model in item["models"]}),
        "tests": sum(item["tests"] for item in items) - superseded,
        "superseded": superseded,
        "also_here": also,
    }


def _public(item, issues, assumed):
    return {"id": item["id"], "label": item["label"], "created_at": item["created_at"], "status": item["status"],
            "engines": item["engines"], "tests": item["tests"], "models": len(item["models"]),
            "machine": item["machine_name"], "attribution": item["attribution"], "loopback": item["loopback"],
            "confirmed": item["id"] in assumed,
            "issues": [{"level": issue["level"], "code": issue["code"], "message": issue["message"]}
                       for issue in issues if item["id"] in issue["run_ids"]]}


def suggestions():
    """Benchmarks of different engines that ran on one machine and can become one."""
    machines = {}
    for item in library_facts():
        if item["machine"] and item["status"] != "running":
            machines.setdefault(item["machine"], []).append(item)
    found = []
    for members in machines.values():
        group = []
        for item in _ordered(members):
            trial = group + [item]
            if not _blocks(_inspect(trial, {member["id"] for member in trial})):
                group = trial
        engines = list(dict.fromkeys(engine for item in group for engine in item["engines"]))
        if len(group) < 2 or len(engines) < 2:
            continue
        reference = next(item for item in group if item["machine"])
        found.append({"key": "%s:%d" % (reference["machine"], len(group)), "machine": reference["machine_name"],
                      "run_ids": [item["id"] for item in group], "engines": engines,
                      "needs_confirmation": sum(item["attribution"] == "client" for item in group),
                      "newest": max(item["created_at"] for item in group)})
    return sorted(found, key=lambda entry: entry["newest"], reverse=True)


# ---------------------------------------------------------------------------
# Merging and undoing


def _history_dir():
    return os.path.join(os.path.dirname(store.RUNS_DIR), ".history")


def _manifest_path(run_id):
    store.path_for(run_id)  # validates the identifier before it reaches a file name
    return os.path.join(_history_dir(), "merge-%s.json" % run_id)


def _manifest(run_id):
    try:
        with open(_manifest_path(run_id), encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return None
    backups = manifest.get("backups") if isinstance(manifest, dict) else None
    if not isinstance(backups, dict) or not backups or any(
            not isinstance(name, str) or os.path.basename(name) != name for name in backups.values()):
        return None
    return manifest


def undo_available(summary):
    """True while the merged benchmark is exactly as the merge left it."""
    if not summary.get("merge"):
        return False
    manifest = _manifest(summary.get("id"))
    return bool(manifest and manifest.get("updated_at") == summary.get("updated_at")
                and manifest.get("results") == summary.get("n_results"))


def undo_merge(run_id):
    """Put back the benchmarks exactly as they were before the last merge."""
    merged = store.load(run_id) if isinstance(run_id, str) else None
    manifest = _manifest(run_id) if merged else None
    if not manifest:
        raise ValueError("This benchmark has no merge that can be undone.")
    if merged.get("status") == "running":
        raise ValueError("Wait for this benchmark to stop before undoing its merge.")
    if manifest.get("updated_at") != merged.get("updated_at") or manifest.get("results") != len(merged.get("results") or []):
        raise ValueError("This benchmark changed after the merge, so the merge can no longer be undone. "
                         "The original files remain in the local archive.")
    copies = {}
    for original_id, name in manifest["backups"].items():
        path = os.path.join(_history_dir(), name)
        try:
            with open(path, encoding="utf-8") as handle:
                original = json.load(handle)
        except (OSError, ValueError):
            raise ValueError("A pre-merge copy is missing from the local archive; nothing was changed.") from None
        if not isinstance(original, dict) or original.get("id") != original_id:
            raise ValueError("A pre-merge copy does not match its benchmark; nothing was changed.")
        copies[store.path_for(original_id)] = path
    store.backup(run_id)  # The merged file stays recoverable too.
    for destination, path in copies.items():
        temporary = destination + ".restore.tmp"
        shutil.copyfile(path, temporary)
        os.replace(temporary, destination)
    os.unlink(_manifest_path(run_id))
    return {"restored": list(manifest["backups"])}


def consolidate(run_ids, label, assume_local=None, overlap="error"):
    """Store several benchmarks of one machine as one benchmark file.

    `assume_local` lists endpoint benchmarks the operator states were served by
    the computer that launched them. `overlap` decides a test measured more than
    once: "error" refuses, "newest" keeps the best measurement (a completed one,
    else the most recent) and archives the others in `previous_attempts`.
    """
    ids, assumed = _ids(run_ids, 2), _assumed(assume_local)
    if not isinstance(label, str) or not label.strip() or len(label) > 160:
        raise ValueError("Choose a machine benchmark label of 1..160 characters.")
    if overlap not in ("error", "newest"):
        raise ValueError("overlap must be error or newest.")
    runs = [store.load(i) for i in ids]
    if any(r is None for r in runs):
        raise ValueError("A selected benchmark does not exist.")
    stopping = _stopping(_inspect([facts(run) for run in runs], assumed))
    if stopping:
        raise ValueError(stopping[0]["message"])
    now = datetime.now(timezone.utc).isoformat()
    declared = [run["id"] for run in runs if _unattributed(run)]
    runs = [localized(run, now) if run["id"] in declared else run for run in runs]
    first = runs[0]

    result = copy.deepcopy(first)
    grouped = any(run.get("engine_groups") for run in runs)
    groups = copy.deepcopy(result.get("engine_groups") or [])
    rows, sessions, sources, attempts = [], [], [], []
    identities, session_ids, cases, superseded = {}, set(), {}, 0
    for original in runs:
        run = copy.deepcopy(original)
        cfg = run["config"]
        if cfg.get("provider") == "mixed":
            if run["id"] != first["id"]:
                raise ValueError("An existing consolidated benchmark must be the destination.")
            current_groups = {g["id"] for g in groups}
        else:
            engine = engine_label(run)
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
                if run.get("inference_local_assertion"):
                    group["inference_local_assertion"] = copy.deepcopy(run["inference_local_assertion"])
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
            key = _case(row)
            if key not in cases:
                cases[key] = len(rows)
                rows.append(row)
                continue
            if overlap != "newest":
                raise ValueError("Measurement cases overlap; repair or deduplicate them before consolidation.")
            # The same test measured twice: keep the best one in the benchmark, the other in its history.
            current = rows[cases[key]]
            if _better(row, current):
                rows[cases[key]], row = row, current
            attempts.append(dict(row, archived_at=now, archive_reason="superseded_by_merge"))
            superseded += 1
        attempts.extend(run.get("previous_attempts") or [])
        for configuration in run.get("model_configurations") or []:
            identities[configuration["configuration_id"]] = configuration
        if cfg.get("provider") == "mixed":
            sources.extend(run.get("source_runs") or [])
        else:
            sources.append({k:v for k,v in run.items() if k not in (
                "results", "sessions", "model_configurations", "previous_attempts", "comparison_group", "source_runs")})

    result.update(label=label.strip(), results=rows, sessions=sessions,
                  source_runs=sources, model_configurations=list(identities.values()))
    if attempts:
        result["previous_attempts"] = attempts
    if len(groups) > 1:
        result["engine_groups"] = groups
        result["config"] = machine_config(groups)
        result["provider"] = {"kind":"mixed", "label":" + ".join(g["label"] for g in groups),
                              "model_management":"per_session"}
        result.setdefault("measurement_scope", {}).update(runtime_gpu_placement="per execution session",
                                            context_control="per execution session", mtp_control="per execution session")
    else:
        # One engine measured in several benchmarks is one benchmark with more
        # sessions, exactly like a benchmark extended with Add tests.
        result["config"] = copy.deepcopy(groups[0]["config"])
        result["provider"] = copy.deepcopy(groups[0].get("provider") or first.get("provider") or {})
        if grouped:
            result["engine_groups"] = groups
        else:
            result.pop("engine_groups", None)
            for session in sessions:
                session.pop("engine_group_id", None)
    managed = next((run.get("lms_version") for run in runs if run.get("lms_version")
                    and run["config"].get("provider", "lmstudio") in ("lmstudio", "mixed")), None)
    if managed and not result.get("lms_version"):
        result["lms_version"] = managed  # Later LM Studio additions compare against the managed version.
    result.pop("comparison_group", None)
    result.pop("telemetry", None)  # Per-engine timelines remain in source_runs.
    for key in ("error", "interrupted_note"):
        result.pop(key, None)  # A source's own outcome stays with it in source_runs.
    finished = [datetime.fromisoformat(r["finished_at"].replace("Z", "+00:00")) for r in runs if r.get("finished_at")]
    if finished:
        result["finished_at"] = max(finished).isoformat()
    result["remaining_tests"] = _remaining(result, runs)
    result["status"] = "completed" if not result["remaining_tests"] else "interrupted"
    if result["remaining_tests"]:
        result["interrupted_note"] = "Some tests requested by the merged benchmarks were never recorded; use Add tests to continue them."
    result["updated_at"] = now
    entry = {"at":now, "source_run_ids":ids, "source_case_counts":{r["id"]:len(r.get("results", [])) for r in runs},
             "measurements_changed":False}
    if superseded:
        entry["superseded_measurements"] = superseded
    if declared:
        entry["local_inference_declared"] = declared
    result.setdefault("consolidation_history", []).append(entry)
    from .validation import validate_run
    errors = validate_run(result)
    if errors:
        raise ValueError("Consolidated result is invalid: " + "; ".join(errors))
    backups = {run["id"]: os.path.basename(store.backup(run["id"])) for run in runs}
    store.save(result)
    for run in runs[1:]:
        store.delete(run["id"])
    with open(_manifest_path(result["id"]), "w", encoding="utf-8") as handle:
        json.dump({"merged_id": result["id"], "updated_at": now, "results": len(rows), "backups": backups}, handle, indent=1)
    return result


def _remaining(result, runs):
    """Requested tests that no merged session recorded, counted once per test."""
    from .runner import DEFAULT_CONFIG, completed_cases, planned_cases
    try:
        requested = set()
        for session in result["sessions"]:
            if session.get("config"):
                requested.update(planned_cases(dict(DEFAULT_CONFIG, **session["config"])))
        if requested:
            return len(requested - completed_cases(result, dict(DEFAULT_CONFIG, **result["config"])))
    except (KeyError, TypeError, ValueError):
        pass  # Legacy session settings cannot be planned; keep what the sources recorded.
    return sum(r.get("remaining_tests", 0) for r in runs)
