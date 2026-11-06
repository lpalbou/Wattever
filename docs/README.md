# Documentation

- [Experiment checkpoint](experiment-status.md): completed dual-5070 performance and quality results, communication measurements, and the explicitly unmeasured GLM preparation state.

- [Browser mode](browser.md): hosted setup, privacy, storage and measurement limits.
- [Sharing results](sharing-results.md): shared catalog, contributions and completing a benchmark later.
- [Getting started](getting-started.md): managed LM Studio or generic endpoint setup, authentication, manual model IDs and first benchmark.
- [Measurements and sessions](measurements.md): client/provider metrics, cache provenance, hardware, compute claims, power boundaries and comparisons.
- [Concurrency comparisons](concurrency.md): total versus per-request throughput, latency ranges, model-by-request-count maps, request timing evidence, matched-repeat scaling and energy.
- [RTX 5060 Ti storage comparison](5060-ti-storage-comparison.md): measured Strata results on Samsung SATA SSD and NVMe, both repeats, transfer rates and comparison settings.
- [RTX 5070 Ti hardware and storage follow-up](5070-ti-hardware-follow-up.md): 144 paired storage observations, prefill/decode aggregates, native drafting evidence, three-pass transfers and memory-channel provenance.
- [PNY versus ASUS Prime RTX 5070 Ti](pny-5070-ti-comparison.md): one PNY repeat across all 13 primary models, power and throughput comparisons with ASUS and RTX 5060 Ti, and the cancelled storage follow-up.
- [i7-5820K versus i7-5960X with the PNY RTX 5070 Ti](5960x-cpu-comparison.md): 156 one-repeat inference cases across the 13 established models, matched CPU comparisons, RAM/PCIe/VRAM copy probes and measured GPU arithmetic.
- [PNY RTX 5070 Ti plus ASUS RTX 5060 Ti](5070-plus-5060-comparison.md): matched and supplemental LM Studio results, per-card and combined energy, transfer/compute probes, connection diagnostics and native Strata coverage status.
- [Multi-GPU communication and placement study](dual-gpu-communication-and-placement.md): communication paths, transfer measurements, placement strategies, profiling evidence and separate diagnostic protocols.
- [Two RTX 5070 Ti cards](two-5070-ti-comparison.md): 180 normal cases, matched single/mixed-card comparisons, native drafting, energy and thermal measurements.
- [Two RTX 5070 Ti placement strategies](two-5070-ti-placement.md): host/cross-card transfers, 153 screening conditions, three profiles, Gemma layout and workload-specific recommendations.
- [Capability evaluation](capabilities.md): visual score/coverage dashboard, question-by-repeat explorer, compatible paired comparisons, offline pinned suites and audit imports.
- [Flash-Next task accuracy](flash-next-quality.md): UD-IQ4_XS and IQ3_S on the same ARC64, GSM64 and MMLU-Pro64 samples, with full answer evidence, coverage, generation controls and source lineage.
- [Architecture](architecture.md): components and data flow.
- [API](api.md): local HTTP interface and side effects.
- [FAQ](faq.md): common interpretation questions.
- [Troubleshooting](troubleshooting.md): setup and runtime symptoms.
- [Agent skill](../skills/wattever/SKILL.md): operating procedure for automated users.

Return to the [project overview](../README.md); see [contributing](../CONTRIBUTING.md) for development and results submissions.
