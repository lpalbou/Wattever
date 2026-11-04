"""OpenAI-compatible transport configuration, separate from model management."""
import copy
import ipaddress
import os
import re
import urllib.parse
import urllib.request
import urllib.error
import json
import math
import secrets
import tempfile
import threading
import time

PRIVATE_KEYS = {"endpoint_base_url", "endpoint_api_key_env", "api_key", "apiKey", "authorization", "storage_probe_directory", "storage_probe_directories"}
ROUTE_REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results", ".endpoint-routes.json")
_IDENTITY_LOCK = threading.RLock()
_ROUTE_CACHE = {}
_PROBE_CACHE = {}
_CLOUDS = {"api.openai.com":("openai-api","OpenAI API","OpenAI"),
           "api.anthropic.com":("anthropic-api","Anthropic API","Anthropic"),
           "openrouter.ai":("openrouter-api","OpenRouter","OpenRouter"),
           "api.groq.com":("groq-api","Groq API","Groq"),
           "api.mistral.ai":("mistral-api","Mistral API","Mistral"),
           "api.deepseek.com":("deepseek-api","DeepSeek API","DeepSeek"),
           "api.together.xyz":("together-api","Together API","Together"),
           "api.fireworks.ai":("fireworks-api","Fireworks API","Fireworks"),
           "api.deepinfra.com":("deepinfra-api","DeepInfra API","DeepInfra"),
           "generativelanguage.googleapis.com":("gemini-api","Google Gemini API","Google Gemini")}
_CLOUD_PATHS={"api.openai.com":"/v1","api.anthropic.com":"/v1","openrouter.ai":"/api/v1",
              "api.groq.com":"/openai/v1","api.mistral.ai":"/v1","api.deepseek.com":"/v1",
              "api.together.xyz":"/v1","api.fireworks.ai":"/inference/v1",
              "api.deepinfra.com":"/v1/openai","generativelanguage.googleapis.com":"/v1beta/openai"}
_SERVICES = {"strata":"Strata","strata-server":"Strata","strata-engine":"Strata","strata-qwen":"Strata",
             "maya":"Maya",
             "llama.cpp":"llama.cpp","llama-cpp":"llama.cpp","llamacpp":"llama.cpp","llama-server":"llama.cpp",
             "lm studio":"LM Studio","lmstudio":"LM Studio","lm-studio":"LM Studio",
             "vllm":"vLLM","sglang":"SGLang","ollama":"Ollama","mlx-lm":"MLX LM","mlx_lm":"MLX LM",
             "koboldcpp":"KoboldCpp","aphrodite":"Aphrodite","tgi":"TGI","text-generation-inference":"TGI",
             "llama-cpp-python":"llama-cpp-python"}
_VERSION_PATTERN=r"(?:v?\d{1,4}(?:\.\d{1,4}){0,3}(?:(?:rc|alpha|beta)\d{0,3})?(?:\+\d{1,6})?|b\d{2,7})"


def _safe_version(value):
    if not isinstance(value,str) or not re.fullmatch(_VERSION_PATTERN,value):return "unknown"
    try:
        ipaddress.ip_address(value.lstrip("v").split("+",1)[0])
        return "unknown"  # Ambiguous IPv4-looking version strings are not public evidence.
    except ValueError:return value


def safe_identity_metadata(value):
    if not isinstance(value,dict):value={}
    services=set(_SERVICES.values())|{record[2] for record in _CLOUDS.values()}|{"unknown"}
    source=value.get("source","unknown")
    service=value.get("service","unknown")
    version=value.get("version","unknown")
    return {"source":source if source in ("detected","inferred","operator_override","unknown","user_reported") else "unknown",
            "service":service if isinstance(service,str) and service in services else "unknown",
            "version":_safe_version(version)}


def _location(base):
    host=(urllib.parse.urlsplit(base).hostname or "").lower()
    if host=="localhost" or host.endswith(".localhost"):return "local"
    try:
        address=ipaddress.ip_address(host)
        return "local" if address.is_loopback else "private" if address.is_private or address.is_link_local else "remote"
    except ValueError:
        return "private" if "." not in host or host.endswith((".local",".lan",".internal",".home",".corp")) else "remote"


