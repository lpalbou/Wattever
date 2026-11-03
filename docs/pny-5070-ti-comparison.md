# PNY and ASUS Prime RTX 5070 Ti versus RTX 5060 Ti

The PNY card was measured once per case, using the same model set, six prompt targets and one/two clients as the reference cards. The PNY dataset contains **156 completed primary observations: 149 passed and 7 timed out**. The user cancelled the remaining storage follow-up; its partial observations and cancellation outcome are preserved separately and excluded from primary statistics. No completed PNY controlled SATA/NVMe comparison is claimed.

Across 76 comparable successful single-client primary cases, the median PNY/ASUS ratio was **1.023 for decode speed** and **1.073 for reported decode GPU watts**: about 2.3% faster decoding and 7.3% higher reported board power in this workload mix. Both cards reported a 300 W limit. These are medians of per-case ratios, rather than whole-run energy totals. One PNY repeat versus two ASUS repeats, unmeasured ambient conditions and board sensor differences limit conclusions about the designs.

[All model/context/client/card aggregates (CSV)](../results/aggregates/pny-asus-5070-ti-5060-ti.csv) include actual token counts, prefill/decode, TTFT, board power, phase energy where available, whole-case energy for concurrent requests, temperatures, failure kinds and deadlines. Raw sessions and telemetry remain in the benchmark JSON files.

Prefill, decode and TTFT use the median over requests within each successful case, then the median over successful repeats. Min/max fields describe those case medians across repeats. PNY/reference ratios compare one PNY case with the reference median; known configuration conflicts suppress ratios and are listed in the CSV. Unreported backend settings remain unverified. Two-client whole-case energy is integrated once over the group, without summing overlapping request windows.

## 20k single-client comparisons

Each cell shows client prefill / decode in tokens/s, then measured decode GPU watts. Reference cells are medians of successful repeats. PNY has one observation. These cells do not establish statistical differences.

| Model / quant | Cohort | RTX 5060 Ti | ASUS Prime 5070 Ti | PNY 5070 Ti |
| --- | --- | ---: | ---: | ---: |
| google/gemma-4-12b-qat · Q4_0 | primary | 1,557.0 / 41.8; 123.1 W | 2,630.9 / 74.2; 198.6 W | 2,744.0 / 76.6; 218.5 W |
| google/gemma-4-26b-a4b-qat · Q4_0 | primary | 295.1 / 82.3; 89.2 W | 2,947.4 / 120.8; 185.0 W | 3,009.8 / 127.8; 205.8 W |
| google/gemma-4-e2b · Q4_K_M | primary | 3,777.4 / 91.1; 99.9 W | 4,927.2 / 128.8; 163.8 W | 4,853.2 / 130.0; 200.2 W |
| google/gemma-4-e4b · Q4_K_M | primary | 2,801.1 / 63.2; 118.9 W | 3,963.6 / 98.3; 182.2 W | 3,819.0 / 97.2; 199.6 W |
| lfm2.5-24b-a2b · Q4_0 | primary | 2,607.0 / 129.8; 133.5 W | 3,737.0 / 184.9; 216.4 W | 3,687.3 / 181.8; 218.4 W |
| openai/gpt-oss-20b · MXFP4 | primary | 2,745.5 / 82.7; 115.9 W | 3,953.2 / 127.3; 185.7 W | 4,017.7 / 131.0; 198.0 W |
| prism-ml/bonsai-27b · Q1_0 | primary | 898.0 / 43.7; 131.5 W | 1,616.9 / 70.6; 196.6 W | 1,704.7 / 73.2; 216.3 W |
| qwen/qwen3.5-9b · Q4_K_M | primary | 2,211.6 / 50.4; 139.8 W | 3,456.2 / 82.2; 212.5 W | 3,552.7 / 84.2; 237.5 W |
| qwen3.6-35b-a3b-ud-q4_k_xl · UD-Q4_K_XL | primary | 650.7 / 21.5; 68.7 W | 1,282.2 / 29.4; 91.8 W | 1,254.9 / 31.4; 93.8 W |
| qwen3.8-27b@q3_k_xl · Q3_K_XL | primary | 811.2 / 22.9; 147.3 W | 1,468.3 / 41.0; 228.4 W | 1,543.6 / 42.1; 248.6 W |
| qwen3.8-flash-next-iq3_s · IQ3_S | primary | 647.8 / 20.3; 54.2 W | 1,201.3 / 34.4; 96.0 W | 1,207.5 / 34.9; 96.0 W |
| qwen3.8-flash-next-unsloth-ud-iq4_xs · UD-IQ4_XS | primary | 198.0 / 8.7; 43.0 W | 1,083.7 / 20.8; 84.8 W | 1,098.1 / 22.0; 85.9 W |
| spark-x2.6-4b · Q4_K_M | primary | 2,961.2 / 73.9; 135.2 W | 4,198.0 / 120.1; 211.6 W | 4,222.0 / 122.9; 231.4 W |

