---
name: wattever
description: Operate Wattever throughput and rule-scored capability benchmarks against streaming LLM endpoints. Use for managed LM Studio or generic endpoint runs, pinned GSM8K/ARC/MMLU-Pro task evaluation, public model identities, live telemetry, comparisons, audit imports and shareable JSON through the local HTTP API.
license: MIT
compatibility: Python 3.9+ and local Wattever server; streaming Chat Completions endpoint or managed LM Studio; Windows, Linux, or macOS.
metadata:
  package-version: "2.5.3"
---

# Wattever

## Purpose and limits

Use Wattever to benchmark managed LM Studio or generic OpenAI-compatible streaming Chat Completions across models, prompt lengths and simultaneous queries. Optional `/models` discovery can be replaced by manual served IDs. Other OpenAI API surfaces are not benchmarked. Configure generic endpoint placement, context/KV cache, quantization, reasoning and MTP in its inference engine. GPU sensor selection does not control placement. GPU and system estimates are not wall-meter readings. Compare matching settings, builds, corpus, timing sources and protocol. Hosted browser mode uses a separate client-timed protocol.

Optional `endpoint_reasoning_effort` requests the standard API field (`none`, `minimal`, `low`, `medium`, `high`, `xhigh`). Throughput API default null omits it; UI and capability API default to `none`. Provider support varies and effective behavior remains unverified. Short budgets can end during hidden reasoning before a visible answer. Never infer token counts from empty stream chunks.

## Install and verify

Download and extract `https://github.com/lpalbou/Wattever/releases/download/v2.5.3/Wattever-2.5.3.zip`, or clone `https://github.com/lpalbou/Wattever.git`, and install Python 3.9+. Use the documentation and skill matching that version. Start an inference engine and load its model. For managed LM Studio only, download models and run `lms bootstrap`. Launch `Wattever.bat` on Windows, `Wattever.command` on macOS, or `sh Wattever.sh` on Linux, or run `python -m wattever.server` from the extracted folder or repository. Keep the server open. Starting can reuse an existing instance and replace an idle older instance; do not restart it during an active benchmark.

Check the printed URL with this read-only probe. Replace the URL with the printed address; the server may select a different port. Require a build ID plus endpoint/capability feature flags. The installed surface's documentation wins when it differs from the skill. Restart the complete helper and reload the page after code updates; an identified idle build can be replaced by the launcher, while active speed/capability work is preserved. Do not stop the inference engine merely to refresh Wattever.

```python
import json
import urllib.error
import urllib.request

base = "http://127.0.0.1:8713"  # Replace with the printed local URL.
try:
    with urllib.request.urlopen(base + "/api/version", timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("application") != "Wattever" or not data.get("version") or not data.get("build_id"):
        raise ValueError("Not a Wattever version response")
    print("Wattever", data["version"], "build", data["build_id"])
    with urllib.request.urlopen(base + "/api/env", timeout=15) as response:
        environment = json.load(response)
    if "openai" not in environment.get("capabilities", {}).get("providers", []):
        raise ValueError("This helper does not expose the generic endpoint provider")
    if not environment.get("capabilities", {}).get("capability_benchmarks"):
        raise ValueError("This helper does not expose capability evaluation")
except (urllib.error.URLError, OSError, ValueError) as exc:
    print("Wattever unavailable or invalid response:", exc)
    raise SystemExit(1)
```

## Mental model

