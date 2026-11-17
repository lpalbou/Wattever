"""A deliberately limited OpenAI Chat Completions gateway for local Strata."""
import json
import asyncio
import math
import os
import re
import ipaddress
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
import jsonschema
from referencing.exceptions import Unresolvable
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.cors import CORSMiddleware
from model_catalog import ModelManager, ModelSwitchError
from gateway_timings import GatewayWallMiddleware, RequestTimings, TRACE_HEADER, TRACE_STATE, valid_trace
ALIAS = os.getenv("GATEWAY_MODEL", "qwen3.8-flash-next")
LEGACY_CANONICAL = os.getenv("GATEWAY_ALIAS_TARGET", "qwen3.8-flash-next-unsloth-ud-iq4_xs")
BACKEND = os.getenv("GATEWAY_BACKEND", "http://127.0.0.1:8081").rstrip("/")
MAYA_BACKEND = os.getenv("GATEWAY_MAYA_BACKEND", "http://127.0.0.1:8097").rstrip("/")
DESKTOP_SETTINGS = Path(__file__).resolve().parent / 'desktop-settings.json'

def maya_catalog_entries():
    """Prepared Project Maya GLM profiles advertised alongside native Strata packs."""
    try:
        from strata_desktop import maya_choice
        choice = maya_choice()
    except (OSError, ValueError, KeyError, RuntimeError, TypeError, ImportError):
        return []
    if not choice:
        return []
    return [{"id": choice["id"], "object": "model", "created": 0, "owned_by": "local-maya"}]

@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient(base_url=BACKEND, timeout=httpx.Timeout(600, connect=5))
    app.state.maya_client = httpx.AsyncClient(base_url=MAYA_BACKEND, timeout=httpx.Timeout(600, connect=5))
    app.state.request_lock = asyncio.Lock()
    # Catalog listing is always available for prepared packs. Switching/loading on
    # chat is gated by GATEWAY_AUTOLOAD (Control sets this for the owned API).
    app.state.model_manager = ModelManager(Path(__file__).resolve().parent, BACKEND)
    app.state.autoload = os.getenv("GATEWAY_AUTOLOAD", "1") != "0"
    yield
    await app.state.client.aclose()
    await app.state.maya_client.aclose()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    allow_credentials=False,
    allow_private_network=True,
    max_age=600,
)

REQUEST_TIMINGS = RequestTimings()
app.add_middleware(GatewayWallMiddleware, store=REQUEST_TIMINGS)

class Invalid(Exception):
    def __init__(self, message, param=None, status=400, code=None):
        self.message, self.param, self.status, self.code = message, param, status, code

def error(message, status=400, param=None, code=None):
    kind = "invalid_request_error" if status < 500 else "server_error"
    return JSONResponse({"error": {"message": message, "type": kind, "param": param, "code": code}}, status_code=status)

@app.exception_handler(Invalid)
async def invalid_handler(request, exc):
    return error(exc.message, exc.status, exc.param, exc.code)

def fail(param, message):
    raise Invalid(message, param)

def keys(value, allowed, path):
    if not isinstance(value, dict):
        fail(path, f"{path} must be an object")
    unknown = set(value) - allowed
    if unknown:
        name = sorted(unknown)[0]
        fail(f"{path}.{name}", f"Unsupported parameter: {path}.{name}")

SUPPORTED = {"model", "messages", "stream", "stream_options", "max_tokens", "max_completion_tokens", "temperature", "top_p", "stop", "n", "tools", "tool_choice", "reasoning_effort", "response_format", "seed", "presence_penalty", "frequency_penalty", "parallel_tool_calls"}
MESSAGE_KEYS = ("role", "content", "tool_calls", "tool_call_id")


