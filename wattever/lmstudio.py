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
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

from .platform_util import popen_kwargs

ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\[\?25[lh]|\[K")
DEFAULT_BASE = os.environ.get("LMS_BASE_URL", "http://localhost:1234")


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
        return True, {
            "load_s": load_s,
            "context_length": info.get("contextLength"),
            "size_bytes": info.get("sizeBytes"),
            "quantization": (info.get("quantization") or {}).get("name"),
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
    out = []
    for m in data.get("data", []):
        out.append({
            "id": m.get("id"),
            "type": m.get("type"),
            "publisher": m.get("publisher"),
            "arch": m.get("arch"),
            "quantization": m.get("quantization"),
            "state": m.get("state"),
            "max_context_length": m.get("max_context_length"),
        })
    return out


def measure_completion(model, prompt, max_tokens, base=DEFAULT_BASE, timeout=1800,
                       on_first_token=None, on_progress=None, cancel=None,
                       temperature=0.0, disable_thinking=False, on_response=None):
    """Stream one completion and time the two phases separately.

    time-to-first-token covers tokenisation, prompt ingestion and the first
    sampled token, which is what "prefill" costs a user in practice. Decode is
    timed from the first token onward so prefill cost does not leak into the
    tk/s figure.

    Reasoning models stream `reasoning_content` before any visible `content`;
    the first delta of *either* kind ends the prefill phase, otherwise TTFT on a
    thinking model would be wrong by the length of its whole chain of thought.
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens),
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if disable_thinking:
        # `reasoning_effort: none` is what LM Studio actually honours here;
        # chat_template_kwargs.enable_thinking is accepted but ignored, which
        # silently leaves decode speed measured over a chain of thought instead
        # of over the answer. Verified on gemma4 and qwen35 templates.
        body["reasoning_effort"] = "none"

    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    t_start = time.perf_counter()
    t_first = None
    t_last = None
    deltas = 0
    usage = {}
    finish_reason = None
    sample = []
    stream_complete = False

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except OSError:
            pass
        raise RuntimeError("HTTP %s from LM Studio: %s" % (exc.code, detail)) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("Could not reach LM Studio at %s (%s)" % (base, exc)) from exc

    # hand the response back so a caller can close it to abort a blocked read
    # (a long prefill produces no bytes for minutes, so polling a flag is not
    # enough to make cancellation feel immediate)
    if on_response:
        on_response(resp)

    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                stream_complete = True
                break
            try:
                chunk = json.loads(payload)
            except ValueError:
                continue
            if chunk.get("error"):
                raise RuntimeError("LM Studio stream error: %s" % chunk["error"])
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content") or delta.get("reasoning_content")
            if choices[0].get("finish_reason"):
                finish_reason = choices[0]["finish_reason"]
            if piece:
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                    if on_first_token:
                        on_first_token(t_first - t_start)
                t_last = now
                deltas += 1
                if len(sample) < 60:
                    sample.append(piece)
                if on_progress:
                    on_progress(deltas, now - (t_first or now))

    t_end = time.perf_counter()
    if not stream_complete:
        raise RuntimeError("LM Studio stream ended before [DONE].")
    if t_first is None:
        raise RuntimeError("The model produced no tokens (prefill may have been rejected).")

    prompt_tokens = usage.get("prompt_tokens")
    completion_tokens = usage.get("completion_tokens") or deltas
    details = usage.get("completion_tokens_details") or {}
    decode_s = max(t_end - t_first, 1e-9)
    ttft = t_first - t_start

    return {
        "t_start": t_start,
        "t_first_token": t_first,
        "t_end": t_end,
        "ttft_s": ttft,
        "decode_s": decode_s,
        "wall_s": t_end - t_start,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "completion_tokens_estimated": not bool(usage.get("completion_tokens")),
        "reasoning_tokens": details.get("reasoning_tokens"),
        "deltas": deltas,
        # prefill throughput: every prompt token had to be ingested before the
        # first token could be sampled
        "prefill_tps": (prompt_tokens / ttft) if (prompt_tokens and ttft > 0) else None,
        # the first token is attributed to prefill, so it is excluded here
        "decode_tps": ((completion_tokens - 1) / decode_s) if completion_tokens and completion_tokens > 1 else None,
        "finish_reason": finish_reason,
        "sample_text": "".join(sample)[:400],
    }