- One benchmark file contains cases (model, Standard/MTP mode, prompt size, concurrency, repeat) and execution sessions. Live results appear as cases finish.
- Defaults: one query, one repeat and 256 output tokens. Managed LM Studio uses per-test context, 512 headroom and 5% prompt margin; shared KV capacity scales with concurrency. Fixed context uses the largest allocation per model/mode.
- Managed MTP is off by default and needs a supported bundled head. Warmup is on; managed placement prefers GPU with fallback. Load and test timeouts default to 900 and 1800 seconds. Generic technique states remain unknown; the engine controls them and no fallback reload occurs.
- Strata remains externally managed. With Strata Control, load or switch its model in that application; API benchmark requests never start or restore a Strata model. Supported GPU/KV modes depend on its model backend, so detected cards or accepted flags do not verify execution placement.
- For external speed runs, `requested_mtp: "on"`, `"off"` or `"unknown"` records intent only. Require `/api/version` feature `external_requested_mtp_metadata` before relying on its retention. It never configures the engine or becomes an inference parameter; effective MTP and observed draft counters need separate evidence.
- Errors return JSON `error` with HTTP 400 for invalid inputs or conflicts, 404 for missing runs, 409 for active-run edits or deletion, and 500 for unexpected failures. Handle connection and JSON errors too.
- For owned-run cancellation, require `/api/version` feature `atomic_expected_run_id_cancel` and send `{"expected_run_id":"<owned-run-id>"}` to the speed or capability cancel route. A different active ID returns 409 without cancellation; invalid IDs return 400. Omitting the field cancels without an ownership check. Cancellation does not prove provider-side termination.
- Speed runs default to fresh transfer probes. Use `hardware_probe_policy: "defer"` to keep fresh RAM/storage/CUDA microbenchmarks outside a loaded-model experiment: only a verified matching saved profile may be reused, with its original timestamp; otherwise evidence is deferred. Capability runs always separate these probes from accuracy collection. This does not unload or reconfigure an engine.

## Capability map

- Hosted browser mode: open `https://lpalbou.github.io/Wattever/`, connect explicitly to an OpenAI-compatible endpoint, and use browser storage and JSON import/export. It requires CORS and may require browser local-network permission. No helper install is needed. The optional key is memory-only; do not put it in conversation or result files.
- The hosted Results catalog is read-only and needs no provider connection. Hiding a shared entry is browser-local. To contribute, submit its JSON in a GitHub pull request; the hosted site does not write to GitHub.
- Read version, environment, models, status, events and runs via GET endpoints in the API reference. `GET /api/bench/extend-plan?run_id=<id>` returns extension options.
- In a source checkout or the hosted Results view, `?compare=<id>,<id>#results` opens two to eight existing benchmarks with shared model IDs and separate prefill/decode charts. It runs no inference. A link containing unknown IDs is ignored. Review model/quant, engine, reasoning, MTP, actual counts and timing provenance before interpreting the paired curves; shared IDs do not authenticate identical settings. Partial experiment metadata can identify unrecorded cases even when the completed source sessions have finished.
- Start/cancel through `/api/bench/*`. Managed LM Studio supports resume. The helper supports same-file extension for managed and reconnected external runs: add missing, retry failed/skipped or rerun selected cases. External extensions require fresh matching connection fields and remain scoped to the selected engine.
- Edit metadata, delete to local trash, export JSON/CSV, start or unload LM Studio, and stop Wattever through the local API. Results and snapshots are written under `results/`.
- GPU sensor selection does not control LM Studio placement. Local NVIDIA telemetry records each detected card and simultaneous combined power, energy, VRAM and utilization, with explicit completeness and sampling coverage. Missing card measurements make combined energy unavailable. Hardware discovery and power availability are best effort.
- Memory topology is collected by built-in OS/firmware probes without additional installs. `POST /api/hardware/memory` refreshes it; optional `run_id` attaches a backed-up later annotation only on matching GPU/driver, CPU, motherboard and RAM settings. Distinguish populated channels inferred from explicit firmware labels from independently detected active controller mode. Unknown topology stays unknown; never infer it from DIMM count or bandwidth. `GET /api/env` exposes matching-GPU configuration reminders in `hardware_follow_ups`.
- Helper benchmarks automatically embed storage-to-RAM and pinned RAM-to-VRAM transfer probes. `POST /api/hardware/bandwidth` runs them independently; optional `gpu_index` and private `storage_probe_directory` choose the GPU sensor and an existing test folder. Add `run_id` for a backed-up later annotation on matching hardware/GPU/driver; its timestamp does not establish historical conditions. Browser-only mode cannot run native probes.
- `POST /api/bench/link` with `run_ids` and a public `label` groups stopped runs on matching hardware/GPU/driver; generic endpoints require explicit local inference attribution. Put the root run first and include all desired members. Results can open the whole group for comparison/export while retaining per-run engine identities and settings.
- To produce one machine benchmark entry/file, use `POST /api/bench/consolidate` with `run_ids` (destination first) and `label`. Compatible sources retain exact measurement rows, configuration fingerprints, sessions and source metadata; other source files are backed up and archived to trash. Conflicting known manufacturer/model/nominal-memory board multisets reject consolidation even for the same GPU chip; sensor order and provenance text are ignored. Matching strong identities tolerate variant-label aliases; otherwise conflicting known variants reject while legacy unknown stays compatible. `provider: mixed` describes the aggregate only; execution groups and session settings identify actual engines. Each repair stays scoped to its engine and selected models. Publish the consolidated file and remove superseded source entries from the catalog.
- Publish every file listed in a machine group's `comparison_group.run_ids`, including its root, to make the whole comparison available in the hosted catalog. Public labels and optional `tags` identify the engine, model, quantization and techniques; inspect runtime observations and notes for provider queueing or ignored flags. Grouping does not merge distinct engine protocols into one run.
- Browser mode has manual hardware descriptions and no power telemetry, model load/unload, MTP allocation, resume or same-file extension. It can request standard reasoning effort but cannot verify effective behavior. Provider settings govern execution/context. Closing the page stops its run; download in-memory results if storage fails.
- Capability API `/api/capabilities/*`: list suites/runs/status/events, start/cancel sequential rule-scored tests, import/re-score audit files, export public JSON. Helper tests use separate `results/capabilities/` storage. Hosted mode supports their shared library/export/comparison but cannot launch tests or validate capability imports. Only one speed/capability run executes at a time.