def normalize(body):
    """
    Map common client conventions onto the supported request, before validation.
    Real clients send optional OpenAI fields (user, metadata, logprobs, ...) and
    server extensions (chat_template_kwargs, top_k, ...). Fields this backend cannot
    act on are ignored rather than rejected, as OpenAI-compatible servers do; values
    of the fields we do support are still validated strictly.
    """
    if not isinstance(body, dict):
        return body
    kwargs = body.get("chat_template_kwargs")
    # llama.cpp/vLLM convention for "no thinking": honor it as reasoning_effort=none.
    if isinstance(kwargs, dict) and kwargs.get("enable_thinking") is False and body.get("reasoning_effort") is None:
        body["reasoning_effort"] = "none"
    if body.get("reasoning_effort") == "minimal":
        body["reasoning_effort"] = "low"  # lowest effort this backend offers besides none
    if body.get("reasoning_effort") is None:
        body.pop("reasoning_effort", None)
    # max_tokens is the deprecated spelling; clients that send both mean the newer one.
    if body.get("max_tokens") is not None and body.get("max_completion_tokens") is not None:
        body.pop("max_tokens")
    return body


# Pictures arrive inline (data URL) or by web address. Local file paths, which the
# backend would also read, are refused: a LAN client must not name files on this PC.
IMAGE_URL = re.compile(r"(?:data:image/[A-Za-z0-9.+-]+;base64,|https?://)\S+\Z")


def message_content(content, path):
    """
    OpenAI content-part arrays: text-only arrays become one string; arrays with
    image_url parts are forwarded as parts. Whether pictures can be read is the
    loaded model's answer (it is started with or without its image encoder).
    """
    if not isinstance(content, list):
        return content
    if not content:
        fail(path, "content must not be an empty array")
    parts, images = [], False
    for part in content:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind == "text" and isinstance(part.get("text"), str):
            parts.append({"type": "text", "text": part["text"]})
        elif kind == "image_url":
            spec = part.get("image_url")
            url = spec.get("url") if isinstance(spec, dict) else spec
            if not isinstance(url, str) or not IMAGE_URL.match(url):
                fail(path, "image_url must be a base64 data:image URL or an http(s) URL")
            parts.append({"type": "image_url", "image_url": {"url": url}})
            images = True
        else:
            fail(path, "Only text and image_url content parts are supported")
    return parts if images else "\n".join(part["text"] for part in parts)