def _safe_service_evidence(value,server_header=None):
    """Use only explicit allowlisted service names; never arbitrary host/model text."""
    if not isinstance(value,dict):value={}
    containers=[(value,False)]
    for key in ("server","backend","service","engine","metadata"):
        if isinstance(value.get(key),dict):containers.append((value[key],key!="metadata"))
    service=None
    version=None
    for block,allow_name in containers:
        for key in ("service","backend","engine","server_name","service_name")+(("name",) if allow_name else ()):
            name=block.get(key)
            if isinstance(name,str) and name.strip().lower() in _SERVICES:
                service=_SERVICES[name.strip().lower()]
        for key in ("version","server_version","backend_version","engine_version"):
            candidate=block.get(key)
            if _safe_version(candidate)!="unknown":version=_safe_version(candidate)
    if service:return {"service":service,"version":version or "unknown"}
    if isinstance(server_header,str):
        for name in sorted(_SERVICES,key=len,reverse=True):
            match=re.match(r"^"+re.escape(name)+r"(?:/([^\s]+))?(?:\s|$)",server_header.strip(),re.I)
            if match:
                return {"service":_SERVICES[name],"version":_safe_version(match.group(1)),"evidence":"known_server_header"}
    if isinstance(value.get("default_generation_settings"),dict) and (isinstance(value.get("model_path"),str) or "build_info" in value):
        return {"service":"llama.cpp","version":_safe_version(value.get("build_info")),
                "identification_source":"inferred","evidence":"llama_cpp_props_schema"}
    return {}


def _probe_identity(cfg,budget=2.8):
    """Bounded, same-origin local service discovery. Public origins are not probed."""
    base=cfg["endpoint_base_url"]
    if _location(base) not in ("local","private"):return {}
    parsed=urllib.parse.urlsplit(base)
    origin=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,"","",""))
    result={}
    active=[]
    stopped=threading.Event()
    deadline=time.monotonic()+budget
    headers={"Accept":"application/json"}
    key=api_key(cfg)
    if key:headers["Authorization"]="Bearer "+key
    def discover():
        for path in ("/health","/props","/version"):
            remaining=deadline-time.monotonic()
            if remaining<=0 or stopped.is_set():break
            try:
                request=urllib.request.Request(origin+path,headers=headers)
                with open_request(request,min(.8,remaining)) as response:
                    active.append(response)
                    body=response.read(65537)
                    if len(body)>65536:continue
                    try:payload=json.loads(body.decode("utf-8"))
                    except ValueError:payload={}
                    detected=_safe_service_evidence(payload,response.headers.get("Server"))
                    if detected:
                        result.update(detected,evidence=detected.get("evidence","same_origin_service_response"))
                        break
            except (OSError,ValueError,urllib.error.URLError):continue
    thread=threading.Thread(target=discover,daemon=True)
    thread.start();thread.join(timeout=budget)
    stopped.set()
    if thread.is_alive():
        for response in active:threading.Thread(target=response.close,daemon=True).start()
    return dict(result)


def _opaque_route(base,service,location):
    cache_key=(ROUTE_REGISTRY_PATH,base)
    with _IDENTITY_LOCK:
        if cache_key in _ROUTE_CACHE:return _ROUTE_CACHE[cache_key]
        try:
            with open(ROUTE_REGISTRY_PATH,encoding="utf-8") as handle:registry=json.load(handle)
            if not isinstance(registry,dict) or registry.get("version")!=1 or not isinstance(registry.get("routes"),dict):raise ValueError("Invalid route registry.")
        except (OSError,ValueError):registry={"version":1,"routes":{}}
        route=registry["routes"].get(base)
        if not isinstance(route,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}",route):
            prefix=re.sub(r"[^a-z0-9]+","-",service.lower()).strip("-") if service!="unknown" else "endpoint"
            route="%s-%s-%s" % (prefix,location,secrets.token_hex(6))
            registry["routes"][base]=route
            directory=os.path.dirname(ROUTE_REGISTRY_PATH)
            os.makedirs(directory,exist_ok=True)
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",dir=directory,prefix=".routes-",suffix=".tmp",delete=False) as handle:
                    temporary=handle.name;json.dump(registry,handle,indent=1)
                os.replace(temporary,ROUTE_REGISTRY_PATH)
            finally:
                if temporary:
                    try:os.unlink(temporary)
                    except FileNotFoundError:pass
        _ROUTE_CACHE[cache_key]=route
        return route