## Completion and coverage

| Board | Primary successful / recorded | Controlled SATA successful / recorded | Controlled NVMe successful / recorded |
| --- | ---: | ---: | ---: |
| RTX 5060 Ti | 291 / 312 | 70 / 72 | 72 / 72 |
| ASUS Prime RTX 5070 Ti | 294 / 312 | 72 / 72 | 72 / 72 |
| PNY RTX 5070 Ti | 149 / 156 | Not completed | Not completed |

## Transfer measurements

GB/s uses decimal bytes. These short sequential/copy probes describe transfer paths, rather than model inference speed or DRAM controller bandwidth. PNY values below come from its fresh pre-benchmark capture; reference values retain their original capture provenance.

| Board | SATA read GB/s | NVMe read GB/s | Pinned RAM→VRAM GB/s | RAM copy, one worker GB/s | RAM copy, six workers GB/s |
| --- | ---: | ---: | ---: | ---: | ---: |
| RTX 5060 Ti | 0.533 | 2.702 | 6.850 | 7.752 | 21.330 |
| ASUS Prime RTX 5070 Ti | 0.535 | 2.753 | 13.435 | 8.530 | 21.299 |
| PNY RTX 5070 Ti | 0.538 | 2.720 | 13.491 | 8.567 | 21.836 |

## Limits of the comparison

- GPU board watts and joules come from nvidia-smi. Whole-system watts and joules are estimates. No wall-meter measurement was made.
- Single-client phase energy is reported separately for prefill and decode. Two-client whole-case energy includes prefill, decoding and queueing; overlapping per-request windows are not summed.
- Prefill is prompt tokens divided by time to first visible token. Native Strata has one backend slot, so two-client values include queue delay.
- PNY primary LM Studio uses the current fixed 300-second deadline. Some ASUS LM Studio reference cases used older adaptive deadlines under a 1800-second configured cap. Successful ASUS LM Studio primary cases completed in less than 300 seconds, but censored outcomes must not be treated as speed ratios. Native primary uses 300 seconds; controlled storage uses 900 seconds.
- Primary ASUS/PNY Flash uses full locked/pinned RAM arenas, and primary Qwen3.6 uses 19 GPU experts per layer on both 5070 Ti boards. RTX 5060 Ti primary residency/placement differs, so its native comparison includes more than the GPU change. Completed controlled SATA/NVMe records in the CSV belong to the ASUS and 5060 Ti references; no PNY controlled pair was completed.
- Requested MTP does not prove that drafts were generated. Engine-scoped counters are retained; Flash counters can include suffix drafts. Qwen3.6 inherits the reference presence penalty, which can suppress drafting.
- OS standby caches were not forcibly flushed. Actual input/output token counts and per-case deadlines are retained. One PNY repeat limits conclusions about repeatability and small differences.
- Ambient temperature was not measured and cooling/fan behavior was not normalized. Temperature and power differences from this one-pass experiment cannot isolate board design alone.
- Board partner is user-identified; nonunique PCI subsystem/vBIOS observations distinguish the installed PNY board. Driver, CPU and memory provenance are recorded per session.

## Raw datasets

- [RTX 5060 Ti](../results/runs/20261030-211908_rtx-5060-ti_af3694cca3.json)
- [ASUS Prime RTX 5070 Ti](../results/runs/20261031-165925_rtx-5070-ti_57b747d701.json)
- [PNY RTX 5070 Ti](../results/runs/20261102-004040_rtx-5070-ti_afd9bb57d2.json)
