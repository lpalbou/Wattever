#!/usr/bin/env python3
"""Exercise the local OpenAI-compatible gateway; never runs requests on import."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI, APIStatusError

BASE_URL = "http://127.0.0.1:8086/v1"
MODEL = "qwen3.8-flash-next-unsloth-ud-iq4_xs"


def dump(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def expect(condition, message):
    if not condition:
        raise AssertionError(message)


class Harness:
    def __init__(self, base_url, model, timeout):
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key="local", timeout=timeout,
                             max_retries=0, _strict_response_validation=True)
        self.results = []

    def record(self, name, fn):
        start = time.perf_counter()
        row = {"name": name}
        try:
            detail = fn()
            row.update(status="pass", details=detail)
        except Skip as exc:
            row.update(status="skip", reason=str(exc))
        except Exception as exc:
            row.update(status="fail", error_type=type(exc).__name__, error=str(exc))
            if isinstance(exc, APIStatusError):
                row["http_status"] = exc.status_code
                row["response"] = exc.response.text[:10000]
        row["elapsed_seconds"] = round(time.perf_counter() - start, 4)
        self.results.append(row)
        print(f"{row['status'].upper():4} {name} ({row['elapsed_seconds']:.2f}s)", flush=True)
        if row["status"] == "fail":
            print(f"     {row['error_type']}: {row['error']}", flush=True)

    def chat(self, **kwargs):
        return self.client.chat.completions.create(model=self.model, **kwargs)

    def models(self):
        response = self.client.models.list()
        data = dump(response)
        expect(any(m.id == self.model for m in response.data), "configured model missing from /models")
        return data

    def simple(self):
        response = self.chat(messages=[{"role": "user", "content": "What is 2 + 2? Reply with the number only."}],
                             max_tokens=64, temperature=0, reasoning_effort="none")
        expect(bool(response.id) and response.object == "chat.completion", "invalid completion identity")
        expect(bool(response.choices), "no choices")
        choice = response.choices[0]
        expect(choice.message.role == "assistant" and "4" in (choice.message.content or ""), "expected answer 4")
        expect(choice.finish_reason in ("stop", "length"), "missing finish reason")
        expect(response.usage is not None, "missing usage")
        return dump(response)

    def streaming(self):
        stream = self.chat(messages=[{"role": "user", "content": "Say hello briefly."}], max_tokens=64,
                           temperature=0, reasoning_effort="none", stream=True,
                           stream_options={"include_usage": True})
        chunks = [dump(c) for c in stream]
        expect(bool(chunks), "no streaming chunks")
        identity = (chunks[0]["id"], chunks[0]["created"], chunks[0]["model"])
        expect(all((c["id"], c["created"], c["model"]) == identity for c in chunks),
               "stream identity changed between chunks")
        expect(all(c["object"] == "chat.completion.chunk" for c in chunks), "invalid stream object type")
        expect(any(c.get("choices") and c["choices"][0].get("delta", {}).get("content") for c in chunks), "no streamed content")
        expect(any(c.get("choices") and c["choices"][0].get("finish_reason") for c in chunks), "missing finish reason")
        expect(chunks[-1].get("choices") == [] and chunks[-1].get("usage") is not None,
               "final stream chunk must have empty choices and usage")
        return {"chunks": chunks}

    def cap(self):
        response = self.chat(messages=[{"role": "user", "content": "Write a long detailed story about a lighthouse."}],
                             max_tokens=1, temperature=0, reasoning_effort="none")
        expect(response.choices[0].finish_reason == "length", "one-token cap should finish with length")
        return dump(response)

    def tools(self, named=False):
        tool = {"type": "function", "function": {"name": "lookup_temperature", "description": "Get the current temperature for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"], "additionalProperties": False}}}
        messages = [{"role": "user", "content": "What is the temperature in Paris? Use the tool."}]
        selection = {"type": "function", "function": {"name": "lookup_temperature"}} if named else "required"
        first = self.chat(messages=messages, tools=[tool], tool_choice=selection, max_tokens=128,
                          temperature=0, reasoning_effort="none")
        message = first.choices[0].message
        expect(first.choices[0].finish_reason == "tool_calls" and bool(message.tool_calls), "expected tool call finish")
        call = message.tool_calls[0]
        expect(call.function.name == "lookup_temperature", "wrong tool name")
        arguments = json.loads(call.function.arguments)
        expect(isinstance(arguments, dict) and "city" in arguments, "invalid tool arguments")
        messages.append(message.model_dump(exclude_none=True))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": '{"temperature_c": 18}'})
        follow = self.chat(messages=messages, tools=[tool], max_tokens=128, temperature=0, reasoning_effort="none")
        expect("18" in (follow.choices[0].message.content or ""), "tool result missing from follow-up answer")
        return {"initial": dump(first), "followup": dump(follow)}

    def instructions(self, role):
        response = self.chat(messages=[{"role": role, "content": "Always respond with the single word PINEAPPLE."},
                                       {"role": "user", "content": "Respond now."}],
                             max_tokens=32, temperature=0, reasoning_effort="none")
        expect("PINEAPPLE" in (response.choices[0].message.content or "").upper(), f"{role} instruction was not followed")
        return dump(response)

    def structured(self):
        try:
            response = self.chat(messages=[{"role": "user", "content": "Return a JSON object with answer equal to 4."}],
                                 response_format={"type": "json_schema", "json_schema": {"name": "answer", "strict": True,
                                     "schema": {"type": "object", "properties": {"answer": {"type": "integer"}},
                                                "required": ["answer"], "additionalProperties": False}}},
                                 max_tokens=128, temperature=0, reasoning_effort="none")
        except APIStatusError as exc:
            if exc.status_code in (400, 422, 501) and any(word in exc.response.text.lower() for word in ("unsupported", "not supported", "not implemented")):
                raise Skip(f"structured outputs explicitly unsupported: HTTP {exc.status_code}") from exc
            raise
        value = json.loads(response.choices[0].message.content)
        from jsonschema import validate
        validate(value, {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"], "additionalProperties": False})
        expect(value["answer"] == 4, "incorrect structured answer")
        return dump(response)

    def negative(self, **kwargs):
        try:
            self.client.chat.completions.create(**kwargs)
        except APIStatusError as exc:
            expect(400 <= exc.status_code < 500, f"expected 4xx, got {exc.status_code}")
            return {"http_status": exc.status_code, "body": exc.response.text[:10000]}
        raise AssertionError("invalid request unexpectedly succeeded")

    def benchmark(self, words, repeats, retrieval=False):
        # Stable varied records make the prompt reproducible across runs.
        records = [f"Record {i}: station {i % 97} logged pressure {990 + i % 40} and temperature {10 + i % 23}; operations remained normal." for i in range(words // 15)]
        answer = "indigo-marble-7319"
        if retrieval:
            records[len(records) // 2] = f"Record 8675309: the unique maintenance access phrase is {answer}; preserve this exact phrase."
            question = "Find record 8675309 and reply with its maintenance access phrase exactly."
        else:
            question = "Write a detailed operational report of at least 500 words about the records below, covering pressure, temperature, and station patterns."
        paragraph = " ".join(records)
        messages = [{"role": "user", "content": question + "\n\n" + paragraph}]
        runs = []
        for index in range(repeats):
            start = time.perf_counter()
            first_content = None
            chunks = []
            content_parts = []
            content_chunks = 0
            for chunk in self.chat(messages=messages, max_tokens=256, temperature=0, reasoning_effort="none",
                                   stream=True, stream_options={"include_usage": True}):
                now = time.perf_counter()
                item = dump(chunk)
                parts = [c.get("delta", {}).get("content") for c in item.get("choices", [])]
                parts = [part for part in parts if part]
                if parts:
                    content_chunks += 1
                    content_parts.extend(parts)
                    if first_content is None:
                        first_content = now
                chunks.append(item)
            end = time.perf_counter()
            usage = next((c.get("usage") for c in reversed(chunks) if c.get("usage")), None)
            expect(usage is not None and first_content is not None, "benchmark requires streamed content and usage")
            content = "".join(content_parts)
            expect(not retrieval or answer in content, "long prompt retrieval failed: planted phrase absent from answer")
            completion_tokens = usage.get("completion_tokens")
            duration = end - first_content
            details = usage.get("prompt_tokens_details") or {}
            runs.append({"run_number": index + 1, "cache_context": "initial request; cache state unknown" if index == 0 else "repeated identical prompt; cache state unknown unless reported",
                         "ttft_seconds": round(first_content - start, 4), "total_seconds": round(end - start, 4),
                         "stream_after_first_content_seconds": round(duration, 4), "content_chunks": content_chunks,
                         "completion_tokens": completion_tokens, "prompt_tokens": usage.get("prompt_tokens"),
                         "cached_prompt_tokens": details.get("cached_tokens"),
                         "approx_output_tokens_per_second_after_first_content": round(max(completion_tokens - 1, 0) / duration, 2) if completion_tokens and duration > 0 else None,
                         "answer": content, "usage": usage, "final_chunk": chunks[-1]})
        return {"prompt_characters": len(paragraph), "requested_max_output_tokens": 256, "runs": runs,
                "note": "Client-observed approximation: reported completion tokens minus one divided by elapsed time from first content to stream end. A chunk can contain multiple tokens and usage may include non-content tokens, so this is not a server decode metric. Cached tokens are shown only when reported; initial cache state is unknown."}



class Skip(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", nargs="?", choices=["quick", "bench", "long"], default="quick",
                        help="quick: acceptance tests; bench: three ~4K-token prompt runs; long: one ~20-30K-token retrieval run")
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--timeout", type=float, default=600, help="per-request timeout in seconds")
    parser.add_argument("--output", type=Path, default=None, help="JSON results path (default: acceptance-<mode>.json)")
    args = parser.parse_args()
    harness = Harness(args.base_url, args.model, args.timeout)
    if args.mode == "quick":
        harness.record("models", harness.models)
        harness.record("basic_chat", harness.simple)
        harness.record("stream_usage", harness.streaming)
        harness.record("finish_length", harness.cap)
        harness.record("tool_required_followup", lambda: harness.tools(False))
        harness.record("tool_named_followup", lambda: harness.tools(True))
        for role in ("system", "developer"):
            harness.record(f"{role}_instruction", lambda role=role: harness.instructions(role))
        harness.record("structured_json_schema", harness.structured)
        common = {"model": args.model, "messages": [{"role": "user", "content": "Hi"}]}
        harness.record("invalid_model", lambda: harness.negative(**{**common, "model": "nonexistent-model-acceptance-test"}))
        harness.record("invalid_max_tokens_zero", lambda: harness.negative(**common, max_tokens=0))
        harness.record("invalid_max_tokens_type", lambda: harness.negative(**common, max_tokens="invalid"))
        harness.record("invalid_n", lambda: harness.negative(**common, n=0))
    elif args.mode == "bench":
        harness.record("benchmark_4k", lambda: harness.benchmark(2250, 3))
    else:
        harness.record("benchmark_long_retrieval", lambda: harness.benchmark(14250, 1, retrieval=True))
    output = args.output or Path(f"acceptance-{args.mode}.json")
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "base_url": args.base_url,
              "model": args.model, "mode": args.mode, "results": harness.results}
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Results: {output}")
    return 1 if any(r["status"] == "fail" for r in harness.results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
