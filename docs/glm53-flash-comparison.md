# GLM-5.3-Flash on two RTX 5070 Ti cards

GLM-5.3-Flash runs on the i7-5960X, 64 GiB RAM and two 16 GB RTX 5070 Ti
cards using Project Maya's mixed Maya-XS quantization. The original model shards
and compatibility pack are on the existing 256 GB AirDisk NVMe. Complete verified
SATA backups are retained but are not the inference source. The Samsung 990 Pro
has not been installed or measured in this capture.

These are **preliminary diagnostic results**, separate from the normal machine
benchmark aggregates. The initial two single-client cases completed successfully;
the remaining context/concurrency matrix and optimization screens are in progress.
There is one measured repeat per condition. Model-quality evaluation is pending.

See the [raw snapshots, CSV and manifest](../results/studies/20261106-glm53-flash/README.md).

## First measured results

| Prompt target | Actual input / output tokens | Client prefill | Client decode | First token | Group time | Combined decode GPU power | Peak temperature |
|---|---:|---:|---:|---:|---:|---:|---:|
| 512 | 503 / 238 | 38.18 tok/s | 9.77 tok/s | 13.17 s | 37.44 s | 138.12 W | 54 C |
| 20k | 17,341 / 256 | 313.39 tok/s | 11.34 tok/s | 55.33 s | 77.82 s | 143.42 W | 66 C |

The backend reports 38.3/315.4 tok/s prompt evaluation and 9.8/11.4 tok/s
generation respectively. Client prefill is actual input tokens divided by TTFT;
backend prompt evaluation is recorded separately and is not isolated GPU compute.
The first output token is excluded from the client decode interval/count.

MTP is active: the two cases report 209/236 and 212/254 accepted/offered drafts.
Those counters describe observed activity, not a measured MTP-on/off speedup.
Output lengths differ because generation stops naturally, with a 256-token cap.
Target prompt lengths are estimates; comparisons must retain the actual counts.

## Comparison with the existing Flash-Next observations

These are single-client observations at the nominal 20k target on the same two-card
hardware setup. They use different models, quantization, backends, KV placement,
effective speculation and prompt/output counts. They are descriptive serving
comparisons, not isolated hardware, quantization or quality comparisons.

| Model / quantization | Backend | Actual input / output | Client prefill | Client decode | First token | Combined decode GPU power |
|---|---|---:|---:|---:|---:|---:|
| GLM-5.3-Flash / Maya-XS mixed IQ1_S + IQ2_XXS + BF16-compat | Project Maya | 17,341 / 256 | 313.39 tok/s | 11.34 tok/s | 55.33 s | 143.42 W |
| Flash-Next / UD-IQ4_XS | Native Strata | 18,927 / 192 | 1,062.01 tok/s | 18.18 tok/s | 17.82 s | 121.65 W |
| Flash-Next / IQ3_S | Native Strata | 18,928 / 226 | 1,089.00 tok/s | 73.26 tok/s | 17.38 s | 168.04 W |

Flash-Next values come from the retained
[paired-case table](../results/aggregates/two-5070-ti-paired-cases.csv) and
[two-card report](two-5070-ti-comparison.md). No historical timings are adjusted.
The initial GLM configuration is slower in both observed phases. Speed does not
establish which model answers tasks better.

## Placement and optimization lead

The GUI requests a **40 GiB global expert RAM tier**, but startup reports only
**20.46 + 7.25 GiB pinned**. The second partition reduces its allocation after a
CUDA pinning refusal. GPU expert pools are approximately 9.59 and 9.61 GiB;
these are not the entire VRAM allocation. Dense weights, KV/state, activation
scratch and staging consume additional VRAM. Reported startup sizes are rounded.

For one observed 8192-token prompt chunk, the first partition reports 3.76 GB of
expert reads from disk and the second 15.03 GB. Their reported chunk wall times
are 5.75 and 17.97 seconds. These are backend copy-path counters, not independent
physical NVMe measurements. The imbalance and disk waits make more balanced
host residency a useful test; a smaller requested RAM tier is not yet a measured
performance improvement. No driver or operating-system changes are involved.

The physical order is ASUS Prime then PNY, represented by GPU selection `1,0`.
Automatic layer splitting places trunk layers 0–24 on the first logical GPU
and 25–44 on the second, with the MTP head on the second. The fixed context is
98,304 tokens, with one backend request slot. INT8 latent KV is authoritative in
pinned host memory, with a requested 32,768-position VRAM subset. This differs
from the native Flash-Next all-VRAM KV condition.

## Protocol and provenance

- Model revision: `6728cf78a5b198a6a6f84b7fb88956a17ec56521`; two verified shards total 73,411,389,088 bytes.
- Runtime: Project Maya 1.1.52, revision `3c935e411cd6625a9af0f292dd0af52598e866d8`.
- Vendor-standard compatibility conversion: 849 non-expert tensors rounded to BF16; original GGUF expert bytes remain unchanged. This is a separate treatment from the quantizer's published scores.
- Sampling: temperature 0, top-p 1, seed 73129, natural stopping with maximum 256 output tokens.
- Reasoning: Maya's forced-off closed-think prompt treatment; it is distinct from official GLM LOW reasoning. A separate readiness pilot returned `READY` and stopped normally; that pilot is excluded from speed results.
- One warmup per measured helper session/model preparation is excluded. The expansion preserves the initial two cases and requests ten missing cases, with zero retries or reruns.
- Timeout: 300 seconds per measured case. Request count is separate from the engine's one backend slot.
- All target experts participate; disk/miss expert skipping is disabled. Background downloads are paused for timed collection.
- The existing Wattever process remains version 2.5.5, build `190ae30841a1b95272a4`. On-disk 2.6.0 sources are captured separately and are not claimed to identify already-imported helper code.
- Per-card and combined power samples are retained. Concurrent energy uses one shared group window, never summed overlapping request windows. GPU board energy is not whole-system wall-meter energy.
- Thermal monitoring cancels only the owned benchmark job at 83 C. CPU package and GPU memory-junction temperatures are unavailable.

For the general metric definitions and concurrency views, see
[Measurements](measurements.md) and [Concurrency comparisons](concurrency.md).