## Workflow

1. Inspect `/api/version`, `/api/status` and `/api/env`. Choose managed LM Studio or a generic endpoint. For generic discovery, POST connection fields to `/api/endpoint/check`; discovery failure permits manual served IDs. Do not mistake the helper's default LM Studio `/api/models` list for generic discovery.
2. Choose the requested models, sizes, concurrency and placement. Starting a run can take a long time, load and unload models and write files. Never interrupt an active run for inspection.
3. Use the Run page or `POST /api/bench/start` with the chosen configuration. Poll `/api/events?cursor=0`, then pass back the returned cursor, or read `/api/status`. Read the saved run by ID after completion and report failures and missing power honestly.
4. For same-file continuation in the local helper, use **Add / rerun tests** or obtain extension options and submit a plan with `run_id`, `model_modes` (IDs mapped to arrays of `off`/`on`), `sizes`, `concurrency_levels`, and optional boolean `retry_failed` or `rerun_existing`. Review plan counts before `POST /api/bench/extend`. Replaced cases move to `previous_attempts`; an exact snapshot is kept in `results/.history/`. External endpoints require a fresh `connection` object on options/preview/start and only `off` scheduling modes; MTP stays provider controlled. Use `POST /api/bench/extend-options` for their model catalog and `engine_group_id`.
5. Compare only compatible results. Extension and resume check hardware, GPU/driver, corpus and LM Studio version. Review a result before sharing it.

## Task accuracy workflow

Capability generation controls are separate from speed tests. Default to a 32,768-token total thinking-and-answer budget and no wall-clock deadline (`timeout_s: 0`); Stop still cancels. Observe loaded-context/provider limits and truncation before interpreting quality. A completed but empty final answer is a task/output-limit failure, not a transport error. Preserve historical audits; never invent lost finish reasons or silently retry failed items with a larger budget under the same evaluation identity.

