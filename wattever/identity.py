"""Public, versioned model configuration identity; never hashes connection URLs."""
import hashlib
import json
import re

from .privacy import LOCAL_DETAIL, NETWORK_DETAIL, public_source

FINGERPRINT_VERSION = "wattever-model-configuration-v1"
UNKNOWN = "unknown"


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def public_text(value, field, maximum=250, required=False):
    if value in (None, ""):
        if required: raise ValueError("Provide a public %s." % field)
        return UNKNOWN
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError("%s must be bounded, public text." % field)
    value = value.strip()
    if not value or LOCAL_DETAIL.search(value) or NETWORK_DETAIL.search(value):
        raise ValueError("%s must not contain private URLs, addresses, credentials or local paths." % field)
    return value


def _reported(value, field):
    if field=="model source" and public_source(value):
        return {"value":value,"source":"user_reported"}
    return {"value": public_text(value, field), "source": "user_reported" if value not in (None, "", UNKNOWN) else UNKNOWN}


def public_request_model(model):
    """A transport model ID may be a private path; retain only a public alias."""
    if not isinstance(model,str) or not model.strip() or len(model)>2000:
        raise ValueError("Provide a bounded model identifier.")
    if (model.startswith(("/","\\")) or LOCAL_DETAIL.search(model) or NETWORK_DETAIL.search(model)):
        return {"value":"private model identifier","source":"redacted_transport_identifier"}
    return {"value":public_text(model,"model identifier",required=True),"source":"requested_model_identifier"}


