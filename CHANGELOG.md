# Changelog

## Unreleased

- Fixes the benchmark library in Results shrinking to a few rows when no benchmark is selected. It now keeps at least the height of the window, so the recorded benchmarks stay browsable.
- Adds **Rename** to every benchmark in Results: on its library card, beside its name when several are compared, and in its heading. One dialog edits the name and the description, including while the benchmark is still running. It replaces the two browser prompts that only an opened, finished benchmark offered.
- Describes the machine on each library card (graphics cards, their memory and RAM) in place of the "N tests passed" badge, which repeated the test count on the line above; failed, skipped and cancelled counts remain. An endpoint benchmark saved as remote that was reached at a local address now shows the inventory of the computer that ran it, marked **Host not confirmed**, in the library and in Overview, and is listed with that computer's other benchmarks. Any other endpoint stays **Hardware not recorded**. Overview says "24 tests passed" once when every test passed.
- Adds an optional **Enable CPU temperature** button to System on Windows when no CPU temperature is readable. After one administrator approval it installs the signed PawnIO driver (2.2.0) and a read-only Wattever task that reads only the CPU package thermal registers (Intel `IA32_PACKAGE_THERM_STATUS`, AMD Tctl); readings carry `cpu_temp_source` `windows_wattever_pawnio:intel_package` or `:amd_tctl`. Both downloads are pinned by SHA-256 and checked again in the administrator-only install folder before anything runs. **Remove CPU sensor** deletes the task and its files and uninstalls PawnIO only if Wattever installed it. `GET`/`POST /api/system/cpu-sensor` report and start the setup.
- Adds the [vLLM on two RTX 5070 Ti cards](docs/vllm-two-5070-ti.md) study with two Wattever runs of NVIDIA's NVFP4 Qwen3.8-27B served by vLLM, a long-prompt sweep, a prefix-reuse probe and per-batch energy against LM Studio.
- Reads CPU temperature on Windows from a sensor tool that is already running: HWiNFO (Shared Memory Support) or LibreHardwareMonitor (web server) for the CPU package, otherwise Core Temp (hottest core) or a live ACPI thermal zone, each named in `cpu_temp_source` and in the System view. Nothing is installed or elevated, and no value is estimated: a thermal zone that never changes is treated as a placeholder. When no source exists, `cpu_temp_note` and the System view say why, instead of an empty CPU temperature line. Previously Windows never reported CPU temperature.
- Reworks the **Results this run** table in Benchmarks: the title shows the average batch wall time, prefill time and speed, and decode time and speed over the tests that completed, each in the colour that underlines its column; the header stays in place while the rows scroll inside the table; the Status column is removed and a failed test is drawn in red with its reason. The explanation of decode timing moves under the table.
- Moves the MTP speculative decoding choice (**Off**, **On**, **Compare both**) out of the collapsed Advanced settings into the Workload card of Benchmarks. The choice can always be made, and the benchmark resolves whether a model can be launched with MTP: a model LM Studio loads is tried and its MTP pass skipped when it has no MTP head. An OpenAI-compatible endpoint decides MTP itself, so **Compare both** runs its standard pass and records that the MTP pass was not run, and **On** alone says why nothing can run instead of starting. Previously the choice disappeared for endpoints.
- Recognizes LM Studio on this computer when it is entered as an OpenAI-compatible endpoint (`http://localhost:1234/v1`, the computer's own network address or name) and connects it as **LM Studio (managed)**, so context, GPU placement and MTP can be set and models show their quantization. The Endpoint card says so and offers **Use as a plain endpoint instead**. That choice is remembered for the address until LM Studio (managed) is connected by hand. An address with an API key stays an endpoint. `POST /api/endpoint/check` reports this as `helper_lmstudio`.
- Keeps the chosen prompt lengths, simultaneous queries, repeats, answer length and MTP mode when you click **Connect**. Previously connecting reset them.
- Reopens Benchmarks on LM Studio when LM Studio was the last connection made in this browser. Previously the page reopened on the endpoint of the last benchmark, even when that endpoint was no longer running.
- Records System telemetry continuously while the helper runs, whether or not System is open, and keeps a rolling last-24-hours log on this computer in the gitignored `results/.telemetry/` (one file per hour, finished hours compressed, older ones deleted). Opening System shows what happened while nobody watched; windows extend to 24 hours, **Earlier**/**Later** and **Go to time** reach any moment in the last day, and long windows average readings while the readout keeps the peak. Axes, the readout and the status line show local clock times, with the date when it is not today, instead of "s ago" or "s old". The monitor no longer stops after 15 seconds without a viewer; during a benchmark it reads the benchmark's sampler instead of running a second sensor stream. New route: `/api/system/history`.
- Accepts an endpoint API key typed directly into the helper's Endpoint card, replacing the environment-variable name. The key stays in memory, is sent only to that endpoint, and is never written to benchmark files, the saved form, exports or recorded errors. The API field is `endpoint_api_key`; `endpoint_api_key_env` remains for scripts.
- Merges benchmarks of one machine from Results: tick them and choose **Merge into one…**, or follow the **Same machine** suggestion. The helper recognizes the machine, including for endpoint benchmarks saved as remote, names every reason a benchmark cannot join, and asks for confirmation before attributing an endpoint benchmark to the machine that launched it. Benchmarks of one engine can now be merged, a test measured more than once keeps its completed, most recent measurement with the others archived, and **Undo merge** restores the original files exactly. New routes: `/api/bench/merge-plan`, `/api/bench/merge-suggestions` and `/api/bench/merge-undo`.
- Keeps an endpoint benchmark running when the endpoint cannot serve one of its models. After a failed request the helper checks the endpoint: a model it refuses is recorded as `model_unavailable`, its remaining tests are skipped and the other models still run. Only an endpoint that stops responding, rejects the key or cannot be checked stops the run, with the unmeasured tests left to continue. Previously any failed request ended the whole benchmark.
- Fixes **Connect** with Provider set to LM Studio reporting "LM Studio has no models indexed" when the last benchmark used an OpenAI-compatible endpoint that is no longer running. The helper listed that endpoint's models instead of LM Studio's; Connect now asks for LM Studio's catalog with `/api/env?provider=lmstudio`.
- Fixes **Add / rerun tests** on endpoint benchmarks saved as remote, which were always refused for a differing GPU. A stopped benchmark offers **Continue this benchmark…** under its progress bar.
- Folds notices in Results and under the progress bar into one slim strip, collapsed by default, that says what needs attention and unfolds to the details. A test that did not pass shows its reason in the live results table, and chart tooltips no longer cut benchmark names.
- Boxes similar models together in the Results model list: the same model under different publishers, quantizations or engines, then one family in several sizes. A box header shows or hides all of its models, and **only** selects just that box.
- Replaces the Recorded conditions panel with switches in the chart key: click a benchmark there to hide or show its curves. Entries that are on carry a faint wash of their own colour; all are on by default.
- Organizes the interface into System, Benchmarks, Results, Capabilities and Share, with independent local system monitoring.
- Replaces the Results Summary with an Overview of each model's peak prefill and decode speed, its change across prompt length and concurrent queries, and the longest prompt completed. Any figure opens its graph.
- Rebuilds Graphs & comparisons around a toggle-button control panel with no menus: several metrics at once, prompt length or concurrent queries on the axis, several query counts or prompt lengths as separate lines, model toggles, and colour by model or benchmark. Charts gain a compare-at-pointer tooltip and a shared key that highlights its lines.
- Stops failed or skipped tests from appearing as separate recorded conditions, and names a condition as a variant only when another one measured the same model. Provenance labels and a session's test plan no longer split one model into several configurations.
- Accounts for every passed test in Graphs: a banner states how many the current toggles draw and offers one-click remedies for the rest, each chart names the values it could not draw and why, and untouched defaults choose query counts, metrics and colours that show everything while the chart stays readable. Library cards list the model count.
- Reports complete concurrent-batch prefill and output rates using recorded batch timing, while preserving per-request evidence and unavailable timing.
- Documents the Samsung 990 PRO GLM-5.3-Flash tuning campaign with separate native/client phase timings, memory/cache telemetry, long retrieval, frozen accuracy screens and ranked upgrade options; the higher-quant comparison remains in progress.

## 2.6.1 - 2026-11-06

- Publishes the NVMe-backed GLM-5.3-Flash Maya-XS six-context baseline, runtime/pack/RAM/concurrency/read-chunk screens, per-request evidence, eligible both-card energy and separate metric graphs alongside retained Flash-Next observations.
- Consolidates the complete GLM baseline into the existing two-5070 machine entry while keeping tuning diagnostics separate. Capability scores and the selected updated-profile full matrix remain unmeasured.
- Validates immutable study artifacts, actual token counts and complete-group accounting across platforms.

## 2.6.0 - 2026-11-06

- Redesigns concurrency comparisons around request-count charts, separate group/per-request throughput, prefill and latency views, measured energy, a model-by-concurrency map, request timing bars and CSV slice exports. Prompt-length charts remain available.
- Separates recorded GPU-offload conditions and excludes incomplete request groups from successful scaling. Scaling pairs matching successful repeats; missing baselines and low-confidence energy remain unavailable.
- Publishes 19 valid prefill/concurrency diagnostic conditions on two RTX 5070 Ti cards, with raw request/power evidence, independent-instance comparisons, Q8-cache capacity and optional fair-scheduler patches. These studies remain separate from normal machine benchmark aggregates.
- Adds an experiment checkpoint linking the completed dual-5070 performance, communication and Flash-Next quality studies, with GLM preparation clearly separated from measured results.

## 2.5.5 - 2026-11-04

- Speeds up privacy filtering for large benchmark exports by reusing field-name classifications while continuing to check every value.
- Publishes 192 Flash-Next IQ3_S accuracy observations alongside the UD-IQ4_XS series, with matched question-level comparisons, complete answer audits and explicit sample-size limits.

## 2.5.4 - 2026-11-04

- Preserves ordinary phrases such as "basic math" in capability audit answers while continuing to redact encoded HTTP Basic credentials and explicit authentication headers. Exports retain independently scorable answers.
- Publishes all 192 Flash-Next UD-IQ4_XS ARC64, GSM64 and MMLU-Pro64 accuracy observations with full answer audits, coverage, source lineage and explicit performance-comparison limits.
- Documents the 32 MiB HTTP import limit separately from suite question-count limits, with file validation and contribution guidance for larger audits.

## 2.5.3 - 2026-11-04

- Adds the pinned 12,032-question MMLU-Pro test split and a fixed 64-question sample balanced across 14 subjects. Both use auditable zero-shot generated-answer chat protocols; the balanced sample defaults to all 64 questions.
- Supports capability suites and audited imports of up to 20,000 questions while retaining bounded prompt sizes. Preserves Unicode separators inside JSONL question text.
- Retains requested MTP metadata for external endpoints, separately from effective settings and observed per-request draft counts. This annotation does not configure the inference server.
- Retries transient permission failures during atomic capability-result saves, preserving completed answers without repeating model requests. Other persistence failures retain a non-sensitive error type.

## 2.5.2 - 2026-11-04

- Publishes the PNY and ASUS Prime dual RTX 5070 Ti dataset: 180 attempted cases across LM Studio and native Strata, with 179 successes and one retained native Qwen35 timeout.
- Adds matched single/mixed/dual-card performance and energy tables, per-card thermal observations, and a separate communication, placement and profiling study. Background-activity conditions and profiled captures remain separate from clean inference comparisons.
- Supports atomic cancellation of an owned speed or capability run using an expected run ID, and identifies endpoints that explicitly report Project Maya.
- Keeps capability collection separate from fresh RAM, storage and CUDA transfer probes; unavailable or reused hardware evidence retains its provenance.
- Retains validated per-request speculative draft counts when a backend supplies them, separately from requested MTP settings.
- Preserves selected Results conditions when consolidation splits a benchmark into engine views. Comparison links can select two to eight benchmarks and their shared models, with separate prefill and decode charts.
- Displays planned but unrecorded cases in consolidated comparison views.

## 2.5.1 - 2026-11-03

- Connects the Python helper to OpenAI-compatible streaming Chat Completions endpoints, with model discovery, manual model IDs and environment-based authentication. LM Studio remains a managed provider; other engines control their own model loading and allocation.
- Identifies endpoints automatically, discovers Results models from the current authenticated connection, and records model, quantization, reasoning and MTP provenance. Public exports omit connection addresses and credentials; unknown effective settings remain explicit.
- Adds capability evaluation with original Core tasks and pinned GSM8K and AI2 ARC-Challenge test splits. Zero-shot chat adaptations retain prompts, answers, grading evidence, subsets and repeats; they are separate from official leaderboard scores. Capability generation has an independent thinking-and-answer budget.
- Adds an interactive capability dashboard with score and coverage cards, category profiles, outcome breakdowns, repeat consistency, paired comparisons and a question-by-repeat evidence inspector.
- Records each detected NVIDIA card and simultaneous combined power, energy, VRAM and utilization. Concurrent requests share one energy interval. Live telemetry also shows CPU/RAM load, available temperatures and clocks, with measurement scope, missing values and retention coverage.
- Adds CPU, memory-topology, storage and per-card PCIe/transfer metadata, including native RAM copies, storage reads and pinned host-to-GPU probes. Hardware and transfer cards distinguish measured, inferred, saved and unavailable observations.
- Organizes Results into Summary, Table and Compare. Selected metrics have individual graphs; shared filters expose recorded storage and engine conditions, quantization methods, matching cases and raw repeats.
- Consolidates compatible engine sessions into one benchmark per hardware setup while preserving measurements and source metadata. Recorded board identities prevent incompatible board combinations. Same-file additions, repairs and removals retain backups and attempt history.
- Shows completed, stopped, failed and incomplete states explicitly. Preserves active work during helper refreshes, handles disconnected browsers, and blocks new tests while outstanding client requests are still closing after a timeout.
- Publishes five consolidated hardware comparisons, covering the RTX 5060 Ti, ASUS Prime and PNY RTX 5070 Ti boards, the i7-5820K/i7-5960X CPU comparison, and the mixed dual-GPU setup. Storage alternatives retain their measured baselines.
- Adds a separate dual-GPU communication and execution study with reproducible configurations, source fingerprints, raw samples, per-card energy and metric-specific graphs. Profiling and diagnostic captures remain separate from normal benchmark aggregates.
- Updates the portable release, hosted result catalog, documentation and API operator skill for the endpoint, capability and multi-GPU workflows.

## 2.4.1 - 2026-10-27

- Keeps viewport position and keyboard focus stable when toggling measures in Summary, Table and Compare.
- Includes a labelled preliminary RTX 5060 Ti result snapshot with its incompleteness and measurement limitations recorded.

- Preselects existing benchmark models when adding tests and shows each model's successful, failed, skipped and unfinished cases. New and unavailable models are distinguished; saved measurements remain intact when changing the selection.

## 2.4.0 - 2026-10-27

- Introduces measurement protocol 2, cross-platform hardware inventory, Linux support, concurrent queries and per-session provenance.
- Stores a benchmark in one JSON file and adds live case results, shared Results filters, chart comparisons and copyable tables.
- Adds same-file extensions, failed and skipped retries, selected-case reruns, attempt history and snapshots. Successful replacements remain separate from archived attempts.
- Plans shared KV capacity per concurrent request with a 5% prompt margin and supports a fixed context policy. Standard and MTP modes can be chosen per model.
- Adds hosted browser mode with direct OpenAI-compatible connections, local import/export and a separate measurement protocol.
- Adds adaptive request deadlines and verified model unload and worker recovery before continuing after a timeout.
- Adds a documentation set and an agent skill for the local API.
- Older concurrent failures may reflect legacy context allocation rather than a model capacity limit; use the session planner information when interpreting them.
- Older result schemas and planner settings remain readable. Extended files may contain sessions using different planners; compare provenance and settings before combining measurements.

## 1.0.0

- Initial release.
