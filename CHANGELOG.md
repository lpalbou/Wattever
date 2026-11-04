# Changelog

## Unreleased

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