def resolve_identity(cfg,evidence=None,probe=True):
    """Resolve public route identity once. Private addresses never enter public IDs."""
    from .identity import public_text
    provider=cfg.get("provider","lmstudio")
    base=cfg.get("endpoint_base_url")
    if not base:
        if provider=="lmstudio":
            from .lmstudio import DEFAULT_BASE
            base=DEFAULT_BASE
        else:
            # Historical records may have intentionally omitted their operational URL.
            base=None
    base=normalize_base(base) if base else None
    prior=cfg.get("endpoint_identity")
    prior=prior if isinstance(prior,dict) else {}
    legacy_override=bool((cfg.get("endpoint_id") or cfg.get("endpoint_label")) and
                         (not prior or prior.get("source")=="operator_override" and prior.get("evidence")=="legacy_operator_fields"))
    override=cfg.get("endpoint_name_override")
    if override not in (None,""):override=public_text(override,"endpoint display name",120,required=True)
    location=_location(base) if base else "unknown"
    cloud=_CLOUDS.get((urllib.parse.urlsplit(base).hostname or "").lower()) if base else None
    detected=_safe_service_evidence(evidence)
    if cloud:
        route,label,service=cloud;source="inferred";origin="known_cloud";version="unknown";reason="exact_public_provider_hostname"
        cloud_url=urllib.parse.urlsplit(base)
        if cloud_url.path!=_CLOUD_PATHS[cloud_url.hostname.lower()] or cloud_url.scheme!="https" or cloud_url.port not in (None,443):
            route=_opaque_route(base,service,location);origin="opaque_local_mapping"
    else:
        if provider=="lmstudio":detected={"service":"LM Studio","version":"unknown","evidence":"declared_managed_provider"}
        if not detected and base:
            with _IDENTITY_LOCK:cached=_PROBE_CACHE.get(base)
            if cached and time.monotonic()-cached[0]<30:detected=dict(cached[1])
            elif probe and not legacy_override and location in ("local","private"):
                detected=_probe_identity(dict(cfg,endpoint_base_url=base))
                with _IDENTITY_LOCK:_PROBE_CACHE[base]=(time.monotonic(),dict(detected))
        service=detected.get("service","unknown");version=detected.get("version","unknown")
        source="inferred" if provider=="lmstudio" else detected.get("identification_source","detected") if detected else "unknown"
        reason=detected.get("evidence","explicit_service_metadata" if detected else "service_not_identified")
        label=(service+" " if service!="unknown" else "")+({"local":"local endpoint","private":"private endpoint","remote":"remote endpoint","unknown":"endpoint"}[location])
        if service=="unknown":label=label[0].upper()+label[1:]
        route=cfg["endpoint_id"] if legacy_override and cfg.get("endpoint_id") else _opaque_route(base,service,location) if base else "endpoint-unknown"
        origin="opaque_local_mapping" if base else "unknown"
    if legacy_override:
        route=public_text(cfg.get("endpoint_id") or route,"endpoint ID",120,required=True)
        label=public_text(cfg.get("endpoint_label") or label,"endpoint label",120,required=True)
        source="operator_override";reason="legacy_operator_fields"
    elif prior and cfg.get("endpoint_id") and not base:
        route=public_text(cfg["endpoint_id"],"endpoint ID",120,required=True)
        label=public_text(cfg.get("endpoint_label") or label,"endpoint label",120,required=True)
    if override:
        label=override;source="operator_override";reason="display_name_override"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}",route):raise ValueError("Invalid public endpoint ID.")
    cfg.update(endpoint_id=route,endpoint_label=label,
               endpoint_identity={"id":route,"label":label,"source":source,"service":service,"version":version,
                                  "location":location,"route_registry":origin,"evidence":reason,
                                  "scope":"route identity; actual inference backend and host are not verified"})
    return cfg

def normalize_base(value):
    value = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Endpoint must be an HTTP(S) base URL without credentials, query or fragment.")
    try:
        parsed.port
    except ValueError:
        raise ValueError("Endpoint port is invalid.")
    if parsed.path.endswith(("/chat/completions", "/models")):
        raise ValueError("Use the API base URL, not an individual route.")
    return value if parsed.path else value + "/v1"

def is_loopback(base):
    hostname = urllib.parse.urlsplit(base).hostname
    if hostname == "localhost": return True
    try: return ipaddress.ip_address(hostname).is_loopback
    except ValueError: return False

