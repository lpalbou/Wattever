"""Offline, checksum-pinned capability datasets; no runtime downloads."""
import hashlib
import json
from pathlib import Path

from . import capabilities
from .identity import canonical_digest

DATA = Path(__file__).resolve().parents[1] / "corpora" / "capabilities"
GSM_SHA256 = "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14"
ARC_SHA256 = "9fa8ffb3e3a1f88cb302d53dd95aae81e39209ad9bcf974376b319d8fd35717c"
GSM_REVISION = "3101c7d5072418e28b9008a6636bde82a006892c"
MMLU_SHA256 = "df41a2413bb510d4d25834e760ac744866bf6e190c9e89d8df008f778bc67a3d"
MMLU_REVISION = "b189ec765aa7ed75c8acfea42df31fdae71f97be"
MMLU_PARQUET_SHA256 = "0e24a191921c2f453518a537a8b2117bd137e7714d4ef1565e9ba06c1ecb9ad8"
MMLU_BALANCED_SEED = 73129


def _rows(filename, digest, count):
    raw = (DATA / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Bundled %s failed its dataset checksum." % filename)
    # JSONL records use LF. Unicode separators inside valid JSON strings are
    # content, rather than extra records as str.splitlines() would treat them.
    rows = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if len(rows) != count: raise ValueError("Dataset item count does not match pinned split.")
    return rows


def _balanced_subset(tasks, count=64, seed=MMLU_BALANCED_SEED):
    """Versioned category round-robin; independent of source row ordering."""
    if not 0 < count <= len(tasks):
        raise ValueError("Invalid balanced subset size.")
    groups = {}
    for task in tasks:
        groups.setdefault(task["category"], []).append(task)
    categories = sorted(groups, key=lambda category: (
        canonical_digest({"seed": seed, "category": category}), category))
    for category in categories:
        groups[category].sort(key=lambda task: (
            canonical_digest({"seed": seed, "item_id": task["id"]}), task["id"]))
    selected = []
    position = 0
    while len(selected) < count:
        for category in categories:
            if position < len(groups[category]):
                selected.append(groups[category][position])
                if len(selected) == count:
                    break
        position += 1
    return sorted(selected, key=lambda task: task["id"])


def _register_mmlu():
    tasks = []
    rows = _rows("mmlu-pro-test.jsonl", MMLU_SHA256, 12032)
    seen = set()
    for row in rows:
        question_id, options, index = row["question_id"], row["options"], row["answer_index"]
        if (type(question_id) is not int or question_id in seen or type(index) is not int
                or not isinstance(options, list) or not 2 <= len(options) <= 10
                or any(not isinstance(option, str) or not option for option in options)
                or not 0 <= index < len(options) or row["answer"] != chr(65 + index)):
            raise ValueError("Invalid pinned MMLU-Pro question or answer mapping.")
        seen.add(question_id)
        labels = [chr(65 + i) for i in range(len(options))]
        prompt = row["question"] + "\n\n" + "\n".join(
            "%s. %s" % pair for pair in zip(labels, options))
        prompt += "\n\nChoose the best answer. End your reply with exactly ANSWER: <letter>."
        tasks.append({"id": "mmlu-pro-test-%05d" % question_id,
                      "category": row["category"].replace(" ", "_"), "prompt": prompt,
                      "scorer": {"kind": "multiple_choice", "expected": row["answer"],
                                 "choices": labels}})
    source = {"kind": "bundled_dataset", "repository": "https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro",
              "revision": MMLU_REVISION, "dataset": "MMLU-Pro", "split": "test", "license": "MIT",
              "sha256": MMLU_SHA256, "upstream_parquet_sha256": MMLU_PARQUET_SHA256,
              "normalization": "Question/options/answer/category/src only; gold cot_content excluded; upstream question IDs retained."}
    full = capabilities.register_suite({"id": "mmlu-pro-test-chat-v1", "name": "MMLU-Pro · test",
        "version": "1.0.0", "description": "12,032 pinned MMLU-Pro test questions across 14 subjects, with up to ten choices. Zero-shot generated-letter chat adaptation; not the official five-shot CoT leaderboard protocol.",
        "source": source, "scoring_version": capabilities.SCORING_VERSION, "tasks": tasks})
    balanced = _balanced_subset(tasks)
    subset_source = dict(source, parent_suite_digest=full["digest"], source_items=12032,
        subset_algorithm="category-round-robin-sha256-v1", subset_seed=MMLU_BALANCED_SEED,
        selected_ids_sha256=canonical_digest([task["id"] for task in balanced]))
    capabilities.register_suite({"id": "mmlu-pro-balanced64-chat-v1", "name": "MMLU-Pro · balanced 64",
        "version": "1.0.0", "description": "64 fixed MMLU-Pro test questions, 4–5 per subject across all 14 subjects. Category-balanced subset, not the full population-weighted benchmark. Zero-shot chat, up to ten options, generated-letter scoring.",
        "source": subset_source, "scoring_version": capabilities.SCORING_VERSION, "tasks": balanced})


def register_bundled_suites():
    """Register zero-shot chat adaptations, explicitly distinct from leaderboards."""
    gsm = []
    for index, row in enumerate(_rows("gsm8k-test.jsonl", GSM_SHA256, 1319)):
        expected = row["answer"].rsplit("####", 1)[1].strip()
        gsm.append({"id":"gsm8k-test-%04d" % index, "category":"math_word_problems",
                    "prompt":row["question"] + "\n\nSolve the problem. End your answer with a final line in exactly this format: #### <number>.",
                    "scorer":{"kind":"numeric_final", "expected":expected}})
    capabilities.register_suite({"id":"gsm8k-test-chat-v1", "name":"GSM8K · test", "version":"1.0.0",
        "description":"1,319 pinned GSM8K test questions; zero-shot chat with final numeric answer scoring. This adaptation is not the official few-shot leaderboard protocol.",
        "source":{"kind":"bundled_dataset", "repository":"https://github.com/openai/grade-school-math",
                  "revision":GSM_REVISION, "dataset":"GSM8K", "split":"test", "license":"MIT", "sha256":GSM_SHA256},
        "scoring_version":capabilities.SCORING_VERSION, "tasks":gsm})
    arc = []
    for index, row in enumerate(_rows("arc-challenge-test.jsonl", ARC_SHA256, 1172)):
        choices = row["question"]["choices"]
        labels = [item["label"] for item in choices]
        expected = chr(65 + labels.index(row["answerKey"]))
        options = "\n".join("%s. %s" % (chr(65+i), c["text"]) for i,c in enumerate(choices))
        arc.append({"id":"arc-test-%04d" % index, "category":"science_reasoning",
                    "prompt":row["question"]["stem"] + "\n\n" + options + "\n\nChoose the best answer. End your reply with exactly ANSWER: <letter>.",
                    "scorer":{"kind":"multiple_choice", "expected":expected,
                              "choices":[chr(65+i) for i in range(len(choices))]}})
    capabilities.register_suite({"id":"arc-challenge-test-chat-v1", "name":"ARC-Challenge · test", "version":"1.0.0",
        "description":"1,172 pinned ARC-Challenge test questions; zero-shot chat and generated answer-letter accuracy. This is not a likelihood-based leaderboard evaluation. Choice labels are deterministically normalized to letters.",
        "source":{"kind":"bundled_dataset", "repository":"https://huggingface.co/datasets/allenai/ai2_arc",
                  "revision":"ARC-V1-Feb2018", "dataset":"ARC-Challenge", "split":"test", "license":"CC-BY-SA-4.0", "sha256":ARC_SHA256,
                  "archive":"https://ai2-public-datasets.s3.amazonaws.com/arc/ARC-V1-Feb2018.zip"},
        "scoring_version":capabilities.SCORING_VERSION, "tasks":arc})
    _register_mmlu()
