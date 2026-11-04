# Dual RTX 5070 Ti experiment checkpoint

This checkpoint records the completed studies and GLM preparation on
2026-11-04. The machine has an i7-5960X, 64 GiB DDR4 and PNY plus ASUS Prime
RTX 5070 Ti cards, each with 16 GiB. Performance, capability evaluation,
transfer probes and profiled captures remain separate measurement families.

## Completed measurements

| Collection | Coverage | Published evidence |
| --- | --- | --- |
| LM Studio and native Strata | 180 attempted cases: 179 successful, one retained timeout; one measured repeat per case | [Performance report](two-5070-ti-comparison.md), [case CSV](../results/aggregates/two-5070-ti-absolute-cases.csv) |
| Standalone llama.cpp placement and concurrency | 153 unprofiled conditions plus three separately labelled Gemma traces; successes, unsupported modes and failures retained | [Execution report](two-5070-ti-placement.md), [raw diagnostic archive](../results/aggregates/two-5070-ti-diagnostic-raw.json.gz) |
| Communication probes | 280 conditions across 14 routes, ten transfer sizes and two timing modes; internal samples are not inference repeats | [Transfer samples](../results/aggregates/two-5070-ti-diagnostic-micro-samples.csv) |
| Flash-Next UD-IQ4_XS accuracy | ARC64, GSM8K64 and MMLU-Pro64: 192 answers | [Quality report](flash-next-quality.md) |
| Flash-Next IQ3_S accuracy | The same 192 questions; all answered once | [Quality audits and charts](flash-next-quality.md) |
| GLM-5.3-Flash | Download and verified staging complete; runtime pack and measurements pending; **no inference or quality results collected** | Preparation scope below |

The hosted Results catalog contains **six hardware setups**, including the
dual-5070 setup. Engine sessions stay within their hardware entry. The
standalone diagnostics do not enter normal benchmark aggregates. Capabilities
contains six separate suite results for the two Flash-Next packages.
Nineteen diagnostic conditions retain download-overlap flags. Instrumented
captures and background-confounded observations remain outside clean inference
comparisons; their timing is not substituted for an unprofiled measurement.

## What two cards improve

These representative standalone results compare the same quantization and
backend on one 5070 Ti against balanced layer placement on both cards.
Numbers are client prefill / decode in tokens per second, at an 80k prompt
target. Actual prompt/output counts, contexts and settings remain in the
[execution report](two-5070-ti-placement.md).

| Model and quant | One card | Both cards, balanced | Interpretation |
| --- | ---: | ---: | --- |
| LFM2.5-24B, Q4_0 QAD | 1,792 / 83.69 | 7,405 / 129.38 | Both phases improve; 256 output tokens in both cases |
| Qwen3.8-27B, Q3_K_XL | 449 / 1.41 | 2,010 / 36.42 | Large gain where the single-card placement spills weights; 221 output tokens in both cases |
| Qwen3.6-35B, Q4_K_M | 386 / 50.20 | 4,882 / 113.22 | Both phases improve; natural EOS gives 234 versus 233 output tokens |
| Gemma E2B, Q4_K_M | 7,304 / 152.66 | 5,645 / 10.60 | Equal splitting is a poor choice for this architecture |

Gemma's reversed weighted placement reaches 9,507 prefill and 111.66 decode
tokens/s: substantially better than its equal split, while single-card decode
remains faster. Shared-KV placement is a measured configuration difference;
the traces do not establish every causal contribution.

Independent LFM instances on separate cards at 20k/two clients improve measured
whole-job output throughput from 45.99 to 117.77 tokens/s. At 80k, balanced
placement instead provides 13.65 whole-job tokens/s versus 9.84 for independent
instances. Per-request rates are not added to manufacture simultaneous
throughput. Overlapping GPU energy is integrated once.

## Native Flash-Next performance and accuracy

The normal native-Strata cases below use one client. Each package is a
separate model condition; this is not an isolated bit-width comparison.

| Package | 20k prefill / decode | 80k prefill / decode | ARC64 | GSM8K64 | MMLU-Pro64 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unsloth UD-IQ4_XS | 1,062.01 / 18.18 | 1,971.69 / 17.55 | 63 / 64 | 63 / 64 | 54 / 64 |
| ISTA-DASLab IQ3_S | 1,089.00 / 73.26 | 2,359.37 / 69.58 | 64 / 64 | 62 / 64 | 52 / 64 |