Connect in Run first; endpoint display name and opaque route ID are supplied automatically. Do not ask the user to invent an ID or label. Use `canonical_model_id` when a served alias is ambiguous. Record known quantization, engine/version, model revision, actual context/KV and reported MTP/reasoning. Public `model_configuration` distinguishes requested/reported/effective/unknown states, and `configuration_id` checks consistency, not weight authenticity or proof of execution.

List `GET /api/capabilities/suites` and check its running `runner.completion_policy`, defaults and limits. An absent or unsupported policy requires relaunching the helper before starting evaluations or validating imports; browser refresh alone does not reload Python. Then POST a configuration such as this to `/api/capabilities/start` only when authorized to run inference:

```json
{"provider":"openai","endpoint_base_url":"http://127.0.0.1:8000/v1","model":"<served-model-id>","suite_id":"gsm8k-test-chat-v1","sample_size":50,"repeats":1,"seed":0,"temperature":0,"top_p":1,"endpoint_reasoning_effort":"none","max_tokens":32768,"timeout_s":0,"endpoint_inference_local":false}
```

Use `wattever-core-v1` for 24 original functional tasks, `gsm8k-test-chat-v1` for 1,319 test math questions, or `arc-challenge-test-chat-v1` for 1,172 test science questions. ARC is AI2 school-science multiple choice, distinct from ARC-AGI grids. `mmlu-pro-test-chat-v1` contains 12,032 questions across 14 subjects; `mmlu-pro-balanced64-chat-v1` fixes four or five questions per subject using seed 73129. Use `sample_size: 0` for all 64 balanced questions; a smaller second subset loses that balance. These zero-shot chat protocols differ from official leaderboard evaluation, including MMLU-Pro's five-shot CoT protocol. Report exact subset size, weighting and first-repeat interval rather than interpreting a small high-scoring sample as a general intelligence score. Dataset files/counts are checksum pinned and loaded offline; GSM8K/MMLU-Pro are MIT, ARC/its chat adaptation is CC BY-SA 4.0. Suite registration/import allows at most 20,000 tasks. Full suites can take substantial time/provider usage. The default requested generation budget is 32,768 tokens shared by reasoning and final output; inspect server context limits and compare identical declared budgets. `token_limit_field` can explicitly select `max_completion_tokens` if required by the endpoint.

Poll `/api/capabilities/status` or `/api/capabilities/events?cursor=0`; fetch `/api/capabilities/runs/<id>` when done. Each item gets a fresh sequential chat; no model lifecycle or MTP control is performed for either provider. Seed determines item selection; API seed is omitted unless `send_seed: true`, which requests seed+repeat with determinism unverified. Top-p defaults omitted; null temperature/top-p omit their fields. Local telemetry requires an explicit true assertion for all capability providers. Timeout/cancel stops subsequent tasks and blocks other work while a request worker remains active; inspect the endpoint rather than assuming cancellation succeeded.

Grade visible answers only with deterministic exact/typed-JSON/numeric-final/choice rules. No generated code or model judge runs. Length truncation is incorrect; transport errors and cancellation are unscored. Successful completions without a final answer are failed task answers; terminal length means an output-limit failure. Report correct/scored with planned/attempted/errors/truncations; `penalized_accuracy` is correct/planned. Wilson 95% intervals use distinct first-repeat items, not pooled repeats. Correlated public tasks are not representative intelligence samples; repeated responses are dependent. These zero-shot chat adaptations are not official few-shot or likelihood leaderboard protocols.

Use the visual dashboard to review strict rule pass rate, correct/planned, evaluation coverage and issues together. Category bars, outcome-ring segments and repeat points filter the question explorer. Combine category/outcome/repeat/text filters; the question-by-repeat matrix paginates 12 questions per page. Select a cell to read the prompt, expected output, actual visible answer and rule explanation. Copy or navigate matching attempts; raw grading/usage/timing stay under **Grading evidence & request metadata**. Inspect an exact-text failure before reporting a reasoning failure: `The answer is 528.` contains the expected number but fails a task requiring only `528`. Errors and unrecorded attempts are not wrong answers, and an error followed by a pass does not demonstrate answer variance.

