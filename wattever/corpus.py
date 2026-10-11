"""Prompt construction for a given prefill target.

Two things make this less trivial than slicing a string:

1. Tokenisers disagree. The same prose is 3.6 chars/token for one model family
   and 4.3 for another, so a fixed character budget would miss a 40k target by
   thousands of tokens. We start from an estimate and refine it per model from
   the exact `prompt_tokens` LM Studio reports, which converges in one or two
   measurements.

2. Prefix caching would falsify the result. llama.cpp-family runtimes reuse the
   attention cache for an unchanged prompt prefix, so a repeated measurement
   would report an impossible prefill speed. Every prompt therefore opens with a
   unique nonce, which invalidates the cache from the first token.
"""
import os
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(ROOT, "corpora", "agentic-ai-oct2026.full.md")

# Measured against the gemma4 and qwen35 tokenisers on this corpus (4.45-4.86).
# Only the first size of a model uses it; after that the ratio is learned from
# the exact prompt_tokens LM Studio reports.
DEFAULT_CHARS_PER_TOKEN = 4.6

INSTRUCTION_HEAD = (
    "Benchmark request {nonce}\n\n"
    "Summarise the report below.\n\n"
    "<document>\n"
)
INSTRUCTION_TAIL = (
    "\n</document>\n\n"
    "Write a concise summary of the document above in about 150 words, covering "
    "its main findings and conclusions.\n"
)
WRAP_DIVIDER = "\n\n<!-- reference material continues from the top -->\n\n"

_master_cache = None


def load_master():
    global _master_cache
    if _master_cache is None:
        if not os.path.isfile(MASTER):
            raise FileNotFoundError(
                "Corpus missing: %s\nRun `python tools/build_corpus.py` first." % MASTER)
        with open(MASTER, "r", encoding="utf-8") as fh:
            _master_cache = fh.read()
    return _master_cache


def master_stats():
    text = load_master()
    return {"chars": len(text),
            "approx_tokens": int(len(text) / DEFAULT_CHARS_PER_TOKEN)}


def _slice(text, budget_chars):
    """Cut near the budget, preferring a paragraph then a word boundary."""
    if budget_chars >= len(text):
        return text
    if budget_chars <= 0:
        return ""
    cut = text.rfind("\n\n", 0, budget_chars)
    if cut < budget_chars * 0.9:
        cut = text.rfind(" ", 0, budget_chars)
    return text[:max(cut, 1)]


def build_prompt(target_tokens, chars_per_token=DEFAULT_CHARS_PER_TOKEN, nonce=None):
    """Assemble a summarisation prompt aiming at `target_tokens` prompt tokens."""
    nonce = nonce or uuid.uuid4().hex[:12]
    head = INSTRUCTION_HEAD.format(nonce=nonce)
    overhead_tokens = (len(head) + len(INSTRUCTION_TAIL)) / chars_per_token
    body_chars = int(max(0, target_tokens - overhead_tokens) * chars_per_token)

    master = load_master()
    wrapped = 0
    if body_chars <= len(master):
        body = _slice(master, body_chars)
    else:
        # Beyond the corpus length, cycle it with a visible divider. Prefill cost
        # does not depend on content, and the repetition is recorded in the result.
        chunks = []
        remaining = body_chars
        while remaining > 0:
            take = min(remaining, len(master))
            chunks.append(_slice(master, take) if take < len(master) else master)
            remaining -= take
            if remaining > 0:
                chunks.append(WRAP_DIVIDER)
                remaining -= len(WRAP_DIVIDER)
                wrapped += 1
        body = "".join(chunks)

    prompt = head + body + INSTRUCTION_TAIL
    return prompt, {
        "nonce": nonce,
        "target_tokens": target_tokens,
        "chars": len(prompt),
        "chars_per_token_used": chars_per_token,
        "corpus_wraps": wrapped,
    }


def refine_chars_per_token(prompt_chars, actual_prompt_tokens, previous):
    """Learn the real ratio from a completed measurement.

    Blended with the previous estimate so one odd measurement cannot throw the
    remaining sizes off; the ratio is stable enough that this converges fast.
    """
    if not actual_prompt_tokens or actual_prompt_tokens <= 0 or not prompt_chars:
        return previous
    observed = prompt_chars / float(actual_prompt_tokens)
    if not (1.5 <= observed <= 12.0):      # implausible: keep what we had
        return previous
    return round(0.35 * previous + 0.65 * observed, 4)