def api_key(cfg):
    name = cfg.get("endpoint_api_key_env") or ""
    if name and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("API-key environment variable name is invalid.")
    if name and not os.environ.get(name):
        raise ValueError("The configured API-key environment variable is not set.")
    return os.environ.get(name, "") if name else ""

def validate(cfg):
    import math
    folder = cfg.get("storage_probe_directory")
    if folder not in (None, "") and (not isinstance(folder, str) or len(folder) > 4096 or
                                     not os.path.isdir(folder)):
        raise ValueError("Storage test folder must be an existing local directory.")
    folders = cfg.get("storage_probe_directories")
    if folders is not None and (not isinstance(folders, list) or len(folders) > 8 or any(
            not isinstance(p, str) or len(p) > 4096 or not os.path.isdir(p) for p in folders)):
        raise ValueError("Choose up to eight existing additional storage folders.")
    if cfg.get("token_limit_field", "max_tokens") not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("Token limit field must be max_tokens or max_completion_tokens.")
    temperature = cfg.get("temperature")
    if temperature is not None and (type(temperature) not in (int, float) or not math.isfinite(temperature) or not 0 <= temperature <= 2):
        raise ValueError("Temperature must be omitted or a finite number in 0..2.")
    label = cfg.get("endpoint_label", "")
    if not isinstance(label, str) or len(label) > 120:
        raise ValueError("Endpoint label must be a string of at most 120 characters.")
    for key in ("api_key", "apiKey", "authorization"):
        if cfg.get(key): raise ValueError("Use an API-key environment variable; literal credentials are not accepted.")
    provider = cfg.get("provider", "lmstudio")
    if provider not in ("lmstudio", "openai"):
        raise ValueError("Provider must be lmstudio or openai.")
    if provider == "openai":
        if cfg.get("endpoint_reasoning_effort") not in (None, "none", "minimal", "low", "medium", "high", "xhigh"):
            raise ValueError("Endpoint reasoning effort must be omitted or a supported standard effort value.")
        cfg["endpoint_base_url"] = normalize_base(cfg.get("endpoint_base_url"))
        api_key(cfg)
        if type(cfg.get("endpoint_inference_local", False)) is not bool:
            raise ValueError("endpoint_inference_local must be true or false.")
        cfg["mtp_modes"] = ["off"]  # A scheduling label, not the server's effective MTP state.
        cfg.pop("model_modes", None)
        cfg.update(context_policy="provider_managed", gpu_offload="provider_managed",
                   disable_thinking=None, fallback_on_failure=False)
    metadata = cfg.get("reproducibility") or {}
    if not isinstance(metadata, dict): raise ValueError("Reproducibility settings must be an object.")
    allowed = {"engine", "engine_version", "model_revision", "quantization", "context_tokens", "kv_cache_dtype", "mtp_draft_tokens"}
    cleaned = {}
    for key, value in metadata.items():
        if key not in allowed: continue
        if key in ("context_tokens", "mtp_draft_tokens"):
            if value in (None, ""): continue
            if type(value) is not int or value < 0 or value > 10000000:
                raise ValueError("Reproducibility token settings must be non-negative integers.")
        elif not isinstance(value, str) or len(value) > 250:
            raise ValueError("Reproducibility text fields must be strings of at most 250 characters.")
        cleaned[key] = value
    cfg["reproducibility"] = cleaned
    claims = cfg.get("performance_claims") or []
    if not isinstance(claims, list) or len(claims) > 20: raise ValueError("Provide at most 20 performance claims.")
    import math
    cleaned_claims = []
    for claim in claims:
        if not isinstance(claim, dict): raise ValueError("Each performance claim must be an object.")
        if claim.get("kind") not in ("advertised_peak", "measured", "estimate") or claim.get("sparsity") not in ("dense", "sparse", "unknown"):
            raise ValueError("Performance claims need a valid kind and sparsity.")
        value = claim.get("value")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0: raise ValueError("Performance claim value must be finite and non-negative.")
        record = {"value": value, "kind": claim["kind"], "sparsity": claim["sparsity"], "provenance": "user_reported"}
        for key in ("precision", "unit", "source", "execution_path"):
            text = claim.get(key, "")
            if not isinstance(text, str) or len(text) > 300: raise ValueError("Performance claim text field is invalid or too long.")
            record[key] = text
        if record["precision"] not in ("FP4", "INT4", "FP8", "INT8", "FP16", "BF16", "FP32"):
            raise ValueError("Performance claim precision is not supported.")
        if record["unit"] != ("TOPS" if record["precision"].startswith("INT") else "TFLOP/s"):
            raise ValueError("Integer compute uses TOPS; floating-point compute uses TFLOP/s.")
        if not record["source"].strip() or not record["execution_path"].strip():
            raise ValueError("Performance claims require source and execution path.")
        cleaned_claims.append(record)
    cfg["performance_claims"] = cleaned_claims
    return cfg