def validate(req):
    if not isinstance(req, dict):
        fail("body", "body must be an object")
    req = {k: v for k, v in req.items() if k in SUPPORTED}
    if "reasoning_effort" in req and req["reasoning_effort"] not in ("none", "low", "medium", "high", "xhigh"):
        fail("reasoning_effort", "Unsupported reasoning_effort")
    for key in ("seed",):
        if req.get(key) is not None and (type(req[key]) is not int or not 1 <= req[key] <= 2**64 - 1):
            fail(key, "seed must be a positive integer no greater than 2^64-1")
    for key in ("presence_penalty", "frequency_penalty"):
        v = req.get(key)
        if v is not None and (type(v) not in (int, float) or not -2 <= v <= 2 or not math.isfinite(v)):
            fail(key, f"{key} must be a number between -2 and 2")
        if v not in (None, 0): fail(key, f"{key} is unsupported by the backend: its history window is limited")
    if req.get("parallel_tool_calls") is not None:
        if type(req["parallel_tool_calls"]) is not bool: fail("parallel_tool_calls", "parallel_tool_calls must be a boolean")
        if req["parallel_tool_calls"] is False and req.get("tools"): fail("parallel_tool_calls", "The backend cannot enforce parallel_tool_calls=false with tools")
    fmt = req.get("response_format")
    if fmt is not None:
        keys(fmt, {"type", "json_schema"}, "response_format")
        kind = fmt.get("type")
        if kind not in ("text", "json_object", "json_schema"): fail("response_format.type", "Unsupported response format")
        if kind != "json_schema" and "json_schema" in fmt: fail("response_format.json_schema", "json_schema requires type=json_schema")
        if kind in ("json_object", "json_schema") and req.get("tools"): fail("response_format", "Structured output with tools is unsupported")
        if kind == "json_schema":
            spec = fmt.get("json_schema")
            keys(spec, {"name", "description", "schema", "strict"}, "response_format.json_schema")
            name, schema = spec.get("name"), spec.get("schema")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name): fail("response_format.json_schema.name", "Invalid schema name")
            if "description" in spec and not isinstance(spec["description"], str): fail("response_format.json_schema.description", "description must be a string")
            if "strict" in spec and type(spec["strict"]) is not bool: fail("response_format.json_schema.strict", "strict must be a boolean")
            if not isinstance(schema, dict) or schema.get("type") != "object": fail("response_format.json_schema.schema", "schema must have an object root")
            def check_refs(node):
                if isinstance(node, dict):
                    for k, v in node.items():
                        if k in ("$ref", "$dynamicRef") and (not isinstance(v, str) or not v.startswith("#")): fail("response_format.json_schema.schema", "Only local schema references are supported")
                        check_refs(v)
                elif isinstance(node, list):
                    for v in node: check_refs(v)
            check_refs(schema)
            try: jsonschema.validators.validator_for(schema).check_schema(schema)
            except jsonschema.exceptions.SchemaError: fail("response_format.json_schema.schema", "Invalid JSON schema")
    model = req.get("model")
    if not isinstance(model, str) or not model:
        fail("model", "model must be a non-empty string")
    for key in ("max_tokens", "max_completion_tokens"):
        if key in req and (type(req[key]) is not int or req[key] <= 0):
            fail(key, f"{key} must be a positive integer")
    if "max_tokens" in req and "max_completion_tokens" in req:
        fail("max_completion_tokens", "Specify only one token limit")
    if "n" in req and (type(req["n"]) is not int or req["n"] != 1):
        fail("n", "Only n=1 is supported")
    for key, low, high in (("temperature", 0, 2), ("top_p", 0, 1)):
        if req.get(key) is not None and (type(req[key]) not in (int, float) or not low <= req[key] <= high or not math.isfinite(req[key])):
            fail(key, f"{key} must be a number between {low} and {high}")
    if "stream" in req and type(req["stream"]) is not bool:
        fail("stream", "stream must be a boolean")
    opts = req.get("stream_options")
    if opts is not None:
        if not isinstance(opts, dict): fail("stream_options", "stream_options must be an object")
        if not req.get("stream") or ("include_usage" in opts and type(opts["include_usage"]) is not bool):
            fail("stream_options", "stream_options requires stream=true and a boolean include_usage")
    if req.get("stop") is not None:
        stop = req["stop"]
        if isinstance(stop, str): stop = [stop]
        if not isinstance(stop, list) or not 1 <= len(stop) <= 4 or any(not isinstance(s, str) or not s for s in stop):
            fail("stop", "stop must be a non-empty string or an array of 1 to 4 non-empty strings")
    messages = req.get("messages")
    if not isinstance(messages, list) or not messages:
        fail("messages", "messages must be a non-empty array")
    pending = set()
    cleaned = []
    for i, m in enumerate(messages):
        p = f"messages[{i}]"
        if not isinstance(m, dict): fail(p, f"{p} must be an object")
        # Extra message fields (name, refusal, reasoning_content, ...) are echoed back by
        # clients; only MESSAGE_KEYS reach inference.
        m = dict(m)
        if "content" in m: m["content"] = message_content(m["content"], p + ".content")
        cleaned.append({k: m[k] for k in MESSAGE_KEYS if k in m})
        # Client extension, not an OpenAI message field: never send it to inference.
        if m.get("metadata") is not None and not isinstance(m["metadata"], dict):
            fail(p + ".metadata", "Message metadata must be an object or null")
        role = m.get("role")
        if role not in ("system", "developer", "user", "assistant", "tool"):
            fail(p + ".role", "Unsupported message role")
        if pending and role != "tool": fail(p, "Each tool call must have a tool response before the next message")
        content = m.get("content")
        calls = m.get("tool_calls")
        pictures = isinstance(content, list)   # message_content leaves a list only when it holds images
        if pictures and role != "user":
            fail(p + ".content", "Images are only supported in user messages")
        if not pictures and not isinstance(content, str) and not (role == "assistant" and content is None and calls):
            fail(p + ".content", "content must be text, or text and image parts")
        if "tool_calls" in m:
            if role != "assistant" or not isinstance(calls, list) or not calls:
                fail(p + ".tool_calls", "tool_calls requires a non-empty assistant tool call array")
            for call in calls:
                keys(call, {"id", "type", "function"}, p + ".tool_calls")
                fn = call.get("function")
                keys(fn, {"name", "arguments"}, p + ".tool_calls.function")
                if call.get("type") != "function" or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in pending or not isinstance(fn.get("name"), str) or not fn["name"] or not isinstance(fn.get("arguments"), str):
                    fail(p + ".tool_calls", "Invalid function tool call")
                try:
                    args = json.loads(fn["arguments"])
                    if not isinstance(args, dict): raise ValueError()
                except ValueError: fail(p + ".tool_calls", "Tool call arguments must be a JSON object string")
                pending.add(call["id"])
        if role == "tool":
            call_id = m.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending: fail(p + ".tool_call_id", "Tool response must match a preceding tool call")
            pending.remove(call_id)
        elif "tool_call_id" in m: fail(p + ".tool_call_id", "tool_call_id is only supported for tool messages")
    if pending: fail("messages", "Missing tool responses")
    names = set()
    if "tools" in req:
        tools = req["tools"]
        if not isinstance(tools, list) or not tools: fail("tools", "tools must be a non-empty array")
        for tool in tools:
            keys(tool, {"type", "function"}, "tools")
            fn = tool.get("function")
            keys(fn, {"name", "description", "parameters", "strict"}, "tools.function")
            name = fn.get("name")
            if tool.get("type") != "function" or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or name in names:
                fail("tools", "Invalid or duplicate function name")
            names.add(name)
            if "description" in fn and not isinstance(fn["description"], str): fail("tools", "Function description must be a string")
            if "parameters" in fn and (not isinstance(fn["parameters"], dict) or fn["parameters"].get("type") != "object"):
                fail("tools", "Function parameters must be an object JSON schema")
            if "parameters" in fn:
                try: jsonschema.validators.validator_for(fn["parameters"]).check_schema(fn["parameters"])
                except jsonschema.exceptions.SchemaError: fail("tools", "Invalid function parameter schema")
    choice = req.get("tool_choice")
    if choice is not None:
        if isinstance(choice, str):
            if choice not in ("none", "auto", "required"): fail("tool_choice", "Unsupported tool_choice")
            if choice == "required" and not names: fail("tool_choice", "tool_choice requires tools")
        else:
            keys(choice, {"type", "function"}, "tool_choice")
            fn = choice.get("function")
            keys(fn, {"name"}, "tool_choice.function")
            if choice.get("type") != "function" or not isinstance(fn.get("name"), str) or fn.get("name") not in names: fail("tool_choice", "tool_choice must name a declared function")
    forwarded = {k: v for k, v in req.items() if k not in ("stream_options", "n", "parallel_tool_calls") and v is not None}
    forwarded["messages"] = cleaned
    if "tools" in forwarded:
        # `strict` is an OpenAI schema hint the backend does not implement.
        forwarded["tools"] = [dict(tool, function={k: v for k, v in tool["function"].items() if k != "strict"})
                              for tool in forwarded["tools"]]
    return forwarded

