# PNY RTX 5070 Ti: i7-5820K versus i7-5960X

The same PNY RTX 5070 Ti 16 GB was measured with an i7-5820K 6c12t and an i7-5960X 8c16t. Driver 617.42, the 300 W GPU limit and 64 GiB memory configured at 2133 MT/s were retained. The 5960X dataset contains **156 recorded cases across the 13 established model variants: 149 successful and 7 unsuccessful/censored**. All 156 cases match the previous 5820K matrix. Each case has one repeat. The published cohort contains ten LM Studio variants and three Strata variants; Qwen3.6-35B is served by Strata, and the retained Qwen3.8-27B quant is Q3_K_XL.

[The complete 156-row case comparison CSV](../results/aggregates/pny-5820k-vs-5960x.csv) includes both outcomes, actual token counts and context windows, quantization/mode/engine identity, configuration fingerprints, timings, measured GPU watts/joules, failures and ratio exclusions. Original per-source sessions and telemetry remain in the raw JSON.

These are descriptive comparisons from one pass on each CPU. Automatic worker choices, backend state, OS caching, clocks and ambient conditions were not independently controlled. Ratios require two successful cases and known compatible required metadata. Actual token counts are evaluated separately using the predeclared engineering tolerances below, rather than treated as model-configuration conflicts. Newly successful cases with a censored baseline have no invented baseline speed.

## CPU comparison coverage

|5820K outcome|5960X outcome|Matched cells|
|---|---|---:|
|failed|failed|7|
|ok|ok|149|

|Clients|Prefill ratio (N)|Decode ratio (N)|TTFT ratio (N)|Wall-time ratio (N)|Whole-case GPU energy ratio (N)|
|---:|---:|---:|---:|---:|---:|
|1|1.004 (75)|1.013 (75)|0.996 (75)|0.988 (46)|Unavailable (0)|
|2|0.999 (73)|1.020 (73)|1.000 (73)|0.992 (37)|0.987 (37)|

Ratios are 5960X/5820K, with an unweighted median across the eligible cells for that metric and client count. N is reported separately because the token/metric availability gates differ. Higher throughput and lower latency/energy ratios are favorable. These medians do not establish a statistical or causal CPU effect.

## Per-model summary

Throughput changes are medians of paired ratios across both client counts and all six prompt sizes. GPU decode energy per token uses only the six single-client cases. N is the number of eligible cells; each cell has one repeat.

|Model|Prefill change (N)|Decode change (N)|GPU decode J/token change (N)|
|---|---:|---:|---:|
|google/gemma-4-12b-qat|-0.0% (12)|+0.9% (12)|-1.8% (6)|
|google/gemma-4-26b-a4b-qat|+1.3% (9)|+2.6% (9)|+1.8% (5)|
|google/gemma-4-e2b|+1.6% (12)|+1.9% (12)|-5.7% (6)|
|google/gemma-4-e4b|+0.5% (12)|+0.8% (12)|+0.7% (6)|
|lfm2.5-24b-a2b|+5.8% (11)|+0.9% (11)|-3.6% (6)|
|openai/gpt-oss-20b|-0.2% (11)|+0.7% (11)|+2.6% (5)|
|prism-ml/bonsai-27b|+0.7% (12)|+1.6% (12)|-1.8% (6)|
|qwen/qwen3.5-9b|+0.4% (12)|+1.3% (12)|-1.3% (6)|
|qwen3.6-35b-a3b-ud-q4_k_xl|-0.2% (12)|+12.2% (12)|-9.6% (6)|
|qwen3.8-27b@q3_k_xl|+0.1% (9)|+1.2% (9)|-1.1% (5)|
|qwen3.8-flash-next-iq3_s|-0.4% (12)|+5.8% (12)|-5.8% (6)|
|qwen3.8-flash-next-unsloth-ud-iq4_xs|-0.3% (12)|+8.4% (12)|-8.3% (6)|
|spark-x2.6-4b|-1.0% (12)|+2.1% (12)|+0.2% (6)|

### Token-size matching rules

Old/new actual per-request token vectors are retained, sorted and compared element by element. Both vector lengths must equal the client count; missing usage is never replaced by the prompt target. The CSV exposes absolute deltas, maximum/median relative deltas, thresholds and per-metric eligibility/reasons. These tolerances are predeclared engineering choices for near-matched workloads, not statistical guarantees of equivalence.