def public_config(cfg):
    from .privacy import public_projection
    from .identity import public_request_model
    clean={k:copy.deepcopy(v) for k,v in cfg.items() if k not in PRIVATE_KEYS}
    if isinstance(clean.get("model"),str):clean["model"]=public_request_model(clean["model"])["value"]
    if isinstance(clean.get("models"),list):clean["models"]=[public_request_model(m)["value"] for m in clean["models"]]
    return public_projection(clean)

def public_provider(cfg):
    external = cfg.get("provider", "lmstudio") == "openai"
    return {"kind": "openai" if external else "lmstudio",
            "label": str(cfg.get("endpoint_label") or ("OpenAI-compatible endpoint" if external else "LM Studio"))[:120],
            "model_management": "external" if external else "lms_cli",
            "inference_location": "loopback" if external and is_loopback(cfg["endpoint_base_url"]) else "unverified_remote" if external else "local",
            "context_control": not external, "mtp_control": not external}

def sanitize_error(value, cfg):
    text = str(value)
    for secret in (cfg.get("endpoint_base_url"), os.environ.get(cfg.get("endpoint_api_key_env") or "", "")):
        if secret: text = text.replace(secret, "[redacted]")
    text = re.sub(r"https?://[^\s\"'<>]+", "[endpoint]", text)
    return text[:800]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must never forward a bearer credential to another origin.
        raise urllib.error.HTTPError(req.full_url, code, "Endpoint redirects are not followed.", headers, fp)

def open_request(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


_QUANT_PATTERN=r"(?:UD[-_])?(?:"+(r"IQ(?:1_[SM]|2_(?:XXS|XS|S|M)|3_(?:XXS|XS|S|M)|4_(?:XS|NL))"
                r"|Q[1-8]_(?:[01]|K(?:_(?:S|M|L|XL))?)|MXFP4|NVFP4|BF16|F16|F32|FP16|FP32|FP8(?:_(?:E4M3(?:FN)?|E5M2))?").replace("_","[-_]")+r")"


def _safe_model_text(value):
    from .privacy import LOCAL_DETAIL,NETWORK_DETAIL
    if not isinstance(value,str):return None
    value=value.strip()
    if not value or len(value)>80 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_ .+()-]*",value):return None
    if LOCAL_DETAIL.search(value) or NETWORK_DETAIL.search(value):return None
    return value