def clean(obj, stream=False, expected_model=None):
    if not isinstance(obj, dict) or not isinstance(obj.get("id"), str) or not obj["id"] or type(obj.get("created")) is not int or obj.get("object") != ("chat.completion.chunk" if stream else "chat.completion") or not isinstance(obj.get("model"), str) or not isinstance(obj.get("choices"), list):
        raise ValueError("Invalid completion envelope")
    if not obj["choices"] and not (stream and isinstance(obj.get("usage"), dict)):
        raise ValueError("Missing completion choices")
    usage = obj.get("usage")
    if usage is not None:
        if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
            raise ValueError("Invalid usage")
        for key in ("prompt_tokens_details", "completion_tokens_details"):
            if key in usage and usage[key] is not None:
                details = usage[key]
                if not isinstance(details, dict) or any(type(v) is not int or v < 0 for v in details.values()): raise ValueError("Invalid usage details")
    for key in ("system_fingerprint", "service_tier"):
        if key in obj and obj[key] is not None and not isinstance(obj[key], str): raise ValueError("Invalid response field")
    for choice in obj["choices"]:
        if not isinstance(choice, dict) or type(choice.get("index")) is not int or "finish_reason" not in choice or choice["finish_reason"] not in (None, "stop", "length", "tool_calls", "content_filter"):
            raise ValueError("Invalid choice")
        msg = choice.get("delta" if stream else "message")
        if not isinstance(msg, dict) or (not stream and (msg.get("role") != "assistant" or "content" not in msg or choice["finish_reason"] is None)):
            raise ValueError("Invalid completion message")
        if "role" in msg and msg["role"] is not None and msg["role"] != "assistant": raise ValueError("Invalid assistant role")
        for key in ("content", "refusal"):
            if key in msg and msg[key] is not None and not isinstance(msg[key], str): raise ValueError("Invalid message field")
        calls = msg.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list): raise ValueError("Invalid tool calls")
            for call in calls:
                if not isinstance(call, dict): raise ValueError("Invalid tool call")
                if stream and (type(call.get("index")) is not int or call["index"] < 0): raise ValueError("Invalid tool call index")
                if not stream and (not isinstance(call.get("id"), str) or not call["id"] or call.get("type") != "function"): raise ValueError("Invalid tool call identity")
                if "id" in call and not isinstance(call["id"], str): raise ValueError("Invalid tool call id")
                if "type" in call and call["type"] != "function": raise ValueError("Invalid tool call type")
                fn = call.get("function")
                if fn is not None:
                    if not isinstance(fn, dict) or any(k in fn and not isinstance(fn[k], str) for k in ("name", "arguments")): raise ValueError("Invalid tool function")
                if not stream and (not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not isinstance(fn.get("arguments"), str)): raise ValueError("Invalid tool function")
    out = {k: obj[k] for k in ("id", "object", "created", "choices", "usage", "system_fingerprint", "service_tier") if k in obj}
    if expected_model is not None and obj["model"] != expected_model:
        raise ValueError("The backend returned a different model from the selected deployment")
    out["model"] = obj["model"]
    out["choices"] = []
    for choice in obj.get("choices", []):
        c = {k: choice[k] for k in ("index", "finish_reason") if k in choice}
        field = "delta" if stream else "message"
        msg = choice.get(field, {})
        c[field] = {k: msg[k] for k in ("role", "content", "refusal") if k in msg}
        if msg.get("tool_calls") is not None:
            c[field]["tool_calls"] = []
            for call in msg["tool_calls"]:
                clean_call = {k: call[k] for k in ("index", "id", "type") if k in call and (stream or k != "index")}
                if call.get("function") is not None:
                    clean_call["function"] = {k: call["function"][k] for k in ("name", "arguments") if k in call["function"]}
                c[field]["tool_calls"].append(clean_call)
        out["choices"].append(c)
    return out

