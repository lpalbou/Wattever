# Capability evaluation

New question subsets use versioned SHA256 ranking of the selection seed and stable item IDs, independent of Python's sampling implementation. Historical Python-sampling records remain readable. Each run also records the exact selected IDs and their digest.

Use **Capabilities** to compare rule-scored task accuracy separately from throughput and power. Connect an endpoint in **Run**, then choose a model, suite, question count, seed, repeats and generation budget. Its display name and opaque route identity are supplied automatically. Launching tests and validating capability imports require the local Python helper. The hosted site can browse, export and compare shared capability results; it does not launch capability inference or import unvalidated capability files.

## Bundled suites

| Suite ID | Questions | Evaluation |
| --- | ---: | --- |
| `wattever-core-v1` | 24 | Original arithmetic, logic, typed JSON, instruction, retrieval and code-output tasks; four per category |
| `gsm8k-test-chat-v1` | 1,319 | GSM8K test split; zero-shot chat with final numeric-answer matching |
| `arc-challenge-test-chat-v1` | 1,172 | ARC-Challenge test split; zero-shot chat with generated answer-letter matching |
| `mmlu-pro-test-chat-v1` | 12,032 | MMLU-Pro test split, 14 subjects and up to ten choices; zero-shot generated letters |
| `mmlu-pro-balanced64-chat-v1` | 64 | Fixed category-balanced MMLU-Pro subset, four or five questions per subject; zero-shot generated letters |

The external test splits are bundled for offline use. GSM8K is pinned to upstream revision `3101c7d5072418e28b9008a6636bde82a006892c` under MIT. ARC uses the `ARC-V1-Feb2018` archive's Challenge test member under CC BY-SA 4.0. The loader checks each extracted file's exact SHA-256 and item count before registering a suite. Source links, hashes, archive member, attribution and licenses are in [dataset provenance](../corpora/capabilities/README.md). The archive ETag records source provenance; it is not the extracted file's cryptographic checksum.

MMLU-Pro is pinned to dataset revision `b189ec765aa7ed75c8acfea42df31fdae71f97be`, whose dataset card declares MIT. Its bundled JSONL preserves question text, options, keys and subjects while excluding gold chain-of-thought explanations. The separate balanced 64 suite selects questions by stable ID and subject using seed `73129`; no answer or model result determines selection. It defaults to **Full suite**, which tests all 64 questions. Its subject weighting differs from the full 12,032-question test split. Subsampling that 64-question suite again does not preserve its guaranteed four-to-five questions per subject.

Both MMLU-Pro suites use zero-shot chat and generated final answer letters. They are separate from the official five-shot chain-of-thought protocol and its leaderboard scores. Full suites can be expensive: imports and suite registration allow at most 20,000 questions, with existing limits of 200,000 characters per prompt and 20 million total prompt characters.