def _normalized_quant(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
        if not isinstance(value, str) or not re.fullmatch(_QUANT_PATTERN, value, re.I):
            return None
    safe=_safe_model_text(value)
    if not safe or safe.lower() in ("unknown","none","null","n/a"):return None
    if re.fullmatch(_QUANT_PATTERN,safe,re.I):
        normalized=safe.upper().replace("-","_")
        return "UD-"+normalized[3:] if normalized.startswith("UD_") else normalized
    return safe


def _quant_from_identifier(model_id):
    name=re.sub(r"\.gguf$","",model_id,flags=re.I)
    name=re.sub(r"-\d{5}-of-\d{5}$","",name)
    match=re.search(r"(?:^|[-_./@])("+_QUANT_PATTERN+r")$",name,re.I)
    return _normalized_quant(match.group(1)) if match else None


def _positive_context(*values):
    for value in values:
        if type(value) in (int,float) and 0<value<=100000000 and math.isfinite(value) and int(value)==value:
            return int(value)
    return None


def _model_entry(model):
    meta=model.get("meta") if isinstance(model.get("meta"),dict) else {}
    details=model.get("details") if isinstance(model.get("details"),dict) else {}
    quant=next((value for value in (_normalized_quant(model.get("quantization")),_normalized_quant(meta.get("quantization")),_normalized_quant(details.get("quantization"))) if value),None)
    quant_source="endpoint_reported" if quant else "unknown"
    if not quant:
        quant=_quant_from_identifier(model["id"])
        if quant:quant_source="model_identifier"
    loaded=_positive_context(model.get("loaded_context_length"),meta.get("n_ctx"),model.get("n_ctx"))
    context=_positive_context(model.get("max_context_length"),model.get("max_model_len"),model.get("context_length"),model.get("context_window"),
                              meta.get("max_context_length"),meta.get("context_length"),meta.get("n_ctx_train"),details.get("context_length"),details.get("context_window"))
    architecture=next((value for value in (_safe_model_text(model.get("arch")),_safe_model_text(model.get("architecture"))) if value),None)
    status=model.get("status")
    states=(model.get("state"),status.get("value") if isinstance(status,dict) else status)
    state=next((value.lower() for value in states if isinstance(value,str) and value.lower() in ("loaded","unloaded","loading","not-loaded","not_loaded","available","ready","error","unknown")),"externally managed")
    from .model_metadata import quantization_metadata
    original = quantization_metadata(model)
    metadata = quantization_metadata(dict(model, quantization=quant, quantization_source=quant_source,
        quantization_method=original["quantization_method"], quantization_method_source=original["quantization_method_source"]))
    return {"id":model["id"],"type":"llm","state":state,**metadata,
            "max_context_length":context or loaded,"loaded_context_length":loaded,"arch":architecture,"architecture":architecture}

def list_models(cfg, timeout=10):
    validate(cfg)
    headers = {"Accept": "application/json"}
    key = api_key(cfg)
    if key: headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(cfg["endpoint_base_url"] + "/models", headers=headers)
    try:
        with open_request(req, timeout) as response:
            payload = json.load(response)
            server_header=response.headers.get("Server")
        detected=_safe_service_evidence(payload,server_header)
        if detected:
            with _IDENTITY_LOCK:_PROBE_CACHE[cfg["endpoint_base_url"]]=(time.monotonic(),dict(detected,evidence=detected.get("evidence","discovery_response")))
            resolve_identity(cfg,probe=False)
        data = payload.get("data")
        if not isinstance(data, list): raise ValueError("Expected an OpenAI model list with a data array.")
        return [_model_entry(m) for m in data
                if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
    except Exception as exc:
        raise ValueError("Model discovery failed: %s. You can enter model IDs manually." % sanitize_error(exc, cfg)) from None

class UnavailableSampler:
    source = "unavailable_remote"
    error = "Inference hardware has not been confirmed local; client power is not inference power."
    def __init__(self, **kwargs): pass
    def start(self): pass
    def stop(self): pass
    def now(self): return {}
    def measure_idle(self, seconds): return {}
    def window(self, start, end): return {"available": False, "source": self.source}

class ExternalControl:
    """Explicitly no management actions against an externally served model."""
    available = False
    exe = None
    def version(self): return None
    def server_running(self): return True
    def start_server(self): return False
    def unload_all(self): pass
    def unload_confirmed(self, model, timeout=90): return False
    def load(self, model_key, context_length, **kwargs):
        return True, {"identifier":model_key,"context_length":None,"load_s":None,
                      "externally_managed":True}

def describe_run(run, cfg):
    run["provider"] = public_provider(cfg)
    if cfg.get("provider") != "openai": return
    run["measurement_scope"].update(runtime_gpu_placement="unknown; externally managed",
                                     runtime_gpu_placement_source="unknown",context_control=False,
                                     mtp_control=False,mtp_state="unknown; configured by endpoint operator")
    if cfg.get("endpoint_inference_local", False):
        run["measurement_scope"]["hardware_attribution"] = "user_reported: inference runs on the measurement client"
        run["measurement_scope"]["inference_local_source"] = "user_reported"
        return
    run["measurement_client"] = {"hardware":run["hardware"],"host":run["host"],"gpu":run["gpu"]}
    run["hardware"] = {"os":{"name":"unknown remote host","release":None,"architecture":None},
                       "cpu":{"name":None,"physical_cores":None,"logical_cores":None},
                       "system":{"manufacturer":None,"model":None,"memory_bytes":None},"gpus":[]}
    run["host"] = {"platform":"remote-unverified"}
    run["gpu"] = {"name":"Remote endpoint: hardware unverified"}
    run["classification"] = {"hardware_class":"remote-unverified","gpu_names":[],"platform":"remote-unverified"}
    run["measurement_scope"].update(power_gpu_sensor_index=None,power_boundary="unavailable for remote inference",
                                     hardware_attribution="unknown remote host; client telemetry is not inference telemetry")
