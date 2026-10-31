# Capability datasets

Wattever Core v1 is original task material under the repository's MIT license.
The two external test splits below are bundled for offline use, byte-for-byte.
No training data or external inference service is downloaded at runtime.

| File | Upstream | License | Items |
| --- | --- | --- | --- |
| `gsm8k-test.jsonl` | [OpenAI GSM8K](https://github.com/openai/grade-school-math/tree/3101c7d5072418e28b9008a6636bde82a006892c) | [MIT](GSM8K-LICENSE) | 1,319 |
| `arc-challenge-test.jsonl` | [Ai2 ARC](https://huggingface.co/datasets/allenai/ai2_arc), ARC-V1-Feb2018 archive | [CC BY-SA 4.0](ARC-CC-BY-SA-4.0.txt) | 1,172 |

ARC credits: Peter Clark, Isaac Cowhey, Oren Etzioni, Tushar Khot, Ashish Sabharwal,
Carissa Schoenick, and Oyvind Tafjord, *Think you have Solved Question Answering?
Try ARC, the AI2 Reasoning Challenge* (2018). The questions remain under CC BY-SA
4.0; the application's MIT license does not replace their license. The ARC chat
prompt adaptations retain that license and normalize choice labels to letters.

Source revisions, exact file SHA256 digests, archive member and observed archive
ETag are recorded in [provenance.json](provenance.json). The loader checks file
checksums and item counts before registering a suite. The archive's ETag is
provenance, not a cryptographic hash of the extracted file.

GSM8K is evaluated with zero-shot chat and final numeric-answer matching. ARC is
evaluated with generated answer letters, not choice likelihoods. These are
versioned Wattever chat adaptations; scores are not interchangeable with a
published leaderboard or oMLX result that uses different prompts, samples,
token budgets or grading rules. See [capability evaluation](../../docs/capabilities.md).