def validate_output(content, fmt):
    if not fmt or fmt.get("type") == "text": return
    value = json.loads(content)
    if not isinstance(value, dict): raise ValueError("Expected a JSON object")
    if fmt.get("type") == "json_schema":
        schema = fmt["json_schema"]["schema"]
        jsonschema.validators.validator_for(schema)(schema).validate(value)

def upstream_error(response):
    try:
        data = response.json()
        err = data.get("error", {})
        msg = err.get("message", "Backend request failed") if isinstance(err, dict) else "Backend request failed"
        code = err.get("code") if isinstance(err, dict) else None
    except (ValueError, AttributeError):
        msg, code = "Backend request failed", None
    return error(msg, response.status_code, code=code)

@app.get("/health")
async def health(request: Request):
    routing = "prepared-presets-v1" if getattr(request.app.state, "autoload", False) else "active-only"
    return {"status": "ok", "service": "strata", "model_routing": routing,
            "catalog": "prepared" if request.app.state.model_manager is not None else "active-only"}


@app.get('/control/request-timings')
async def request_timings(request: Request):
    # Diagnostics are read-only, payload-free, and local to Control. A forwarded
    # client address header cannot turn a LAN request into a loopback request.
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local or request.headers.get('origin'):
        return error('Control timing diagnostics are available only on loopback.', 403)
    return JSONResponse({'scope': 'gateway_http', 'retention': REQUEST_TIMINGS.limit,
                         'requests': REQUEST_TIMINGS.snapshot()}, headers={'Cache-Control': 'no-store'})