Compare matching `evaluation_key` values and declared suite/scoring/subset/sampling/budget settings, then review configuration and coverage. The paired percentage-point change is compared result minus baseline, over matching item-ID/repeat pairs scored in both runs. Exclude errors and ungraded records from paired wins/losses; unmatched repeats stay outside that denominator. Different protocols can be viewed descriptively but must not receive a fair paired-delta claim. Use recorded first-repeat intervals only; the dashboard changes no scoring rules or independence assumptions.

For a live API dashboard, fetch `/api/capabilities/runs/<id>` once to obtain the suite manifest and existing results, then apply `/api/capabilities/events?cursor=<cursor>` result events by item ID and repeat. Retain the returned cursor; fetch the final saved run after completion. Preserve the user's selected result, question filters and focused input while applying updates. Aggregate-only historical files have no reconstructed item audit.

Export `/api/capabilities/runs/<id>/download` or `/export.json?ids=<id>`. Import POST `/api/capabilities/import` accepts one run or `{"capability_runs":[...]}`; it re-scores full audit answers and checks identities/suite/protocol/subsets. Redacted answers cannot be imported as independently scored audits. Review prompts, expected answers, sources and generated text before sharing. Fingerprints do not authenticate the operator or actual model.

Live telemetry describes whole-client load and each detected NVIDIA GPU, including competing processes. CPU/RAM sampling continues without GPU sensors; missing CPU temperatures are normal. Saved history is a 3,600-point recent ring, about nine minutes at 150 ms, with explicit dropped counts. Whole-run observed means/peaks/coverage remain preserved. Early/late clock/temperature drift is an observation, not proof of throttling or sustained model speed. Charts separate metrics with different units.

## Rules and sharp edges

Independent hardware follow-ups use `POST /api/hardware/memory` and `/api/hardware/bandwidth`, with `run_id` to attach later evidence after hardware matching and backup. Inference rows and session snapshots stay unchanged. A page-sized usable-RAM variation after reboot can be accepted only with matching installed memory, clocks, CPU/board/OS and GPU/driver inventory; `hardware_match` records that exception. Never rewrite historical conditions from a later measurement. See the [API](../../docs/api.md) and [5070 Ti example](../../docs/5070-ti-hardware-follow-up.md).

For generic helper use, send this JSON to `/api/endpoint/check` to discover without inference; then add `models`, `sizes`, `concurrency_levels`, `max_tokens` and `repeats` and POST to `/api/bench/start` when authorized to benchmark:

```json
{"provider":"openai","endpoint_base_url":"http://127.0.0.1:8000/v1","endpoint_inference_local":false}
```

A root URL gets `/v1`; an explicit API base path stays unchanged. Never supply an individual route, embedded credentials, query or fragment. Optional `endpoint_api_key_env` is only an environment-variable name; have the user set its secret outside conversation before starting the helper. Missing variables fail validation. `endpoint_inference_local` defaults false even for loopback; set true only with evidence/authorization that inference executes on the client. Only then is client power attributed to inference.

Connect and direct starts resolve endpoint display name/opaque ID automatically. Recognized official cloud hosts and explicit service metadata provide evidence; bounded local/private health/props/version GETs never use ports or model aliases to guess software. Unknown software uses Local/Private/Remote endpoint. Naming provenance is `detected`, `inferred`, `operator_override` or `unknown`; route location does not prove inference location. Optional `endpoint_name_override` changes one public display name; legacy explicit `endpoint_id`/`endpoint_label` remain accepted, and historical `user_reported` fingerprints remain supported. Never require users to populate two naming fields.

