# GLM-5.3-Flash optimization study: 2026-11-14

The [completed tuning tables](../../../docs/glm53-flash-comparison.md#driver-aware-ram-planning-and-completed-tuning-results)
and [latest-results.md](latest-results.md) include the driver-aware native
variant, 131,072-context measurements through 120k actual input tokens, read-piece
screens, full native/client phase timings and correctness grades. Each condition
has its own raw JSON, request logs and paging samples. The 128-token output-cap
diagnostic remains a failed warm-up capture; its results are not merged with the
completed cap-512 matrix.

`v1.1.59-wddm-cap-buffered-direct.*` identifies the native build/patch.
`control-sources/` preserves content-addressed Python sources;
`validation-20261114.json` records 538 passing tests and 35 passing subtests.
`public-quality-plan.json` is an offline selection plan, not an accuracy result.
`online-glm-comparison.json` and `upgrades-research.json` retain research evidence.
The checksum manifest includes nested prompt and source files.

Read the [comparison report](../../../docs/glm53-flash-comparison.md#samsung-990-pro-glm-baseline-2026-11-14)
for the completed baseline table, settings and limitations. This is a preliminary
study on two RTX 5070 Ti cards, i7-5960X, 64 GiB RAM and Samsung 990 Pro storage.
It is separate from the 2026-11-06 AirDisk study and the Flash RAM experiments.

- `xs1156-ctx98-ram40-pin1024-ch4-measured.json` is the complete nine-request
  Maya-XS/1.1.56 speed baseline: three repeats at 512/5k/20k, one excluded warmup
  per target and no functional probes mixed into measured statistics.
- `summary.json` gives the native/client median/range table and grouped telemetry.
- Matching `*-measured-*.log` and `*-warmup-*.log` files preserve each request's
  native logs. `*-paging.jsonl` retains sampled RAM, paging and physical disk data.
  The raw JSON contains per-GPU request summaries and timing provenance.
- `xs1156-ctx98-ram40-pin1024-ch4-v2.json` and its three functional logs preserve
  the explicit multiply/ascending-sort/code-output probes: two pass, code output
  is40 rather than30. This is not another speed repeat.
- `prompts.json` retains the frozen speed prompt identities, official chat-token
  counts, expected five-record values and tokenizer/template hashes. `prompts/`
  contains the frozen source text files. Prompt digests use UTF-8 text with LF
  newlines as submitted by the client; the manifest separately hashes stored bytes.
- [manifest.json](manifest.json) records source-copy SHA-256 values and byte sizes.
  The raw capture's absolute paths identify its recorded setup, not portable
  reproduction instructions.

`xs1156-ctx98-quality-v2.json`, `quality-tasks-v2.json` and sixteen matching
`*-quality-v2-*.log` files preserve the completed exact-answer screen: 8/16 exact
passes, reasoning none, 512-token cap, one attempt per task. Its prompts,
expected values, strict JSON types and format-only classification are retained.
One response exhausts its output cap. This is a distinct quality cohort.
Later candidate runtime/quantization/allocation/I/O cohorts remain separate;
missing results are not speed or quality claims. Both clients are HTTP-only and
require a model explicitly loaded in Strata Control.

## Matched patched-1.1.59 buffered-I/O pair

`xs1159patch-io0-ctx98-ram26-pin1024-ch4-split25.json` and the corresponding
`io1` JSON retain nine completed requests each, one excluded warmup per target,
identical frozen prompt bytes and actual input counts, and45/45 correct ledger
records per condition. Their matching request logs and paging JSONL files are
separate cohorts. `buffered-io-summary.json` retains the full timing ranges,
physical D: reads, resident/standby cache, Maya RSS and per-GPU observations.
`memory_telemetry.py` is the exact local sampler source for these cache counters;
`prompt-renderers.json` records the effective template/frontend/tokenizer
identity and actual counts without changing historical prompt manifests.

`software-provenance/v1.1.59-buffered-io.json` and its patch identify the native
binary shared by both conditions. A startup-failure JSON and the retained startup
log cover unsuccessful40GiB allocation attempts; they are not inference results.
The successfully measured pair requests26GiB (13.29+12.70GiB allocated), with
context98,304 and split25. It is not a pure runtime-only comparison against
1.1.56/RAM40. Available Windows RAM includes reclaimable standby cache.

## Mmap / native Auto / sequential-load screen

`xs1159-mmap-auto-serial-room2048-ctx98.json` contains all six measured5k/20k
requests (three per target), two excluded warmups and30/30 correct ledger records.
Its matching native logs and paging JSONL retain the variable decode and output
stall. `xs1159-mmap-auto-room2048-serial-rerun-load.json`, matching startup log and
load-paging JSONL retain the valid122.920s startup,20.28+0.18GiB pinned experts,
99.6% startup peak RAM and0.508s global heavy-page-out interval. Measured-request
pressure/stalls are reported separately from startup.

`xs1159-mmap-auto-room2048-serial-load.json` is a separately excluded,
monitor-interrupted trial with no observed native fatal error. It must not be
counted as an OOM, capacity result or inference repeat. The source/build receipt
and patch under `software-provenance/v1.1.59-io-modes-warm-checks.*` identify the
874d0e24 native build. This is not the same binary/allocation treatment as the
preceding buffered-I/O pair. No recommendation follows from reduced physical
reads or high RAM use alone; the measured decode variability and5.505s output gap
are retained in the comparison.

## Publication note

Before publication, the Windows account name in recorded file paths was replaced with `user` and a private
network address with `192.168.x.x`. No measurement was altered. The SHA-256 values of the edited files, and every
reference to them in this folder and its manifest, were recomputed afterwards.