**AI2 ARC-Challenge** contains grade-school multiple-choice science questions. It is distinct from the [ARC-AGI grid-reasoning benchmark](https://arcprize.org/arc-agi/1). The dashboard identifies its task type explicitly. A high score on this science-question suite is not an ARC-AGI score or a general intelligence rating. See the [AI2 dataset description](https://huggingface.co/datasets/allenai/ai2_arc).

These are versioned Wattever chat adaptations. GSM8K uses a zero-shot final `#### <number>` answer rather than a published few-shot harness. ARC normalizes option labels to letters and grades generated `ANSWER: <letter>` output rather than option likelihoods. Different prompts, sampling, item selections, output budgets and graders can change results. Do not equate these scores with official leaderboard or other harness scores. Public tasks may have appeared in training; none of these suites measures IQ or establishes a general intelligence ranking. Core is a small functional probe with a possible ceiling effect.

## Run and compare

1. Start the inference engine and load the intended model. Connect it in **Run**; no manual endpoint ID or label is needed. Use an optional canonical model ID if its served alias is ambiguous. An advanced **Display name** override can clarify an ambiguous detected name; keep it public and free of connection URLs or credentials.
2. Record quantization, model revision, engine/version, context/KV settings, reported reasoning and MTP where known. Configure actual settings in the engine. An omitted or unknown field does not mean disabled.
3. Open **Capabilities**, select the model and suite, and choose a subset or **Full suite**. The seed selects a deterministic subset; its exact IDs and digest are recorded. Use the same selection and evaluation settings across comparisons. Full suites can take substantial time and provider usage.
4. Review total generation tokens, optional deadline and repeats. Capability generation settings are independent of the speed benchmark. The default budget is 32,768 tokens for thinking plus final output, with no wall-clock deadline (`0`). This is a ceiling, not a request to generate that many tokens. The server's loaded context and provider limits can impose lower limits. Use Stop to cancel, or set an explicit item deadline when needed. The UI defaults requested reasoning to **None**; **Provider default** omits that API field. Unsupported reasoning requests fail visibly. A short total budget can truncate reasoning before a final answer appears. Inspect the observed reasoning, token usage, output-limit count and coverage; a requested reasoning setting does not prove the engine honored it. Keep budgets consistent across comparable configurations and report deliberate budget changes as separate evaluations.
5. Start the test. One request runs at a time, with a fresh chat per item/repeat. No model load/unload or MTP command is sent. Local CPU/RAM/GPU telemetry is available only after explicitly confirming inference runs on this computer, including when using an LM Studio endpoint.
6. Open a saved result to explore its dashboard. Select category bars, outcomes or repeats to filter the question matrix, then select a cell to inspect the prompt, expected output and visible response. Select multiple results for configuration comparisons. Different evaluation keys indicate different experiments.

Requested sampling is recorded, including temperature, optional top-p and reasoning effort. The seed always determines question selection. **Also send seed to the inference API** is off by default; with `send_seed: true`, it additionally requests `seed + repeat` for generation. Top-p defaults to omitted, and a blank temperature omits that parameter. The endpoint's determinism and effective settings are not independently verified. Repeats expose response variation on the same questions and do not create independent new questions. A provider can change its served weights or routing without changing its public alias; record any known changes as a new configuration.

## Explore a result

The overview keeps score and coverage together. Its header shows selected questions versus total suite questions, repeat count, and the descriptive first-repeat 95% interval when recorded. Subset scores are labeled as subsets; a 50-question sample is not the full 1,172-question ARC-Challenge test split. Small samples and scores near the ceiling can limit the ability to distinguish configurations; inspect the questions, coverage and paired evidence rather than comparing headline percentages alone.

The dashboard includes:

| View | What it shows |
| --- | --- |
| **Strict rule pass rate** | Passed attempts divided by graded attempts; request errors are excluded |
| **Correct / planned** | Passed attempts divided by every planned attempt, including unfinished work |
| **Evaluation coverage** | Graded attempts divided by planned attempts |
| **Issues to review** | Rule mismatches, output limits and request errors, with distinct outcomes |
| **Capability profile** | Category pass rates with passed/graded and graded/planned counts |
| **Outcome breakdown** | A ring and legend separating passes, rule mismatches, output limits, request errors, cancellation, ungraded and unrecorded attempts |
| **Consistency across repeats** | Per-repeat pass rates and questions whose recorded pass/fail outcome changed |

Select a chart segment, category bar or repeat point to filter the **Question explorer**. Combine category, outcome, repeat and text filters; **Clear filters** restores the full selection. The matrix places questions in rows and repeats in columns, with 12 questions per page. Symbols and outcome labels accompany colors. Unrecorded cells are visible rather than counted as failures.

Select a cell to open its evidence inspector. The question appears above expected and actual output, followed by a human-readable explanation of the recorded rule. Copy controls and previous/next navigation help review matching attempts. **Grading evidence & request metadata** contains the raw grading, normalization, usage and timing fields in a collapsed panel. Model responses remain inert text; inspecting them executes no generated code.

An exact-rule mismatch can be a format failure. For example, if the task requires only `528`, the response `The answer is 528.` fails the exact-text rule despite containing the expected number. Inspect the required output and grading explanation before concluding that the model failed the arithmetic. The dashboard presents the recorded decision; it does not relax the rule or add a model judge.

Completed answers are saved atomically. Transient permission failures during file replacement use bounded retries without repeating model requests. A persistent save failure stops the run; completed, missing and failed observations remain distinct.

Live results update as attempts finish while preserving question filters and input focus. You can inspect another saved result without being redirected to the active run. Older files that contain only aggregate scores are labeled **Reported summary only**; question evidence is not reconstructed from their totals.

For API clients, fetch the full run and its suite manifest once through `/api/capabilities/runs/<id>`, then consume `/api/capabilities/events?cursor=<cursor>`. Apply result updates by item ID and repeat, retain the returned cursor, and refresh the final saved result after completion. This avoids repeatedly transferring the full question manifest. See the [API reference](api.md) for request and event routes.

## Compare configurations

Select results in the library and choose a baseline. Overall and category bars show recorded pass rates with their graded denominators. A paired change is available only when evaluation fingerprints and declared suite, scoring, selected questions, sampling and answer-budget settings match. Otherwise the bars remain descriptive and the dashboard marks the paired result **Not comparable**.

The paired percentage-point change is the compared result minus the baseline, using only matching item-ID/repeat pairs graded in both runs. Wins, losses and ties describe those same scored pairs. Errors, cancelled requests, ungraded records and missing observations do not become losses. Different repeat counts can leave unmatched observations outside the paired calculation. This paired change can differ from subtracting overall pass rates when the runs have different graded coverage.

Repeat variation means that scored pass/fail flags changed for the same question. A request error followed by a pass is an availability difference, not evidence of answer variation. The dashboard introduces no new scoring protocol or independence assumption; use the statistical context below when interpreting gaps.

## Grading and uncertainty

Scorers are deterministic and auditable. Exact text ignores surrounding whitespace. Typed JSON rejects duplicate keys, non-finite values, extra keys and value/type mismatches. Numeric final answers use decimal comparison with validated number formatting. Multiple-choice scoring checks the final answer label. No model judge or generated-code execution is used.

Only visible final-answer content is scored; streamed reasoning is not treated as the final answer. A successfully completed stream retains its finish reason, usage and observation metadata even when no final answer appears. Empty final output at `finish_reason: "length"` is an output-limit failure; empty final output at normal completion is a failed task answer. Neither is a transport error. `finish_reason: "length"` is scored incorrect even when a partial answer resembles the expected result. Transport errors and cancellation are unscored and counted separately. A configured positive request deadline stops the evaluation; the helper cannot verify provider-side cancellation. It blocks another benchmark while its request worker remains active. Inspect the engine before restarting.

`accuracy` and `percent` divide correct answers by scored observations, excluding unscored errors. Always inspect `planned`, `attempts`, `scored`, `errors`, `cancelled` and `truncated`. `penalized_accuracy` divides correct answers by all planned observations; unfinished work reduces it. Category and per-repeat statistics accompany the total.

The 95% Wilson interval uses distinct scored items from the first repeat only. It is a descriptive binomial approximation: questions may be correlated and are not a representative sample of intelligence. Repeated responses are dependent observations. An interval is not a significance test between models, and overlapping or non-overlapping intervals alone do not establish a ranking. Compare matching suite/subset, scoring version, token budget and sampling settings, with coverage and failure rates visible.

## Configuration identity and result files

New evaluations record completion policy `finished-stream-visible-answer-v2` in their evaluation fingerprint. Historical no-final-answer error records can lack finish reasons and usage; their cause cannot be reconstructed from the stored record. They remain unchanged and carry an interpretation notice in the dashboard. Rerun with adequate generation settings for a complete evaluation.

Each result contains `model_configuration` and its SHA-256 `configuration_id`. The public configuration records requested/canonical model identifiers, public endpoint ID/label/provider, quantization, engine/version, model revision/source, context/KV settings, and requested/reported/effective reasoning and MTP. Unknown values stay explicit. Effective settings require control-plane evidence; accepting a request is not that evidence. Endpoint URLs and credentials do not enter this public fingerprint.

Connect or a direct start supplies endpoint identity automatically. Naming provenance distinguishes detected service metadata, inference from an official cloud host/declared managed provider, an optional operator display-name override and unknown software. Ports and model aliases are not software evidence. An unknown provider keeps a generic location-based name rather than a guessed engine. Canonical known public API routes use stable public provider IDs; other routes use privately generated opaque IDs with URL mappings kept in the ignored helper registry or browser local storage. Route origin and provider branding do not verify inference location or backend weights, and historical user-reported identities remain readable. See [automatic endpoint identity](api.md#automatic-endpoint-identity).

The suite has a version and content digest. `evaluation_key` fingerprints the evaluation protocol and selected items; repeat count is recorded separately. Fingerprints establish content consistency, not authentication, proof that inference happened, or proof of which weights an endpoint served.

Capability files use `benchmark_kind: "capability"`, `schema_version: 1`, and a separate `results/capabilities/<id>.json` directory. They include the public suite, item answers, scorer decisions, usage/timing provenance and summary. Throughput files remain in `results/runs/` with their existing schema. See [API](api.md) for read/start/cancel/import/export routes.

Import accepts one capability run or an object containing `capability_runs`. It validates public configuration/suite/protocol fingerprints, deterministic item selection and observation uniqueness, then independently re-scores visible audit answers and recomputes statistics. A completed result requires all planned observations. A response redacted for privacy cannot be independently re-scored and is rejected as a full audit import. Preserve the reviewed original locally if you need that audit.

HTTP import bodies are limited to 32 MiB, independently of the question-count limit. Large full-suite results can exceed that size. You can validate an individual result file with `python tools/validate_results.py <file.json>` and contribute it as a file using the [sharing workflow](sharing-results.md); running or exporting a local suite does not require re-importing it.

Public suite prompts, expected answers and source citations must remain exact for their digest and audit. Review them, generated answers and all annotations before sharing. Connection credentials and URLs are excluded as connection metadata; arbitrary task text can still contain information you do not want to publish. See [sharing results](sharing-results.md) and [security](../SECURITY.md).