The accuracy runs have complete coverage, no request errors and no output
limits, with a 32,768-token generation budget. They use 65,536 context; the
separate normal performance cohort uses 98,304. Download activity overlapped
quality collection, so **quality timings and energy are not performance
measurements**. Small fixed public-question subsets cannot establish general
quality equivalence or an official leaderboard ranking. See the descriptive
intervals and individual answers in the [quality report](flash-next-quality.md).

IQ3_S is the practical speed candidate among these measured native packages.
Its small sample-score differences from UD-IQ4_XS need wider evaluation before
making a general quality claim. Native Qwen35 used one GPU despite two being
installed, so its results do not establish a two-card acceleration.

## Communication and temperature

Both cards negotiated PCIe Gen3 x16 under load. CUDA peer access is unavailable
in both directions. Explicit host-staged copies are labelled as staged;
fallback copies are not reported as direct P2P.

| Measured route | Throughput |
| --- | ---: |
| Pinned RAM to PNY / ASUS, 256 MiB | 13.575 / 13.555 GB/s |
| PNY / ASUS to pinned RAM, 256 MiB | 10.387 / 10.418 GB/s |
| Explicit staged card 0 to 1 / 1 to 0 | 1.773 / 2.375 GB/s |
| Measured simultaneous equal-size RAM to both GPUs | 13.961 GB/s combined |
| Measured simultaneous weighted 2:1 RAM to both GPUs | 19.465 GB/s combined |
| Measured bidirectional cross-card traffic | 11.308 GB/s combined |

These values retain their sizes, timing methods, synchronization and validation
in the [transfer evidence](two-5070-ti-placement.md). They are not sums of
independent rates. Link ceilings are not measured transfer rates.

The completed performance campaign reached 77°C on PNY and 67°C on ASUS, with
no observed hardware/software thermal-slowdown flags. GPU die observations do
not measure memory-junction temperature; that counter is unavailable. Per-card
power, clocks, utilization, energy and sample coverage remain published.

## GLM preparation and remaining work

The selected first candidate is **Maya-XS mixed IQ1_S/IQ2_XXS**, pinned to
quantizer revision `6728cf78a5b198a6a6f84b7fb88956a17ec56521`. Its two shards
total **73,411,389,088 bytes**. The official model publisher reports **320B
total / 18B active parameters**. The rounded parameter counts do not describe
the quantized file size.

The download completed on 2026-11-04. By 05:12 UTC, both shards had their full
publisher checksums verified on NVMe and on complete SATA backup copies.
The runtime compatibility pack has not been created. The
larger Maya-S24 package has not been downloaded or benchmarked.

The experimental runtime is **Project Maya v1.1.52**, source
`3c935e411cd6625a9af0f292dd0af52598e866d8`, built for the installed Windows/CUDA
environment. It is a Strata-derived backend, distinct from native Strata.
The vendor BF16 compatibility pack is a separate runtime treatment, so the
quantizer's published quality figures do not directly describe this experiment.

No GLM weights have been loaded, no GLM inference endpoint has been started,
and **there are no measured GLM prefill, decode, energy or accuracy scores**.
Runtime compilation and engine-free control checks establish preparation,
not working-model or performance evidence. Therefore no measured comparison
with Flash-Next or optimized GLM setting is claimed.

The remaining sequence is verified staging and pack creation; foreground
Strata Control loading; actual allocation, MTP, KV and output-budget checks;
one measured 20k/single-client case; then the missing cases in the six-context,
one/two-client matrix. RAM tiers, KV placement, MTP policy and serving slots
require separately identified conditions. Official GLM LOW reasoning must
remain separate from Maya's forced-off prompt treatment. Fixed capability
questions use the same source selections as the Flash-Next audits.

GLM source and model references: [official model](https://huggingface.co/zai-org/GLM-5.3-Flash),
[quantizer](https://huggingface.co/peasantsmith/GLM-5.3-Flash-Maya-GGUF),
[Maya runtime](https://github.com/mw00/project-maya/tree/3c935e411cd6625a9af0f292dd0af52598e866d8).