- Prefill/decode tokens/s, decode GPU watts and normalized prefill/decode energy-per-token ratios require each input-token difference to be at most max(8 tokens, 2% of the larger count). Decode throughput uses the actually timed output-token numerator; a one-token input difference alone does not exclude it.
- Raw TTFT ratios use the tighter input rule max(4 tokens, 0.5% of the larger count).
- Raw whole-case wall-time and whole-case GPU-energy ratios require the 8-token/2% input rule and also the 8-token/2% rule for actual completion-token vectors. Whole-case GPU energy is available for concurrent groups, not a decode-only interval.
- Every metric additionally requires two successful cases, known compatible configuration and finite measured values with a positive baseline denominator. Failed/censored outcomes remain excluded even when partial token counts exist.

## 20k single-client observations

Values show client prefill / decode tokens/s and TTFT seconds. The CSV contains every prompt target and both client counts. Prefill is prompt tokens divided by client time to first observable output, including any queueing or unobserved reasoning; it is not an engine-only compute rate.

|Model|Quantization|5820K prefill / decode; TTFT|5960X prefill / decode; TTFT|Decode ratio|
|---|---|---:|---:|---:|
|google/gemma-4-12b-qat|Q4_0|2,744.04 / 76.62; 6.91|2,743.72 / 77.22; 6.92|1.008|
|google/gemma-4-26b-a4b-qat|Q4_0|3,009.79 / 127.82; 6.30|3,106.63 / 131.20; 6.11|1.026|
|google/gemma-4-e2b|Q4_K_M|4,853.18 / 130.00; 3.91|4,856.96 / 136.32; 3.91|1.049|
|google/gemma-4-e4b|Q4_K_M|3,818.97 / 97.20; 4.97|4,025.27 / 98.04; 4.71|1.009|
|lfm2.5-24b-a2b|Q4_0|3,687.34 / 181.85; 5.13|3,872.21 / 175.14; 4.89|0.963|
|openai/gpt-oss-20b|MXFP4|4,017.67 / 131.04; 4.62|4,059.30 / 129.98; 4.55|0.992|
|prism-ml/bonsai-27b|Q1_0|1,704.67 / 73.18; 11.10|1,694.77 / 74.08; 11.17|1.012|
|qwen/qwen3.5-9b|Q4_K_M|3,552.68 / 84.22; 5.33|3,579.96 / 85.66; 5.29|1.017|
|qwen3.6-35b-a3b-ud-q4_k_xl|UD-Q4_K_XL|1,254.94 / 31.37; 15.08|1,254.80 / 35.72; 15.08|1.139|
|qwen3.8-27b@q3_k_xl|Q3_K_XL|1,543.58 / 42.14; 12.26|1,542.62 / 42.14; 12.27|1.000|
|qwen3.8-flash-next-iq3_s|IQ3_S|1,207.54 / 34.89; 15.67|1,187.11 / 40.98; 15.94|1.175|
|qwen3.8-flash-next-unsloth-ud-iq4_xs|UD-IQ4_XS|1,098.11 / 22.00; 17.24|1,090.20 / 25.21; 17.36|1.146|
|spark-x2.6-4b|Q4_K_M|4,221.98 / 122.90; 4.50|4,297.50 / 122.26; 4.42|0.995|

## Transfer paths

All rates below are decimal GB/s of copied payload. Logical read+write traffic is approximately twice RAM-copy payload; these probes do not measure memory-controller bus traffic. Four populated firmware channels do not independently verify active channel mode.

|Probe|5820K | 5960X|5960X/5820K|
|---|---:|---:|---:|
|RAM copy, one worker|8.567|9.691|1.131|
|RAM copy, fixed six workers|21.836|23.399|1.072|
|RAM copy, automatic eight workers|Unavailable|21.933|Unavailable|
|Pinned RAM to VRAM|13.491|13.526|1.003|
|SATA sequential storage to RAM|0.538|0.538|1.000|
|NVMe sequential storage to RAM|2.720|2.816|1.035|

The matched six-worker measurement uses 64 MiB buffers per side per worker and eight copies per sample, matching the prior 21.8363 GB/s protocol. The 5960X fixed-six result is 23.3987 GB/s; automatic eight-worker 21.9327 GB/s is separate. Single-worker 9.691 versus 8.567 GB/s and pinned H2D 13.526 versus 13.491 GB/s describe these transfer paths. No controlled SATA/NVMe inference sweep was performed.

## First measured GPU compute and VRAM copy

These probes were first measured on the 5960X setup. There is no matching 5820K compute/VRAM probe, so they cannot show a CPU-related GPU throughput gain. Dense GEMM rates count multiplication and addition separately (2mnk), with 15 CUDA-event batch samples per shape and exact sampled-output validation. Tensor instruction selection was not independently profiled.

