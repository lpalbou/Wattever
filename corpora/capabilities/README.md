# Capability datasets

Wattever Core v1 is original task material under the repository's MIT license.
External test splits are bundled for offline use. GSM8K and ARC retain their
upstream JSONL bytes; MMLU-Pro retains selected columns in normalized JSONL.
No training data or external inference service is downloaded at runtime.

| File | Upstream | License | Items |
| --- | --- | --- | --- |
| `gsm8k-test.jsonl` | [OpenAI GSM8K](https://github.com/openai/grade-school-math/tree/3101c7d5072418e28b9008a6636bde82a006892c) | [MIT](GSM8K-LICENSE) | 1,319 |
| `arc-challenge-test.jsonl` | [Ai2 ARC](https://huggingface.co/datasets/allenai/ai2_arc), ARC-V1-Feb2018 archive | [CC BY-SA 4.0](ARC-CC-BY-SA-4.0.txt) | 1,172 |
| `mmlu-pro-test.jsonl` | [TIGER-Lab MMLU-Pro](https://huggingface.co/datasets/TIGER-Lab/MMLU-Pro/tree/b189ec765aa7ed75c8acfea42df31fdae71f97be) | [MIT](MMLU-Pro-LICENSE) | 12,032 |

ARC credits: Peter Clark, Isaac Cowhey, Oren Etzioni, Tushar Khot, Ashish Sabharwal,
Carissa Schoenick, and Oyvind Tafjord, *Think you have Solved Question Answering?
Try ARC, the AI2 Reasoning Challenge* (2018). The questions remain under CC BY-SA
4.0; the application's MIT license does not replace their license. The ARC chat
prompt adaptations retain that license and normalize choice labels to letters.

MMLU-Pro credits: Yubo Wang and colleagues, [*MMLU-Pro: A More Robust and
Challenging Multi-Task Language Understanding Benchmark*](https://arxiv.org/abs/2406.01574)
(2024), TIGER-Lab. Its pinned dataset card declares MIT; the upstream evaluation
code has its own license. The JSONL normalization preserves question IDs,
question text, options, answer keys, categories and source labels, sorted by
question ID. Gold chain-of-thought explanations are excluded. Reproduction uses
`tools/build_mmlu_pro.py` with the pinned Parquet source and a preparation-only
DuckDB installation; running Wattever requires neither Parquet nor DuckDB.

The full MMLU-Pro suite contains all 12,032 test questions. Its separate balanced
64 suite selects four or five questions from each of 14 subjects using
`category-round-robin-sha256-v1`, seed `73129`, independently of answers. Choose
**Full suite** to evaluate all 64 selected questions. This category-balanced
sample has a different subject weighting from the full test split.

Source revisions, exact file SHA256 digests, archive member and observed archive
ETag are recorded in [provenance.json](provenance.json). The loader checks file
checksums and item counts before registering a suite. The archive's ETag is
provenance, not a cryptographic hash of the extracted file.

GSM8K is evaluated with zero-shot chat and final numeric-answer matching. ARC is
evaluated with generated answer letters, not choice likelihoods. MMLU-Pro uses
zero-shot generated letters, rather than the official five-shot CoT protocol. These are
versioned Wattever chat adaptations; scores are not interchangeable with a
published leaderboard or oMLX result that uses different prompts, samples,
token budgets or grading rules. See [capability evaluation](../../docs/capabilities.md).
