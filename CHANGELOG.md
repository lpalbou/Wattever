# Changelog

## 2.5.0 - 2026-11-03

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
