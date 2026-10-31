"""Offline, checksum-pinned capability datasets; no runtime downloads."""
import hashlib
import json
from pathlib import Path

from . import capabilities

DATA = Path(__file__).resolve().parents[1] / "corpora" / "capabilities"
GSM_SHA256 = "3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14"
ARC_SHA256 = "9fa8ffb3e3a1f88cb302d53dd95aae81e39209ad9bcf974376b319d8fd35717c"
GSM_REVISION = "3101c7d5072418e28b9008a6636bde82a006892c"


def _rows(filename, digest, count):
    raw = (DATA / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Bundled %s failed its dataset checksum." % filename)
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != count: raise ValueError("Dataset item count does not match pinned split.")
    return rows


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
