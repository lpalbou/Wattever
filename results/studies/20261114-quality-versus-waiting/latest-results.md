## Four-model quality versus waiting time

Updated 2026-11-14T17:21:28+00:00. **Paused at the user's request; saved attempts and completed downloads are retained.** No overall winner is established.

The primary question is which model supplies correct, complete answers quickly on this machine. The common frozen set has 32 ARC-Challenge, 32 GSM8K and 64 balanced MMLU-Pro questions, plus 16 logical/structured questions and 8 executable coding questions. Public128 and practical24 scores remain separate. Each condition has one attempt per question, a 2,048-token total generation cap, greedy sampling and 32,768 reserved context. Downloads must be complete or freshly acknowledged paused for timed requests.

| Exact candidate | Requested mode | Completed public questions | Public strict correct | Practical progress / correct | Condition status |
|---|---|---:|---:|---|---|
| qwen3.8-flash-next-iq3_s | none | pending | pending | pending | not measured |
| qwen3.8-flash-next-iq3_s | low | pending | pending | pending | not measured |
| qwen3.8-flash-next-unsloth-ud-iq4_xs | none | pending | pending | pending | not measured |
| qwen3.8-flash-next-unsloth-ud-iq4_xs | low | pending | pending | pending | not measured |
| glm-5.3-flash-maya-xs-iq1_s | none | 106/128 | 81 | 8/24 attempted; 6 correct | stopped_after_request |
| glm-5.3-flash-maya-xs-iq1_s | low | 0/128 | pending | 8/24 attempted; 8 correct | functionality_screen_saved |
| glm-5.3-flash-maya-s24-iq2_xxs_s | none | pending | pending | pending | not measured |
| glm-5.3-flash-maya-s24-iq2_xxs_s | low | pending | pending | pending | not measured |

An 8-question screen is functionality evidence only. Its attempts are retained when the rest of the same condition resumes; questions are not silently rerun. Previous XS public128 results collected with a background download are retained below as a separate accuracy cohort and are excluded from controlled answer-time comparisons.

### Saved results at pause

The in-flight request completed and was saved before stopping. The benchmark client exited. Control closed its experimental panel and unloaded the model; the ownership registry is empty and port 8097 is closed. No other model was restored. The guarded experimental profile and exact configuration snapshots remain available for a future explicit resume. No inference or download will restart automatically.

| XS / 2,048 total tokens / 32,768 context | Strict correct | Content correct | Correct completed within 30 / 60 / 120 s | Completed-answer median [range], s | p95, s | Truncated |
|---|---:|---:|---|---|---:|---:|
| none; public128; partial | 81/106 | 91/106 | 80 / 81 / 81 of 106 | 4.45 [1.98-34.82] | 9.62 | 0 |
| none; practical24; partial | 6/8 | 6/8 | 6 / 6 / 6 of 8 | 4.88 [3.37-8.29] | 8.29 | 0 |
| low; practical24; partial | 8/8 | 8/8 | 8 / 8 / 8 of 8 | 5.73 [4.12-8.14] | 8.14 | 0 |

The XS Off public subset is 106/128 questions: ARC32 and GSM32 are complete, MMLU-Pro is 42/64. Strict/content scores are ARC 25/32 and 30/32; GSM 30/32 and 30/32; partial MMLU-Pro 26/42 and 31/42. Ten correct-content public answers failed the strict requested format. These partial results cannot be ranked against the earlier completed Low public128 cohort.

S24 preparation is complete: three independently readback-verified GGUF shards, 94,701,676,224 bytes (88.20 GiB), and the official Maya 1.1.59 BF16-compatible pack with F32 expert-selection bias preserved. It has not been inference-benchmarked. All download/conversion workers have exited; a cooperative download pause marker is retained. No files were discarded.

### Observable timing and reasoning behavior

Record first output of any kind, first visible final-channel text, and terminal completed-answer arrival separately. Visible analysis is not proof that a useful answer has arrived. The 30/60/120-second metric requires a correct completed answer by that deadline; truncated/incomplete responses never count. Slow tails, reasoning/output counts, repetition and category accuracy remain in each per-question record.

Offline Chat/Responses rendering parity passed 12 cases across the pinned frontends and both Qwen templates. Live XS probes confirmed Off produces 0 provider reasoning tokens and Low can produce positive counts. Responses exposes the provider reasoning counter that Chat Completions omits. Greedy sampling is explicit; these Responses implementations do not expose a seed setting, and bitwise hardware determinism is not claimed.

| Family/runtime | Thinking off | Enabled Low | Effective budget difference at 2,048 total tokens |
|---|---|---|---|
| Qwen / Strata server 0.1.53 | enable_thinking=false; template writes an empty thinking block | low prompt instruction | Default answer_room closes thinking near 1,536 tokens to leave answer room; verify active config |
| GLM / Maya 1.1.59 reviewed native variant | enable_thinking=false; frontend closes the templateâ€™s thinking prefix | Low effort header | Current thinking_budget=0 allows reasoning to consume the total cap; this is not the same reasoning partition as Qwen |

The common total output budget is identical; the backend closing policies are explicitly different. If truncation limits a conclusion, use a separately labelled complete MMLU-Pro64 cohort at 4,096 tokens rather than replacing individual failed answers.

### Coding-grader review

The functionality screen caught two over-restrictive grader rules. Basic type-name lookup and equivalent ordered Python interval pairs are now accepted. Exact scalar values, explicit list-container contracts, input immutability, memory/time limits and forbidden I/O/import checks remain enforced. Earlier grades and source versions are retained; saved answers were regraded without repeating inference. All candidates use the corrected policy.

### Scope and current storage qualification

GPU inventory/placement and RAM budgets remain Auto. The GLM 750 MiB reserve is a scoped measured tuning candidate, not a production default. Requested configuration, native applied information and actual per-GPU observations are retained separately. Long-context comparisons at approximately 20k/80k/120k will grade retrieval, combining distant evidence, contradictions and missing evidence separately from short reasoning. Expensive S24/190k grids are deferred until quality justifies them.

The existing 500,000-reservation XS probe is saved: 489,998 actual prompt tokens, all five records correct, natural stop, 338 output tokens, 487.04 s wall for one measured request with downloading active. It demonstrates capacity, not a three-repeat throughput result or a practical latency recommendation.

A read-only check found D: is exFAT with 1 MiB allocation units and Windows reports Full Repair Needed. The physical 990 PRO reports Healthy/OK. Detailed reliability/dirty-bit queries were denied. No repair, reformat, partition or OS change was performed. This observation is not proof of an SSD speed bottleneck. S24 checksums, independent readback verification and conversion are complete; inference remains untested.

[Frozen protocol, question-level results and source/grade history](../results/studies/20261114-quality-versus-waiting/README.md). [Upgrade investigation](local-inference-upgrades.md) prioritizes unproven native-Linux feasibility and fully costed used RAM platforms; no extra SSD or expensive GPU is justified by assumption.