def build_model_configuration(cfg, model=None, effective=None, require_endpoint=True):
    """Describe requested, reported and verified settings without conflating them.

    `effective` is reserved for actual control-plane evidence. An endpoint accepting
    a request parameter is not evidence that the engine honored it.
    """
    effective = effective or {}
    if not isinstance(cfg.get("endpoint_identity"),dict) or not cfg.get("endpoint_id") or not cfg.get("endpoint_label"):
        from .endpoints import resolve_identity
        cfg=resolve_identity(dict(cfg),probe=False)
    meta = cfg.get("reproducibility") or {}
    if not isinstance(meta, dict): raise ValueError("reproducibility must be an object.")
    if isinstance(meta.get("settings"), dict): meta = meta["settings"]
    model = model or cfg.get("model") or next(iter(cfg.get("models") or []), None)
    request_model = public_request_model(model)
    private_request = request_model["source"]=="redacted_transport_identifier"
    canonical_model = public_text(cfg.get("canonical_model_id") or (UNKNOWN if private_request else model), "canonical model identifier", required=True)
    endpoint_id = public_text(cfg.get("endpoint_id"), "endpoint ID", maximum=120, required=require_endpoint)
    label = public_text(cfg.get("endpoint_label"), "endpoint label", maximum=120, required=require_endpoint)
    if require_endpoint and (endpoint_id==UNKNOWN or label==UNKNOWN):
        raise ValueError("Provide a known public endpoint ID and label for capability runs.")
    if endpoint_id != UNKNOWN and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", endpoint_id):
        raise ValueError("Public endpoint ID must use letters, numbers, dots, underscores, colons or hyphens.")
    provider = cfg.get("provider", "lmstudio")
    if provider not in ("lmstudio", "openai"): raise ValueError("Unknown endpoint provider.")
    requested_reasoning = cfg.get("endpoint_reasoning_effort")
    if requested_reasoning is None and provider == "lmstudio" and cfg.get("disable_thinking"):
        requested_reasoning = "none"
    if requested_reasoning is not None and requested_reasoning not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        raise ValueError("Invalid requested reasoning effort.")
    requested_mtp = cfg.get("requested_mtp", UNKNOWN)
    if requested_mtp not in ("on", "off", UNKNOWN): raise ValueError("requested_mtp must be on, off or unknown.")
    reported_mtp = cfg.get("reported_mtp", UNKNOWN)
    if reported_mtp not in ("on", "off", UNKNOWN): raise ValueError("reported_mtp must be on, off or unknown.")
    effective_mtp = effective.get("mtp")
    if type(effective_mtp) is bool: effective_mtp = "on" if effective_mtp else "off"
    effective_reasoning = effective.get("reasoning")
    # The request flag alone is not proof that the engine disabled reasoning.
    if effective_reasoning is None and effective.get("reasoning_disabled") is not None:
        effective_reasoning = None
    from .endpoints import safe_identity_metadata
    endpoint_metadata=safe_identity_metadata(cfg.get("endpoint_identity"))
    endpoint_source=endpoint_metadata.get("source", "user_reported" if endpoint_id!=UNKNOWN else UNKNOWN)
    if endpoint_source not in ("user_reported","detected","inferred","operator_override",UNKNOWN):endpoint_source=UNKNOWN
    engine=_reported(meta.get("engine"),"engine")
    engine_version=_reported(meta.get("engine_version"),"engine version")
    if engine["value"]==UNKNOWN and endpoint_metadata.get("service") not in (None,"",UNKNOWN) and endpoint_source in ("detected","inferred"):
        engine={"value":public_text(endpoint_metadata["service"],"detected engine"),"source":"endpoint_reported" if endpoint_source=="detected" else "inferred"}
        if engine_version["value"]==UNKNOWN and endpoint_metadata.get("version") not in (None,"",UNKNOWN):
            engine_version={"value":public_text(endpoint_metadata["version"],"detected engine version"),"source":engine["source"]}
    identity = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "model": {"value": canonical_model, "source": "user_reported" if cfg.get("canonical_model_id") else UNKNOWN if private_request else "requested_model_identifier"},
        "request_model": request_model,
        "endpoint": {"id": endpoint_id, "label": label, "provider": provider,
                     "source": endpoint_source,
                     "scope": "public route identity; actual inference backend is not verified"},
        "quantization": {"value":public_text(effective.get("quantization") or meta.get("quantization") or cfg.get("quantization"), "quantization"),
                         "source":"control_plane_verified" if effective.get("quantization") else "user_reported" if meta.get("quantization") or cfg.get("quantization") else UNKNOWN,
                         "user_reported":public_text(meta.get("quantization") or cfg.get("quantization"), "reported quantization")},
        "engine": engine,
        "engine_version": engine_version,
        "model_revision": _reported(meta.get("model_revision"), "model revision"),
        "model_source": _reported(cfg.get("model_source") or meta.get("model_source"), "model source"),
        "kv_cache_dtype": _reported(meta.get("kv_cache_dtype"), "KV cache dtype"),
        "reasoning": {"requested": requested_reasoning if requested_reasoning is not None else "omitted",
                      "effective": public_text(effective_reasoning, "effective reasoning"),
                      "effective_source": "control_plane_verified" if effective_reasoning else UNKNOWN,
                      "user_reported": public_text(cfg.get("reported_reasoning"), "reported reasoning")},
        "mtp": {"requested": requested_mtp,
                "effective": public_text(effective_mtp, "effective MTP"),
                "effective_source": "control_plane_verified" if effective_mtp else UNKNOWN,
                "user_reported": reported_mtp},
    }
    for key in ("context_tokens", "mtp_draft_tokens"):
        value = meta.get(key)
        if value is not None and (type(value) is not int or not 0 <= value <= 10000000):
            raise ValueError("%s must be a bounded non-negative integer." % key)
        identity[key] = {"value": value if value is not None else UNKNOWN,
                         "source": "user_reported" if value is not None else UNKNOWN}
    from .model_metadata import quantization_metadata
    quant_meta = quantization_metadata(dict(effective, id=model))
    if quant_meta["quantization_method"]:
        identity["quantization"].update(method=quant_meta["quantization_method"],
                                         method_source=quant_meta["quantization_method_source"])
    if effective.get("quantization_source") in ("endpoint_reported", "model_identifier", "lmstudio_catalog_backfill"):
        identity["quantization"]["source"] = effective["quantization_source"]
    identity["configuration_id"] = canonical_digest(identity)
    return identity