async def active_model(client):
    """Read the actual serving identity; never advertise a fabricated ready model."""
    try:
        response = await client.get("/v1/models", timeout=5)
        response.raise_for_status()
        payload = response.json()
        items = payload.get("data")
        if not isinstance(items, list): raise ValueError("Missing model list")
        candidates = [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]]
        loaded = [item for item in candidates if item.get("state") == "loaded" or isinstance(item.get("status"), dict) and item["status"].get("value") == "loaded"]
        if loaded: candidates = loaded
        if len(candidates) != 1: raise ValueError("Expected one serving backend model")
        item = candidates[0]
        identifier = item["id"]
        if len(identifier) > 250 or identifier.startswith("/") or "\\" in identifier or ":" in identifier:
            raise ValueError("Invalid public model identity")
        created = item.get("created")
        return {"id": identifier, "object": "model", "created": created if type(created) is int and created >= 0 else 0, "owned_by": "local"}
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise Invalid("Could not discover a single serving model from the local backend", status=502, code="backend_unavailable") from None


def matches_model(requested, actual):
    # The historical alias names the original quant, not whichever model later loads.
    return requested == actual or requested == ALIAS and actual == LEGACY_CANONICAL

async def catalog(request):
    manager = request.app.state.model_manager
    listed = []
    if manager is not None:
        # Listing never loads a model or starts a download.
        listed = await asyncio.to_thread(manager.available_models)
    if not listed:
        # Prepared catalog empty: fall back to whatever the backend is serving, if any.
        try:
            listed = [await active_model(request.app.state.client)]
        except Invalid:
            if manager is not None:
                listed = []
            else:
                raise
    extra = await asyncio.to_thread(maya_catalog_entries)
    if extra:
        known = {info["id"] for info in listed}
        listed = [*listed, *(item for item in extra if item["id"] not in known)]
    return listed

