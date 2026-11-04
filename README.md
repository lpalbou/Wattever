# Wattever

Wattever benchmarks LLM endpoints across prompt sizes, models and simultaneous query counts, and compares successes and failures in a browser UI. Connect the local Python helper or hosted browser to an OpenAI-compatible streaming Chat Completions endpoint, including Strata, llama.cpp and LM Studio. The helper also offers managed LM Studio model loading and optional local GPU power measurements. Wattever uses Python 3.9+, the Python standard library and local JavaScript without a package installation or CDN.

## Browser quick start

Open [Wattever on GitHub Pages](https://lpalbou.github.io/Wattever/), start an OpenAI-compatible server, enable its CORS support, and use **Connect** to select models and run tests. Browser and server permissions can affect access to a local endpoint. See [Browser mode](docs/browser.md) for settings, storage, privacy and measurement limits.

## Full local helper quick start

1. Start your inference engine and load a model. For managed LM Studio operation, install LM Studio, download models and run `lms bootstrap` once. Other endpoints do not need LM Studio.
2. Download and extract the [Wattever 2.5.5 source archive](https://github.com/lpalbou/Wattever/releases/download/v2.5.5/Wattever-2.5.5.zip), or clone the repository. Use the matching documentation and source version.
3. On Windows run `Wattever.bat`; on macOS run `Wattever.command`; on Linux run `sh Wattever.sh`. Alternatively run `python -m wattever.server` from the repository.
4. Open the displayed local URL. On **Run**, choose **OpenAI-compatible endpoint**, enter your API base URL and use **Connect**. Wattever identifies the endpoint where evidence is available and supplies its display name automatically; no manual label or ID is required. Enter a model ID manually if discovery is unavailable. Choose workloads and start. Leave the server running until the benchmark finishes.

See [Getting started](docs/getting-started.md) for both provider modes and [Measurements and sessions](docs/measurements.md) for interpretation. The external inference engine controls model loading, quantization, context, KV cache, GPU placement and speculative decoding. With Strata Control, start the server and load the model there; connecting Wattever does not start or switch Strata models. Generic endpoint power measurements require an explicit assertion that inference runs on the benchmark computer; remote inference power remains unavailable.

**Results** has three views: **Summary** reports primary coverage, hardware and measured machine transfers; **Table** lists numerical results and raw repeats; **Compare** plots selected measurements. Table and Compare share model, metric, query-count and recorded-condition filters. The local helper supports **Add / rerun tests** for managed LM Studio and reconnected external endpoints. One JSON file stores each benchmark. Review it before publication; see [results guidance](results/README.md).

Consolidation preserves engine/session settings and exact measurement rows. Conflicting known GPU board identities remain separate even when the GPU chip name matches; legacy unknown board metadata stays unknown.

The collapsed **This machine** header shows CPU, GPU, RAM and per-card VRAM. Expand it for grouped compute, storage, transfer and platform cards; full names and probe methods stay accessible. Compatible saved transfer captures retain their original date and are labeled **Saved capture**.

GPU, memory, storage and engine-setting badges summarize recorded conditions. Storage types use **HDD**, **SSD** and **NVMe**; unknown types stay unclassified. If an experiment contains alternative storage observations, **Compare recorded storage** selects the measured variants. Installed drives and storage probes alone do not create inference variants. See the [RTX 5060 Ti Strata comparison](docs/5060-ti-storage-comparison.md) for the measured gains and conditions.

The [two RTX 5070 Ti comparison](docs/two-5070-ti-comparison.md) records 180 normal cases across LM Studio and native Strata, with matched throughput, energy, drafting and per-card thermal observations.

The [multi-GPU communication and placement study](docs/dual-gpu-communication-and-placement.md) examines execution strategies with separate diagnostic protocols. Transfer microbenchmarks and profiled inference captures stay separate from the normal benchmark library. Study tools have their own platform and dependency requirements.

The hosted Results view also includes a shared catalog that you can browse without connecting a model provider. See [sharing results](docs/sharing-results.md) to contribute your benchmark or add missing tests later.

## Capability tests and live telemetry

The helper's **Capabilities** tab runs original Core tasks and pinned GSM8K, ARC-Challenge and MMLU-Pro test questions. MMLU-Pro offers its full 12,032-question split or a fixed 64-question sample balanced across 14 subjects. Choose a deterministic subset or the full suite and compare auditable rule-scored answers. These are zero-shot chat adaptations, separate from official leaderboard scores and intelligence rankings. Requested reasoning, reported MTP/quantization and unknown effective settings remain distinct in public model/endpoint configuration fingerprints. See [capability evaluation](docs/capabilities.md) for scoring, dataset licenses and uncertainty.

The [Flash-Next accuracy collection](docs/flash-next-quality.md) compares UD-IQ4_XS and IQ3_S on the same ARC64, GSM64 and MMLU-Pro64 samples, with inspectable answers and complete question coverage, separately from throughput measurements.

The [experiment checkpoint](docs/experiment-status.md) brings together the completed dual-5070 results and distinguishes them from GLM-5.3-Flash preparation, which has no inference measurements yet.

Its visual dashboard combines strict pass rate, correct/planned and coverage tiles with category bars, an outcome ring and repeat comparisons. Select a chart to filter the paginated question-by-repeat matrix, then open a cell to inspect the question, expected output, model response and rule explanation. Matching evaluation protocols support paired comparisons that exclude unscored requests. A format mismatch or request error is shown distinctly from a passed answer; inspect the evidence before drawing conclusions about reasoning.

Local runs can display CPU/GPU utilization, RAM/VRAM usage, available temperatures, GPU clocks and power. On supported NVIDIA systems, measurements cover all detected cards with individual readings and combined GPU board power. Concurrent requests share one group energy window, so overlapping request energy is not added together. CPU/system power estimates remain labeled separately from measured GPU power; they are not wall-meter readings.

Saved telemetry includes a bounded recent history and whole-run observed means/peaks/coverage; early/late changes are observations, not a throttling diagnosis. Results charts separate selected metrics into individual graphs. The hosted site can browse/export/compare shared capability results; launching tests and validating capability imports require the helper.

## Documentation

[Documentation index](docs/README.md) | [Architecture](docs/architecture.md) | [API](docs/api.md) | [FAQ](docs/faq.md) | [Troubleshooting](docs/troubleshooting.md) | [Changelog](CHANGELOG.md) | [Contributing](CONTRIBUTING.md) | [Security](SECURITY.md)

## For AI agents

Register and use the [Wattever agent skill](skills/wattever/SKILL.md) to operate the local API. Copy `skills/wattever/` from the version you use into your agent's skills directory.

Wattever is [MIT licensed](LICENSE).
