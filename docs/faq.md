# FAQ

## Can I run without installing Wattever?

Yes. The hosted [browser mode](browser.md) connects directly to an OpenAI-compatible endpoint. The endpoint and browser must allow the connection. The Python helper also supports generic endpoints without LM Studio; managed model control and same-file extensions are specific to its LM Studio provider.

## Does the selected GPU determine where inference runs?

No. It selects a telemetry sensor. Configure placement in the inference engine and record the setup in notes. Generic endpoints require an explicit local-inference assertion before attributing client power to inference. See [measurements](measurements.md).

## Are power numbers comparable?

GPU board and SoC rail measurements have different boundaries; estimated whole-system power is not a wall meter and only includes the selected GPU. Compare like sources and settings.

## Can I compare old and new files?

Older files remain readable. Compare protocol, model, corpus, settings, context planner and session provenance; legacy and newer context allocation can differ within an extended benchmark. See [measurements](measurements.md).

## Does a failed case invalidate the benchmark?

No. Failures and skipped cases are recorded and can be retried. See [troubleshooting](troubleshooting.md) for common causes.

## Must a benchmark be complete before I share it?

No. Partial sessions are useful contributions. Managed LM Studio runs can add missing cases to the same file later, subject to compatibility checks. Generic endpoint and browser experiments use new runs. See [sharing results](sharing-results.md).

## Does any OpenAI-compatible API work?

The required surface is streaming Chat Completions. `/models` discovery is optional because you can enter exact served IDs manually. Missing usage counts leave generic token throughput unavailable. Responses, embeddings, image and audio APIs are not benchmarked. Configure context, KV cache, quantization and MTP in your engine; Wattever's generic adapter does not control them.

## Why can longer prompts show higher prefill throughput?

The displayed metric divides prompt tokens by client TTFT, which includes fixed overhead, queueing and network time. Larger batches can also use hardware more efficiently. Cache reuse and provider warm state can change it further. Inspect separately reported engine timings and cache usage; missing cache data does not establish a cold run.

## Does Wattever measure bandwidth and TFLOPS?

Hardware metadata includes reported memory clocks and PCIe link state where available. Its encoding-based PCIe ceiling is theoretical, and idle links can downshift. It does not benchmark DMA or memory bandwidth or derive compute peaks from GPU names. Optional sourced, precision-specific compute claims are user-reported and stay separate from detected fields. See [measurements](measurements.md).

## Are GSM8K and ARC scores official benchmark results?

They use pinned real test splits, with versioned zero-shot chat prompts and deterministic numeric/answer-letter grading. They are Wattever adaptations, not published few-shot or likelihood-based leaderboard protocols. Compare matching suite/subset, token budget, sampling and grading versions. Public tasks may be contaminated, and repeats are not independent questions. See [capability evaluation](capabilities.md).

## Does a configuration fingerprint prove which model ran?

No. It checks consistency of public model/endpoint identifiers and known/requested/reported settings. A canonical ID helps distinguish a served alias, but hashes do not authenticate weights or server routing. Unknown effective reasoning, MTP or quantization remains unknown.

## Must I invent an endpoint name or ID?

No. Connect resolves them automatically, as do direct API starts. Names come from recognized official cloud hosts or explicit service metadata, with generic Local/Private/Remote endpoint fallbacks when unknown. Ports and model names are not used to guess software. Advanced settings offer one optional **Display name** override. Canonical known public API routes have stable provider IDs; other routes use a private random-ID registry without publishing or hashing the connection URL.

## Can telemetry show thermal throttling?

It shows observed utilization, temperatures, clocks and power where sensors exist. Whole-run aggregates and recent history can reveal changes to investigate. Different workloads and competing processes also change these readings; drift alone does not diagnose throttling. The bounded recent trace may omit older samples while whole-run observed means/peaks remain preserved.