async def settled(task):
    """Do not abandon a lifecycle/close operation when its caller disconnects."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result

class LockedStream(StreamingResponse):
    """Keep model selection serialized until the downstream stream is closed."""
    def __init__(self, *args, upstream, release, **kwargs):
        super().__init__(*args, **kwargs)
        self.upstream, self.release = upstream, release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await settled(asyncio.create_task(self.upstream.aclose()))
            finally:
                self.release()

@app.get("/v1/models")
async def models(request: Request):
    return {"object": "list", "data": await catalog(request)}

@app.get("/v1/models/{model_id}")
async def model(model_id: str, request: Request):
    for info in await catalog(request):
        if matches_model(model_id, info["id"]): return info
    raise Invalid(f"The model '{model_id}' does not exist.", "model", 404, "model_not_found")

@app.post("/v1/chat/completions")
async def chat(request: Request):
    try: body = await request.json()
    except (ValueError, UnicodeDecodeError): raise Invalid("Invalid JSON body")
    body = normalize(body)
    if isinstance(body, dict) and "reasoning_effort" not in body:
        try:
            settings = json.loads(DESKTOP_SETTINGS.read_text(encoding='utf-8'))
            effort = settings.get('reasoning_effort')
            if effort in ('none', 'low', 'medium', 'high'):
                body['reasoning_effort'] = effort
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError):
            return error('The desktop reasoning default could not be read. Save it again in Strata Control.', 503, code='desktop_settings_unavailable')
    forwarded = validate(body)
    lock = request.app.state.request_lock
    await lock.acquire()
    released = False
    transferred = False
    def release():
        nonlocal released
        if not released:
            released = True
            lock.release()
    try:
        response = await selected_chat(request, body, forwarded, release)
        transferred = isinstance(response, LockedStream)
        return response
    finally:
        if not transferred: release()

async def relay_chat(client, body, forwarded, release, canonical, trace_id=None):
    forwarded["model"] = canonical
    try:
        headers = {TRACE_HEADER: trace_id} if valid_trace(trace_id) else {}
        upstream = await client.send(client.build_request("POST", "/v1/chat/completions", json=forwarded,
                                                         headers=headers), stream=True)
    except httpx.RequestError:
        return error("Could not connect to the local backend", 502, code="backend_unavailable")
    if upstream.status_code >= 400:
        try:
            await upstream.aread()
            return upstream_error(upstream)
        except httpx.HTTPError:
            return error("Failed to read the local backend response", 502, code="backend_unavailable")
        finally:
            await upstream.aclose()
    if not body.get("stream"):
        try:
            data = json.loads(await upstream.aread())
            validate_output(data["choices"][0]["message"]["content"], body.get("response_format"))
            return JSONResponse(clean(data, expected_model=canonical))
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, jsonschema.exceptions.ValidationError, Unresolvable, httpx.HTTPError):
            return error("Invalid response from the local backend", 502, code="invalid_backend_response")
        finally: await upstream.aclose()
    include_usage = (body.get("stream_options") or {}).get("include_usage", False)
    async def events():
        usage = None
        last = None
        buffered = []
        content = []
        structured = (body.get("response_format") or {}).get("type") in ("json_object", "json_schema")
        try:
            lines = []
            async for line in upstream.aiter_lines():
                if line:
                    if line.startswith(":"):
                        yield ": keep-alive\n\n"
                    if line.startswith("data:"): lines.append(line[5:].lstrip(" "))
                    continue
                if not lines: continue
                data = "\n".join(lines)
                lines = []
                if data == "[DONE]":
                    if structured:
                        validate_output("".join(content), body.get("response_format"))
                        for chunk in buffered: yield chunk
                    if include_usage and usage is not None and last:
                        yield "data: " + json.dumps({**{k: last[k] for k in ("id", "object", "created", "model") if k in last}, "choices": [], "usage": usage}, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                obj = json.loads(data)
                if "error" in obj:
                    yield "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return
                last = clean(obj, True, expected_model=canonical)
                usage = last.get("usage") or usage
                last.pop("usage", None)
                if include_usage: last["usage"] = None
                event = "data: " + json.dumps(last, ensure_ascii=False) + "\n\n"
                if structured:
                    for choice in last.get("choices", []): content.append(choice.get("delta", {}).get("content") or "")
                    buffered.append(event)
                else: yield event
            yield "data: " + json.dumps({"error": {"message": "Backend stream ended unexpectedly", "type": "server_error", "param": None, "code": "invalid_backend_response"}}) + "\n\n"
            yield "data: [DONE]\n\n"
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, jsonschema.exceptions.ValidationError, Unresolvable):
            yield "data: " + json.dumps({"error": {"message": "Backend stream failed", "type": "server_error", "param": None, "code": "backend_stream_error"}}) + "\n\n"
            yield "data: [DONE]\n\n"
        finally: await upstream.aclose()
    return LockedStream(events(), upstream=upstream, release=release, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

async def selected_chat(request, body, forwarded, release):
    client = request.app.state.client
    manager = request.app.state.model_manager
    target = LEGACY_CANONICAL if body["model"] == ALIAS else body["model"]
    maya = await asyncio.to_thread(maya_catalog_entries)
    maya_ids = {entry["id"] for entry in maya}
    if manager is not None and getattr(request.app.state, "autoload", False):
        available = await asyncio.to_thread(manager.available_models)
        allowed = {info["id"] for info in available} | maya_ids
        if target not in allowed:
            raise Invalid(f"The model '{body['model']}' is not available on this endpoint.", "model", 404, "model_not_found")
        if target not in maya_ids:
            try:
                await settled(asyncio.create_task(manager.ensure_model(target, client)))
            except ModelSwitchError as exc:
                raise Invalid(exc.message, "model", 503, exc.code) from None
    if await request.is_disconnected():
        raise Invalid("Request disconnected before inference started", status=499, code="request_cancelled")
    backend = request.app.state.maya_client if target in maya_ids else client
    info = await active_model(backend)
    canonical = info["id"]
    if not matches_model(body["model"], canonical):
        raise Invalid(f"The model '{body['model']}' is not currently served.", "model", 404, "model_not_found")
    return await relay_chat(backend, body, forwarded, release, canonical,
                            getattr(request.state, TRACE_STATE, None))

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def unsupported(path: str):
    return error(f"Unsupported endpoint: /{path}", 404, code="unsupported_endpoint")
