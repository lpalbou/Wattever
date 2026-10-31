"""Quantization format and method are separate, provenance-bearing labels."""
import re

_METHOD = re.compile(r"(?:^|[\s_./@()-])(QAT|QAD|UD|PTQ|GPTQ|AWQ)(?=$|[\s_./@()-])", re.I)


def safe_label(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_ .+()-]*", value):
        return None
    return None if value.lower() in ("unknown", "none", "null", "n/a") else value


def quantization_metadata(model):
    """Never infer QAT or PTQ from a storage type such as Q4_0 or Q4_K_M."""
    quant = model.get("quantization")
    block = quant if isinstance(quant, dict) else {}
    value = safe_label(block.get("name") if block else quant)
    source = model.get("quantization_source") or ("endpoint_reported" if value else "unknown")
    method = safe_label(model.get("quantization_method") or block.get("method"))
    method_source = model.get("quantization_method_source") or ("endpoint_reported" if method else "unknown")
    if method:
        method = method.upper()
    if not method:
        for key in ("display_name", "displayName", "filename", "id", "modelKey"):
            label = model.get(key)
            match = _METHOD.search(label) if isinstance(label, str) else None
            if match:
                method = match.group(1).upper()
                method_source = "model_identifier" if key in ("id", "modelKey", "filename") else "endpoint_reported"
                break
    return {"quantization": value, "quantization_source": source,
            "quantization_method": method, "quantization_method_source": method_source}


def backfill_run_metadata(run, catalog, timestamp):
    """Annotate historical rows from today's matching catalog, without changing measurements.

    A catalog lookup does not verify which weight bytes were used in the past.
    Existing storage formats must match before applying a current method label.
    The caller must back up the run before saving the returned corrections.
    """
    from .identity import canonical_digest
    indexed = {entry["id"]: entry for entry in catalog}
    changed = 0
    for row in run.get("results", []):
        current = indexed.get(row.get("model"))
        if not current or (row.get("quantization") and row["quantization"] != current.get("quantization")):
            continue
        meta = quantization_metadata(current)
        if not row.get("quantization"):
            row["quantization"] = meta["quantization"]
            row["quantization_source"] = "lmstudio_catalog_backfill"
        else:
            row.setdefault("quantization_source", "control_plane_verified")
        if not row.get("quantization_method"):
            row["quantization_method"] = meta["quantization_method"]
            row["quantization_method_source"] = "lmstudio_catalog_backfill" if meta["quantization_method"] else "unknown"
        identity = row.get("model_configuration")
        if identity:
            quant = identity["quantization"]
            if quant["value"] == "unknown" and row.get("quantization"):
                quant.update(value=row["quantization"], source="lmstudio_catalog_backfill")
            if row.get("quantization_method"):
                quant.update(method=row["quantization_method"], method_source=row["quantization_method_source"])
            identity["configuration_id"] = canonical_digest({k:v for k,v in identity.items() if k != "configuration_id"})
            row["configuration_id"] = identity["configuration_id"]
        changed += 1
    identities = {row["configuration_id"]: row["model_configuration"] for row in run.get("results", [])
                  if row.get("model_configuration") and row.get("configuration_id")}
    if identities:
        run["model_configurations"] = list(identities.values())
    run.setdefault("metadata_repair_history", []).append({"at":timestamp,"source":"lmstudio_catalog_backfill",
        "rows":changed,"scope":"Matching model ID and recorded format; current catalog labels, historical weight bytes not verified"})
    return changed