|Input / output arithmetic|Matrix edge|Median measured rate|
|---|---:|---:|
|FP32 SGEMM, pedantic/TF32 disabled|4096|35.68 TFLOP/s|
|FP32 SGEMM, pedantic/TF32 disabled|8192|36.63 TFLOP/s|
|FP16 inputs, FP32 compute requested/output|4096|100.33 TFLOP/s|
|FP16 inputs, FP32 compute requested/output|8192|101.77 TFLOP/s|
|INT8 inputs, INT32 accumulation/output|4096|330.03 TOPS|
|INT8 inputs, INT32 accumulation/output|8192|350.52 TOPS|

FP16 uses CUBLAS_COMPUTE_32F and tensor-enabled math. Reduced-precision partial reductions were not explicitly disabled; strict FP32 accumulation throughout is unverified. INT8 uses TOPS, not TFLOP/s. INT4 is unavailable; quantized model weight bits do not establish hardware arithmetic precision.

|D2D payload|Payload GB/s|Nominal read+write GB/s|
|---:|---:|---:|
|256MiB|374.50|749.00|
|1024MiB|375.17|750.34|

Every copied destination byte passed a full SHA256 readback check. These are measured CUDA copy-path rates, not a theoretical DRAM peak. Separately, sustained unmeasured H2D activity observed PCIeGen3×16; this link-state observation does not replace the 13.526 GB/s matched H2D measurement. Raw clock/power/temperature samples and library/driver versions are embedded in the run.

## Native execution and energy scope

|Model|5820K CPU threads observed|5960X CPU threads observed|GPU experts, 5960X|Host residency evidence|
|---|---:|---:|---:|---|
|qwen3.8-flash-next-unsloth-ud-iq4_xs|Unreported|Unreported|2469|55.44GiB full arena; pinned and VirtualLock evidence retained|
|qwen3.8-flash-next-iq3_s|Unreported|Unreported|3284|46.84GiB full arena; pinned and VirtualLock evidence retained|
|qwen3.6-35b-a3b-ud-q4_k_xl|6|8|19|Pinned hybrid-host memory observations retained|

- Native context remains 96K, with one backend slot queueing two clients. Effective expert cache/placement and actual locked/pinned host evidence are retained. Qwen automatic thread selection changing from six to eight is part of this configuration change; this is not a fixed-worker CPU microbenchmark.
- Actual KV dtype stays unknown where the backend does not report it. Qualified default-KV configuration equivalence is permitted only when explicit coordinator launch-capture sentinels document omitted --kv on both sides and known engine/version/context/effective expert cache match. This means the same backend-default configuration, not a verified dtype. Requested expert-VRAM budgets are retained as control settings; a budget adjustment alone does not conflict when actual expert placement and host residency agree.
- MTP requested does not prove drafts were generated. Engine-lifetime completion/draft counters include warmups; 30 completions are expected when all 12 cases succeed. Flash draft counters can include suffix drafts. Qwen retains the reference presence penalty, which can suppress drafting.
- Single-client prefill/decode GPU board watts and joules are measured by nvidia-smi. Two-client group GPU energy is integrated once across the whole case, including prefill, decoding and queueing. It is never labeled decode-only and overlapping request windows are not summed.
- Whole-system energy is an estimate, GPU + 60 W + 140 W×CPU-utilization fraction. The utilization denominator changes from 12 to 16 logical cores. Lower estimated system watts do not establish better measured CPU efficiency; no CPU package-energy or wall-meter measurement was made.
- Timeout/error outcomes remain in the CSV and raw data. A deadline is censoring, not a completed inference runtime. Failed/censored pairs have no speed/efficiency ratio.
- Repeats, ambient temperature, background activity, OS caches and automatic clocks limit precision. Small observed differences need further repeats. GPU compute/copy results are brief measured-throughput observations, not sustained inference predictions.

## PNY versus ASUS board comparison

The separate [PNY/ASUS/5060Ti report](pny-5070-ti-comparison.md) found an approximately 2.3% higher median PNY decode rate and 7.3% higher measured decode GPU watts across 76 matched successful single-client cells. That comparison used one PNY repeat versus two ASUS repeats and is descriptive. It does not isolate board design from cooling, clocks or run-to-run variation.

## Raw datasets

- [PNY RTX5070Ti — i7-5820K](../results/runs/20261102-004040_rtx-5070-ti_afd9bb57d2.json)
- [PNY RTX5070Ti — i7-5960X](../results/runs/20261102-041639_rtx-5070-ti_6b6060174b.json)

Source measurements, fingerprints, sessions, failures and bounded telemetry are preserved. The final experiment records verified StrataStopped, restored owned preset flags and released probe allocations/context. Endpoint credentials, private paths and device UUIDs are excluded from public attachments.