def verify_model_configuration(value, require_endpoint=False):
    if not isinstance(value, dict) or value.get("fingerprint_version") != FINGERPRINT_VERSION:
        raise ValueError("Unsupported model configuration identity.")
    fields = {"fingerprint_version","model","request_model","endpoint","quantization","engine","engine_version","model_revision","model_source","kv_cache_dtype","reasoning","mtp","context_tokens","mtp_draft_tokens","configuration_id"}
    if set(value) != fields: raise ValueError("Unexpected or missing model configuration fields.")
    core = {key: item for key, item in value.items() if key != "configuration_id"}
    if value.get("configuration_id") != canonical_digest(core):
        raise ValueError("Model configuration fingerprint does not match its public fields.")
    endpoint = core.get("endpoint") or {}
    if not isinstance(endpoint,dict) or set(endpoint)!={"id","label","provider","source","scope"}: raise ValueError("Invalid public endpoint identity shape.")
    for key in ("id","label"): public_text(endpoint[key],"endpoint "+key,120,required=True)
    if require_endpoint and (endpoint["id"]==UNKNOWN or endpoint["label"]==UNKNOWN): raise ValueError("Capability identity requires a known public endpoint ID and label.")
    if endpoint["id"]!=UNKNOWN and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}",endpoint["id"]): raise ValueError("Invalid public endpoint ID.")
    if endpoint["provider"] not in ("lmstudio","openai") or endpoint["source"] not in (UNKNOWN,"user_reported","detected","inferred","operator_override"): raise ValueError("Invalid endpoint provenance.")
    if endpoint["scope"]!="public route identity; actual inference backend is not verified": raise ValueError("Invalid endpoint scope.")
    for key in ("model","request_model","quantization","engine","engine_version","model_revision","model_source","kv_cache_dtype"):
        block = core[key]
        expected = {"value","source","user_reported"} if key=="quantization" else {"value","source"}
        if key == "quantization" and isinstance(block, dict) and "method" in block:
            expected |= {"method", "method_source"}
            public_text(block["method"], "quantization method", required=True)
            if block.get("method_source") not in (UNKNOWN, "endpoint_reported", "model_identifier", "control_plane_verified", "user_reported", "lmstudio_catalog_backfill"):
                raise ValueError("Invalid quantization method provenance.")
        if not isinstance(block,dict) or set(block)!=expected: raise ValueError("Invalid "+key+" identity shape.")
        if not (key=="model_source" and public_source(block["value"])):
            public_text(block["value"],key,required=True)
        sources=(UNKNOWN,"user_reported","requested_model_identifier","control_plane_verified") + (("redacted_transport_identifier",) if key=="request_model" else ()) + (("endpoint_reported","inferred") if key in ("engine","engine_version") else ())
        if key == "quantization": sources += ("endpoint_reported", "model_identifier", "lmstudio_catalog_backfill")
        if block["source"] not in sources: raise ValueError("Invalid identity provenance.")
        if key=="request_model" and block["source"]=="redacted_transport_identifier" and block["value"]!="private model identifier": raise ValueError("Invalid redacted transport identity.")
        if "user_reported" in block: public_text(block["user_reported"],key,required=True)
    for key in ("reasoning","mtp"):
        block=core[key]
        if not isinstance(block,dict) or set(block)!={"requested","effective","effective_source","user_reported"}: raise ValueError("Invalid control identity shape.")
        for name in ("requested","effective","user_reported"): public_text(block[name],key+" "+name,required=True)
        if block["effective_source"] not in (UNKNOWN,"control_plane_verified"): raise ValueError("Invalid effective provenance.")
        if key=="reasoning" and block["requested"] not in ("omitted","none","minimal","low","medium","high","xhigh"): raise ValueError("Invalid reasoning request.")
        if key=="mtp" and any(block[name] not in (UNKNOWN,"on","off") for name in ("requested","effective","user_reported")): raise ValueError("Invalid MTP state.")
    for key in ("context_tokens","mtp_draft_tokens"):
        block=core[key]
        if not isinstance(block,dict) or set(block)!={"value","source"}: raise ValueError("Invalid token metadata shape.")
        if block["value"]!=UNKNOWN and (type(block["value"]) is not int or not 0<=block["value"]<=10000000): raise ValueError("Invalid token metadata.")
        if block["source"] not in (UNKNOWN,"user_reported"): raise ValueError("Invalid token metadata provenance.")
    return True


model_configuration = build_model_configuration