Canonical known public API routes use stable public provider IDs for aggregation. Other routes, including custom cloud paths, map operational base URLs to random IDs in the ignored private helper registry `results/.endpoint-routes.json` or browser local storage. URLs are not hashed into public IDs or fingerprints. Do not export/share that registry; clearing it/storage can regenerate its IDs. Provider branding does not verify backend software/weights. Optional names must not include connection URLs, addresses or credentials.

Public outputs omit endpoint URLs, key variable names and keys. Review free-text labels/notes/model names/sources before sharing. Hardware inventory describes the client and carries additive `metadata_schema_version: 1` within compatible benchmark schema 2. Optional `reproducibility` records user-reported engine/version, model revision, quantization, actual context/KV settings and draft count; it does not configure the engine. Optional `performance_claims` require source, arithmetic precision, execution path, sparsity and peak/measured/estimate kind. Use TOPS for INT4/INT8 and TFLOP/s for floating precision. Never infer compute precision from weight quantization or invent a peak from a GPU name.

Client `prefill_tps` is prompt tokens/TTFT including overhead and unobserved reasoning, not isolated engine compute. Keep `reported_timings` separate. Inspect `timing_scope` and `decode_token_count`: unstreamed reported reasoning is excluded from decode, and ambiguous hidden reasoning can leave decode unavailable. Missing `cached_prompt_tokens` means unknown cache usage. Generic missing token usage means unavailable throughput; chunks are not tokens. PCIe encoding ceilings are theoretical one-direction limits, not measured DMA or memory bandwidth; link snapshots can downshift at idle.

Do not start, cancel, unload models, delete runs, or shut down a server without authorization for that action. A benchmark is resource intensive; managed operation modifies LM Studio state and remote inference may incur provider charges. Inspect the saved power scope: historical measurements can cover one selected card, while all-card captures include idle cards too. Integrate overlapping concurrent requests once over their shared measurement window. Missing power remains unavailable. Model execution may use other GPUs or CPU; record the actual setup. Failed and skipped cases are results, not zero performance. An extended legacy benchmark can contain different planner versions; inspect each session.

Managed helper recovery verifies model unload and request worker exit before continuing. Generic helper and browser timeouts stop the run and cannot verify provider-side cancellation or unload; check the engine before restarting. Outstanding throughput workers keep `/api/status` `running` true even after the run records an error; capability workers keep its `capability_running` true. Live status identifies the wait and new benchmarks remain blocked until workers exit; poll both busy states, not only the saved run status. These guards clear automatically without killing the external engine.

## When things fail

- No models: for managed LM Studio check downloads and `LMS_BASE_URL`; for generic endpoints check API base/authentication or enter served IDs manually.
- CLI missing: run `lms bootstrap` and relaunch.
- Power unavailable: check `nvidia-smi`; on macOS optional `powermetrics` requires authorization. Speed can still be measured.
- Context/load failure: reduce size/concurrency or adjust the external engine's allocation. Missing generic throughput requires streamed server usage, not chunk counting.
- Extension compatibility error: start a separate benchmark when hardware, driver, corpus or LM Studio version differs or cannot be verified.

## Go deeper

