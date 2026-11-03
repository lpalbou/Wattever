"""LM Studio control plane + the measured inference call.

Two channels are used deliberately:
  * the `lms` CLI for load/unload, because only it can set context length and
    GPU offload ratio -- the two knobs that decide whether a 40k prefill is
    possible at all on a small card;
  * the REST API for inference, because streaming gives an honest
    time-to-first-token and `stream_options.include_usage` gives the exact
    prompt token count the model actually saw.
"""
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .platform_util import popen_kwargs

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\[\?25[lh]|\[K")
DEFAULT_BASE = os.environ.get("LMS_BASE_URL", "http://127.0.0.1:1234")


class Cancelled(Exception):
    pass


class LoadError(Exception):
    pass


def _clean(text):
    """lms draws spinners; keep only the last meaningful lines."""
    text = ANSI.sub("", text or "")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # collapse the progress spam ("Loading model 12%") into nothing
    lines = [l for l in lines if not re.match(r"^Loading .*\d+%$", l)]
    return "\n".join(lines[-12:])


class LmsCli:
    def __init__(self):
        self.exe = shutil.which("lms") or shutil.which("lms.cmd")
        if not self.exe:
            # macOS installs the CLI under the home directory, which is not on
            # PATH when the app is launched from Finder rather than a shell
            for candidate in (os.path.expanduser("~/.lmstudio/bin/lms"),
                              os.path.expanduser("~/.cache/lm-studio/bin/lms"),
                              "/usr/local/bin/lms"):
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    self.exe = candidate
                    break

    @property
    def available(self):
        return bool(self.exe)

    def run(self, args, timeout=900):
        if not self.exe:
            raise LoadError("The `lms` CLI was not found on PATH. Install LM Studio and run "
                            "`lms bootstrap` once, then relaunch this tool.")
        env = dict(os.environ, NO_COLOR="1", FORCE_COLOR="0", TERM="dumb")
        proc = subprocess.run([self.exe] + list(args), capture_output=True, text=True,
                              timeout=timeout, env=env, errors="replace",
                              **popen_kwargs())
        return proc.returncode, _clean(proc.stdout), _clean(proc.stderr)

    def version(self):
        try:
            _, out, err = self.run(["version"], timeout=30)
            blob = (out or "") + " " + (err or "")
            m = re.search(r"\d+\.\d+\.\d+", blob)
            if m:
                return m.group(0)
            # current builds print "CLI commit: <sha>" instead of a semver
            m = re.search(r"commit:\s*([0-9a-f]{6,40})", blob, re.I)
            return ("commit " + m.group(1)) if m else "unknown"
        except (OSError, subprocess.SubprocessError, LoadError):
            return "unknown"

    def server_running(self, base=DEFAULT_BASE):
        try:
            with urllib.request.urlopen(base + "/api/v0/models", timeout=4):
                return True
        except (urllib.error.URLError, OSError):
            return False

    def start_server(self):
        try:
            self.run(["server", "start"], timeout=90)
        except (OSError, subprocess.SubprocessError, LoadError):
            return False
        for _ in range(20):
            if self.server_running():
                return True
            time.sleep(0.5)
        return False

    def ps(self):
        try:
            code, out, _ = self.run(["ps", "--json"], timeout=60)
            if code != 0:
                return []
            start = out.find("[")
            return json.loads(out[start:]) if start >= 0 else []
        except (OSError, subprocess.SubprocessError, ValueError, LoadError):
            return []

    def unload_all(self):
        try:
            self.run(["unload", "--all"], timeout=180)
        except (OSError, subprocess.SubprocessError, LoadError):
            pass

    def unload_confirmed(self, model, timeout=90):
        """Unload and verify the CLI's loaded-model list; errors are not success."""
        try:
            code, _, _ = self.run(["unload", model], timeout=timeout)
            if code != 0:
                return False
            code, out, _ = self.run(["ps", "--json"], timeout=30)
            start = out.find("[")
            if code != 0 or start < 0:
                return False
            entries = json.loads(out[start:])
            return isinstance(entries, list) and all(isinstance(item, dict) for item in entries) and not any(
                item.get("modelKey") == model or item.get("identifier") == model
                for item in entries)
        except (OSError, subprocess.SubprocessError, ValueError, LoadError):
            return False

    def load(self, model_key, context_length, gpu="auto", parallel=1, timeout=900,
             mtp=False):
        """Load one model. `gpu` is "auto" | "max" | "off" | "0.0".."1.0".

        `mtp` toggles load-time MTP speculative decoding, which only works on a
        GGUF that ships a supported MTP head; asking for it elsewhere fails
        immediately with a recognisable message, which the caller treats as
        "not applicable" rather than as a hardware limit.

        Returns (ok, detail) where detail carries the resolved context length and
        the error text when the load failed -- an OOM here is itself a result
        worth recording, not just a crash.
        """
        args = ["load", model_key, "-y", "-c", str(int(context_length)),
                "--parallel", str(int(parallel))]
        args.append("--speculative-draft-mtp" if mtp else "--no-speculative-draft-mtp")
        if gpu and gpu != "auto":
            args += ["--gpu", str(gpu)]
        t0 = time.perf_counter()
        try:
            code, out, err = self.run(args, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, {"error": "load timed out after %ds" % timeout,
                           "load_s": time.perf_counter() - t0}
        except (OSError, ValueError, subprocess.SubprocessError, LoadError) as exc:
            return False, {"error": "could not run `lms load`: %s" % exc,
                           "load_s": time.perf_counter() - t0}
        load_s = time.perf_counter() - t0
        blob = (out + "\n" + err).strip()
        if code != 0 or "loaded successfully" not in blob.lower():
            return False, {"error": blob or ("lms load exited %d" % code),
                           "load_s": load_s,
                           "mtp_unsupported": "mtp speculative decoding requires" in blob.lower()}
        info = {}
        for entry in self.ps():
            if entry.get("modelKey") == model_key or entry.get("identifier") == model_key:
                info = entry
                break
        from .model_metadata import quantization_metadata
        return True, {
            "load_s": load_s,
            "context_length": info.get("contextLength"),
            "size_bytes": info.get("sizeBytes"),
            **quantization_metadata(dict(info, id=model_key)),
            "architecture": info.get("architecture"),
            "params": info.get("paramsString"),
            "max_context_length": info.get("maxContextLength"),
            "identifier": info.get("identifier") or model_key,
            "log": blob.splitlines()[-1] if blob else "",
        }

    def estimate(self, model_key, context_length):
        """`--estimate-only` asks LM Studio whether a config can fit, without loading."""
        try:
            code, out, err = self.run(
                ["load", model_key, "-y", "-c", str(int(context_length)), "--estimate-only"],
                timeout=120)
            return {"ok": code == 0, "text": (out + "\n" + err).strip()}
        except (OSError, subprocess.SubprocessError, LoadError):
            return {"ok": False, "text": ""}


def list_models(base=DEFAULT_BASE, timeout=10):
    """Models LM Studio can serve right now (downloaded, indexed)."""
    req = urllib.request.Request(base + "/api/v0/models")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # v0 omits display names and consequently loses QAD and Dynamic labels.
    native = {}
    try:
        with urllib.request.urlopen(base + "/api/v1/models", timeout=min(timeout, 3)) as resp:
            native = {m["key"]: m for m in json.load(resp).get("models", []) if isinstance(m, dict) and m.get("key")}
    except (OSError, ValueError, TypeError):
        pass
    from .model_metadata import quantization_metadata
    out = []
    for m in data.get("data", []):
        out.append({
            "id": m.get("id"),
            "type": m.get("type"),
            "publisher": m.get("publisher"),
            "arch": m.get("arch"),
            **quantization_metadata(dict(m, **{k: v for k, v in native.get(m.get("id"), {}).items()
                                              if k in ("display_name", "quantization", "quantization_method")})),
            "state": m.get("state"),
            "max_context_length": m.get("max_context_length"),
        })
    return out


def _stream_payloads(response, cancel=None):
    """Dispatch SSE events; multiple data fields belong to one JSON payload."""
    fields = []
    for raw in response:
        if cancel is not None and cancel.is_set(): raise Cancelled()
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if fields:
                yield "\n".join(fields)
                fields = []
        elif line.startswith("data:"):
            value = line[5:]
            fields.append(value[1:] if value.startswith(" ") else value)
    if fields: yield "\n".join(fields)


def _usage_count(mapping, key):
    value = mapping.get(key)
    if value is not None and (type(value) is not int or value < 0):
        raise RuntimeError("Invalid non-negative integer token count: %s." % key)
    return value


def measure_completion(model, prompt, max_tokens, base=DEFAULT_BASE, timeout=1800,
                       on_first_token=None, on_progress=None, cancel=None,
                       temperature=0.0, disable_thinking=False, on_response=None,
                       api_key=None, api_base=False, provider_name="LM Studio",
                       reasoning_effort=None, capture_output=False, output_limit_chars=200000,
                       seed=None, top_p=None, token_limit_field="max_tokens"):
    """Stream one completion and time the two phases separately.

    time-to-first-token covers tokenisation, prompt ingestion and the first
    sampled token, which is what "prefill" costs a user in practice. Decode is
    timed from the first token onward so prefill cost does not leak into the
    tk/s figure.

    Reasoning models stream `reasoning_content` before any visible `content`;
    the first delta of *either* kind ends the prefill phase, otherwise TTFT on a
    thinking model would be wrong by the length of its whole chain of thought.

    Audit capture returns completed streams even without a final visible answer;
    finish reason and usage let the capability scorer distinguish an output limit
    from a transport failure. No observed text means unavailable TTFT/decode.
    Only final content is retained in audit output/sample text, never reasoning.
    Capture requires both a terminal choice finish reason and the [DONE] marker.
    """
    if token_limit_field not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("Token limit field must be max_tokens or max_completion_tokens.")
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        token_limit_field: int(max_tokens),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if temperature is not None: body["temperature"] = temperature
    if seed is not None: body["seed"] = int(seed)
    if top_p is not None: body["top_p"] = float(top_p)
    if capture_output and (type(output_limit_chars) is not int or not 1 <= output_limit_chars <= 200000):
        raise ValueError("Output capture limit must be between 1 and 200000 characters.")
    if reasoning_effort is not None:
        body["reasoning_effort"] = reasoning_effort
    elif disable_thinking:
        # `reasoning_effort: none` is what LM Studio actually honours here;
        # chat_template_kwargs.enable_thinking is accepted but ignored, which
        # silently leaves decode speed measured over a chain of thought instead
        # of over the answer. Verified on gemma4 and qwen35 templates.
        body["reasoning_effort"] = "none"

    headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if api_key: headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(
        base + ("/chat/completions" if api_base else "/v1/chat/completions"),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )

    t_start = time.perf_counter()
    t_first = None
    t_last = None
    deltas = 0
    usage = {}
    finish_reason = None
    sample = []
    stream_complete = False
    reported_timings = {}
    observed_output_kinds = set()
    first_output_kind = None
    empty_deltas_before_output = 0
    captured_output = []
    captured_chars = 0
    response_model = None

    try:
        if api_base:
            from .endpoints import open_request
            resp = open_request(req, timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except OSError:
            pass
        raise RuntimeError("HTTP %s from %s: %s" % (exc.code, provider_name, detail)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("Could not reach %s (%s)" % (provider_name, exc)) from exc

    # hand the response back so a caller can close it to abort a blocked read
    # (a long prefill produces no bytes for minutes, so polling a flag is not
    # enough to make cancellation feel immediate)
    if on_response:
        on_response(resp)

    with resp:
        for payload in _stream_payloads(resp, cancel):
            if payload == "[DONE]":
                stream_complete = True
                break
            try:
                chunk = json.loads(payload)
            except ValueError as exc:
                raise RuntimeError("Invalid JSON in completion stream.") from exc
            if not isinstance(chunk, dict):
                raise RuntimeError("Invalid completion stream object.")
            if isinstance(chunk.get("model"), str): response_model = chunk["model"][:250]
            if chunk.get("error"):
                raise RuntimeError("%s stream error: %s" % (provider_name, chunk["error"]))
            if chunk.get("usage"):
                if not isinstance(chunk["usage"], dict): raise RuntimeError("Invalid completion usage object.")
                usage = chunk["usage"]
            timings = chunk.get("timings") or {}
            if not isinstance(timings, dict): raise RuntimeError("Invalid provider timing object.")
            for key, value in timings.items():
                if key in ("prompt_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_ms", "predicted_per_second", "cache_n"):
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise RuntimeError("Invalid provider timing value.")
                    reported_timings[key] = value
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            reasoning = delta.get("reasoning_content")
            if content is not None and not isinstance(content, str) or reasoning is not None and not isinstance(reasoning, str):
                raise RuntimeError("Invalid streamed output text.")
            kinds = (["reasoning_content"] if reasoning else []) + (["content"] if content else [])
            piece = (reasoning or "") + (content or "")
            observed_output_kinds.update(kinds)
            if capture_output and content:
                captured_chars += len(content)
                if captured_chars > output_limit_chars:
                    raise RuntimeError("Completion exceeds the bounded output capture limit; response not scored.")
                captured_output.append(content)
            if (not piece and t_first is None and not delta.get("role")
                    and not choices[0].get("finish_reason")):
                empty_deltas_before_output += 1
            terminal_reason = choices[0].get("finish_reason")
            if capture_output and terminal_reason is not None:
                if not isinstance(terminal_reason, str) or not terminal_reason.strip():
                    raise RuntimeError("Invalid terminal choice finish_reason; expected nonempty text.")
            if terminal_reason:
                finish_reason = terminal_reason
            if piece:
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                    first_output_kind = kinds[0]
                    if on_first_token:
                        on_first_token(t_first - t_start)
                t_last = now
                deltas += 1
                sampled_text = content if capture_output else piece
                if sampled_text and len(sample) < 60:
                    sample.append(sampled_text)
                if on_progress:
                    on_progress(deltas, now - (t_first or now))

    t_end = time.perf_counter()
    if not stream_complete:
        raise RuntimeError("%s stream ended before [DONE]." % provider_name)
    if capture_output and finish_reason is None:
        raise RuntimeError("%s stream ended without a terminal choice finish_reason." % provider_name)
    if t_first is None and not capture_output:
        raise RuntimeError("The completion stream produced no observable content or reasoning text; generated tokens may be hidden by the endpoint.")

    prompt_tokens = _usage_count(usage, "prompt_tokens")
    completion_tokens = _usage_count(usage, "completion_tokens")
    if completion_tokens is None and not api_base and not capture_output: completion_tokens = deltas
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    if not isinstance(details, dict) or not isinstance(prompt_details, dict):
        raise RuntimeError("Invalid completion token-details object.")
    cached_tokens = _usage_count(prompt_details, "cached_tokens")
    reasoning_tokens = _usage_count(details, "reasoning_tokens")
    if (reasoning_tokens is not None and completion_tokens is not None
            and reasoning_tokens > completion_tokens):
        raise RuntimeError("Reasoning token count exceeds total completion tokens.")
    decode_s = max(t_end - t_first, 1e-9) if t_first is not None else None
    ttft = t_first - t_start if t_first is not None else None
    reasoning_streamed = "reasoning_content" in observed_output_kinds
    decode_scope_tokens = completion_tokens
    if reasoning_tokens is not None and not reasoning_streamed:
        decode_scope_tokens = completion_tokens - reasoning_tokens if completion_tokens is not None else None
        decode_scope = "visible_content_only; reported hidden reasoning excluded" if reasoning_tokens else "server_usage; no reasoning tokens reported"
    elif reasoning_streamed:
        decode_scope = "observed content and reasoning; unobserved reasoning cannot be ruled out"
    else:
        decode_scope = "server_usage_total; potential hidden reasoning; reasoning token count unavailable"
    if empty_deltas_before_output and not (reasoning_tokens == 0 or reasoning_tokens is not None and not reasoning_streamed):
        decode_scope_tokens = None
        decode_scope = "unavailable: empty deltas before observed output may hide an unknown number of reasoning tokens"
    if deltas < 2:
        decode_scope_tokens = None
        decode_scope = ("unavailable: no observable output; generated tokens may be hidden by the endpoint"
                        if t_first is None else
                        "unavailable: only one observed output delta; no subsequent output event to measure decode")
    decode_token_count = max(0, decode_scope_tokens - 1) if decode_scope_tokens is not None else None

    result = {
        "t_start": t_start,
        "t_first_token": t_first,
        "t_end": t_end,
        "ttft_s": ttft,
        "decode_s": decode_s,
        "wall_s": t_end - t_start,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "completion_tokens_estimated": usage.get("completion_tokens") is None,
        "reasoning_tokens": reasoning_tokens,
        "decode_token_count": decode_token_count,
        "timing_scope": {"boundary": "first observed nonempty content or reasoning text; not necessarily the first generated token",
                         "first_output_kind": first_output_kind,
                         "observed_output_kinds": sorted(observed_output_kinds),
                         "empty_deltas_before_output": empty_deltas_before_output,
                         "reasoning_streamed": reasoning_streamed,
                         "reasoning_token_count_source": "server_usage" if reasoning_tokens is not None else "unavailable",
                         "requested_reasoning_effort": reasoning_effort if reasoning_effort is not None else "none" if disable_thinking else None,
                         "reasoning_effort_effective": "unknown; request parameter does not verify engine behavior",
                         "decode_token_scope": decode_scope,
                         "first_delta_token_count": "unknown; throughput convention subtracts one token, not one chunk"},
        "deltas": deltas,
        "cached_prompt_tokens": cached_tokens,
        "metric_sources": {"ttft_s": "client_monotonic_clock_to_first_observed_output" if t_first is not None else "unavailable; no observable output",
                           "prefill_tps": "prompt_tokens_divided_by_client_ttft; includes queue/network/tokenization and any unobserved reasoning; cache state may be unknown" if t_first is not None else "unavailable; no observable output or client TTFT",
                           "decode_tps": "decode_token_count_over_client_duration_after_first_observed_output; see timing_scope",
                           "completion_tokens": "server_usage" if usage.get("completion_tokens") is not None else "unavailable" if api_base or capture_output else "stream_chunk_estimate"},
        "reported_timings": reported_timings,
        # prefill throughput: every prompt token had to be ingested before the
        # first token could be sampled
        "prefill_tps": (prompt_tokens / ttft) if (prompt_tokens and ttft is not None and ttft > 0) else None,
        # the first token is attributed to prefill, so it is excluded here
        "decode_tps": (decode_token_count / decode_s) if decode_token_count and decode_s is not None else None,
        "finish_reason": finish_reason,
        "sample_text": "".join(sample)[:400],
        "response_model": response_model,
    }
    if capture_output:
        result["output_text"] = "".join(captured_output)
        result["output_capture"] = {"scope": "visible content only; streamed reasoning excluded", "limit_chars": output_limit_chars, "truncated": False,
                                    "visible_content_observed": bool(captured_output),
                                    "visible_answer_present": bool(result["output_text"].strip()),
                                    "reasoning_observed": reasoning_streamed,
                                    "observable_output_present": t_first is not None,
                                    "stream_complete": stream_complete}
    return result