- [Getting started](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/getting-started.md): setup and first run.
- [Browser mode](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/browser.md): hosted setup and limitations.
- [Sharing results](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/sharing-results.md): contributions and continuing helper benchmarks.
- [Measurements](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/measurements.md): metric boundaries and session semantics.
- [Multi-GPU engineering study](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/dual-gpu-communication-and-placement.md): separate transfer, placement and profiling protocols, source manifests and measurement limits. Study tools have their own dependencies and do not enter normal benchmark aggregates.
- [Capability evaluation](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/capabilities.md): datasets, grading, public identity, uncertainty and audit files.
- [API](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/api.md): routes and side effects.
- [Troubleshooting](https://raw.githubusercontent.com/lpalbou/Wattever/main/docs/troubleshooting.md): symptoms and remedies.
- [Full documentation](https://raw.githubusercontent.com/lpalbou/Wattever/main/llms-full.txt): complete context.

## Repair and extend managed benchmarks

Preview with `POST /api/bench/extend-plan` before executing `POST /api/bench/extend`. Include `run_id`, `model_modes`, `sizes`, `concurrency_levels` and the desired total `repeats` (at least the saved count, at most 100). Add missing slots by default; use `retry_failed: true` to retry failed/skipped cases while retaining successful ones. For one exact replacement, pass `selected_cases: [[model_id, false, concurrency, target_tokens, zero_based_repeat]]` and `rerun_existing: true`. Optional `per_test_timeout_s` (1..7200) changes only the new execution session's budget. Selection stays session-local; backups and previous attempts preserve history. External repairs include fresh endpoint `connection` and the saved `engine_group_id`. These calls require authorization to run inference. To remove a model, use `POST /api/runs/<id>/models/delete` with `models: [id]`; a backup is kept and other models remain. Use whole-benchmark deletion for the last model.

Set `fallback_on_failure: false` to keep the requested placement after load failures. Opt-in `skip_after_timeout: true` skips remaining repeats and larger contexts after a timeout; describe that as a practical budget boundary, not a proven OOM. Managed requests honor the configured timeout. Timeouts and transient request errors do not imply a capacity limit or suppress later repeats. Quantization format and method are separate: retain `quantization_method` and its provenance alongside `quantization`; never infer QAT/PTQ from bit width. Catalog backfills describe current labels and do not verify historical weights.

For machine characterization, `POST /api/hardware/bandwidth` measures native single/parallel RAM copy payload rates as well as disk reads and pinned H2D transfers. Supply `storage_probe_directories` with up to eight existing folders to compare volumes. Interpret RAM copy rates as payload bytes, not measured DRAM bus traffic. Each additional volume retains independent raw samples; paths stay private. Run these probes while inference is idle.

Storage comparisons retain frozen `storage_comparison.baseline_results` and `alternate_results` in the same machine benchmark. Primary `results` remain the card comparison matrix. Summary reports primary coverage and per-volume transfer measurements. Table and Compare share recorded-condition, model, metric and query-count filters. Alternatives are listed only when measured inference rows exist; inventory/probe presence never creates model observations. **Compare recorded storage** selects the actual experiment conditions/models and opens Compare. Table's CSV follows that selection; the Summary experiment also exports the complete paired dataset. Repeats and per-query evidence remain in All measurements. Reported MTP is not proof of effective execution; missing storage labels or measurements must not be invented.

When a fresh storage baseline differs from historical rows or sessions, keep it as a separate recorded condition and pair NVMe with that actual baseline. Do not relabel the older primary measurements. Verify effective GPU/host expert placement before comparing drives; identical requested budgets can produce different placement. Preserve timeouts and retry sessions. A loaded MTP block does not establish drafting: native offered/accepted counters can show zero attempts because of backend sampling restrictions. Keep that evidence separate from requested/reported settings, and treat penalty-neutral probes as a different sampling condition. See the [5070 Ti experiment](../../docs/5070-ti-hardware-follow-up.md) and its derived CSV.

Run shows CPU/GPU/RAM/per-card VRAM in its collapsed header and visual inventory cards when expanded. `/api/env` may restore a compatible prior transfer profile after restart; `source: saved_capture` and `saved_capture` identify the source run, original time and matching fields. This is not a new measurement and writes nothing. CPU/RAM/GPU/driver/reported-clock mismatches prevent reuse. Chart condition controls expose matching-case counts; the legend includes plotted points/failures only. Single-model chart readouts show actual medians and explicitly matched deltas, with no delta for an unpaired timeout/success.

The [5070 Ti plus 5060 Ti experiment](../../docs/5070-plus-5060-comparison.md) demonstrates separate capacity, throughput and combined-energy comparisons. Keep connection-profile latency and native server prompt timing separate; a post-run diagnostic is not a correction to archived TTFT.
