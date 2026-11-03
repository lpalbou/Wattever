# RTX 5070 Ti: hardware and paired storage follow-up

The [5070 Ti benchmark JSON](../results/runs/20261031-165925_rtx-5070-ti_57b747d701.json) now contains **144 completed storage observations**, three independent transfer captures, memory-topology evidence and a PCIe diagnostic. All 144 latest storage observations passed. The original 312 inference rows, sessions and engine groups are preserved. One earlier 600-second timeout remains in the attempt history.

Open **Results**, select **RTX 5070 Ti**, then **Compare recorded storage**. The charts select the fresh SATA control and NVMe condition; the older automatic-residency measurements remain separate. Select **Prefill speed** and **Decode speed** for separate charts. Change **Simultaneous queries** to inspect either client count. **Table** retains individual repeats and request timings. **Export both conditions CSV** downloads all 144 raw observations.

The [aggregate CSV](../results/aggregates/rtx-5070-ti-storage.csv) contains all 36 model/size/client-count groups: two repeats, medians and ranges for prefill, decode and wall time, actual token counts, case limits and drafting observations.

## Inference results

Completion speedup is the median of matched SATA/NVMe case wall-time ratios over six prompt targets, both client counts and two repeats. It describes this workload mix, rather than aggregate tokens per second.

| Model / quant | Median completion speedup | Matched cases | SATA passed | NVMe passed |
| --- | ---: | ---: | ---: | ---: |
| Flash-Next UD-IQ4_XS | 1.842x | 24 | 24/24 | 24/24 |
| Flash-Next IQ3_S | 0.998x | 24 | 24/24 | 24/24 |
| Qwen3.6 35B A3B UD-Q4_K_XL | 1.006x | 24 | 24/24 | 24/24 |

NVMe substantially improved UD-IQ4_XS in this configuration. IQ3_S and Qwen3.6 were essentially unchanged. Faster sequential storage does not imply the same inference gain for models with different residency and caching behavior.

### 80k target, one client

Values are medians of two successful repeats. Prefill and decode use client timings; wall time covers the completed request.

| Model / quant | SATA prefill / decode (tok/s) | NVMe prefill / decode (tok/s) | SATA wall (s) | NVMe wall (s) |
| --- | ---: | ---: | ---: | ---: |
| Flash-Next UD-IQ4_XS | 262.0 / 9.8 | 555.1 / 11.7 | 329.12 | 163.04 |
| Flash-Next IQ3_S | 1,440.0 / 22.7 | 1,457.0 / 22.5 | 64.35 | 62.74 |
| Qwen3.6 35B A3B UD-Q4_K_XL | 1,059.4 / 12.0 | 1,075.1 / 12.3 | 94.71 | 92.92 |

### 80k target, two clients

One backend slot queues the two clients. Prefill uses each case's median per-request rate, then the median across repeats. It includes queue delay in time to first token and must not be read as isolated engine prefill throughput. Wall time covers both completed requests.

| Model / quant | SATA prefill / decode (tok/s) | NVMe prefill / decode (tok/s) | SATA wall (s) | NVMe wall (s) |
| --- | ---: | ---: | ---: | ---: |
| Flash-Next UD-IQ4_XS | 191.7 / 9.8 | 379.2 / 11.6 | 660.75 | 345.45 |
| Flash-Next IQ3_S | 1,053.9 / 24.0 | 1,056.6 / 22.5 | 126.99 | 129.45 |
| Qwen3.6 35B A3B UD-Q4_K_XL | 777.0 / 12.3 | 776.9 / 12.4 | 183.53 | 184.05 |

## Experimental controls

- Three models, two storage volumes, targets 512/5k/10k/20k/40k/80k, one/two clients and two repeats: 144 observations. Engine context was verified at 98,304 tokens before every condition. Output limit was 256; actual lengths can be shorter and are retained. Reasoning effort was requested as `none`, with temperature zero. Effective reasoning remains unverified where the API does not report it.
- The initial UD-IQ4_XS SATA run used a 600-second case limit. Its paired 80k case timed out. Both paired 80k repeats subsequently completed with 900 seconds allowed; earlier successful rows were retained. The failed attempt, initial telemetry and extension sessions remain recorded. Subsequent storage conditions used 900 seconds without skipping remaining cases after timeout.
- Strata engine 0.1.52, the same model packages and a shared static Flash expert profile were used. Prior full copy verification and current size/completion-marker checks are recorded separately; weights were not rehashed during inference.
- Each pair was checked for equal **effective** expert placement. Automatic free-VRAM sizing initially differed. Explicit cache budgets or a supported native memory budget corrected that before measurement. Exact settings and rejected setup attempts are retained.

| Model | Verified GPU experts on both drives | Verified host placement | File tier / KV |
| --- | --- | --- | --- |
| Flash-Next UD-IQ4_XS | 2,343 cached; 5.27 GiB | 40 GiB locked; 0 GiB CUDA-pinned after pageable fallback | Unbuffered file tier; INT8 KV |
| Flash-Next IQ3_S | 3,116 cached; 5.97 GiB | 40 GiB locked; 0 GiB CUDA-pinned after pageable fallback | Through OS file cache; INT8 KV |
| Qwen3.6 UD-Q4_K_XL | 20 per layer; 1.46 GiB | All 18.75 GiB pinned; 1.10 GiB GPU staging | KV dtype unreported |

Qwen3.6 requested a 3.0 GiB expert budget on SATA and 2.60 GiB on NVMe to obtain the same 20-expert placement. Flash NVMe cache budgets were fixed to the SATA-selected counts. These temporary preset changes were restored after tests. OS standby caches were not forcibly flushed. Startup observations are not controlled cold-start measurements. Two repeats do not establish statistical significance or universal performance.

### Requested MTP versus observed drafting

MTP was requested and loaded for all three models. Native logs confirm draft attempts for both Flash variants on both drives. For Qwen3.6, **both conditions offered zero drafts** across 48 completed native request log rows each, including warmups. Its acceptance rate is undefined, rather than zero percent.

Inspection of the installed server identifies an inherited presence penalty of 1.5. The reviewed native Qwen source disables speculation with nonzero penalties, which explains the observed zero-draft path; source/binary equivalence was not independently established. Source hashes and this inference are retained in the JSON. A future penalty-neutral request would be a separate sampling condition; these measurements must not be relabeled as active Qwen MTP measurements. Flash counters can include suffix drafts and do not isolate MTP alone.

## Transfer measurements

All rates use decimal GB/s. Each 5070 Ti pass contains three timed samples per path; the table reports the median of three independent pass medians and their range. These captures preceded the inference storage experiment.

| Transfer path | RTX 5060 Ti reference | RTX 5070 Ti median | 5070 Ti pass range |
| --- | ---: | ---: | ---: |
| Samsung 870 EVO SATA to RAM | 0.540 | 0.538 | 0.535–0.540 |
| AirDisk NVMe to RAM | 2.702 | 2.684 | 2.662–2.753 |
| Pinned RAM to VRAM | 6.582 | 13.434 | 13.410–13.435 |
| RAM copy, one worker | 9.929 | 8.530 | 8.466–8.533 |
| RAM copy, six workers | 20.634 | 22.011 | 21.299–22.164 |

The 5060 Ti reference uses its independent 02:57 UTC transfer capture, except NVMe, which uses its 11:05 UTC saved profile. The 5070 Ti captures are timestamped 19:16–19:18 UTC on 1 November 2026. RAM copy variation cannot be attributed to the GPU change alone.

Pinned host-to-device throughput is 2.04 times the 5060 Ti reference. A separate 15-sample diagnostic measured 13.409 GB/s and observed PCIe 3 x16 under transfer; idle observations included PCIe 1 x16. The 5060 Ti condition was PCIe 3 x8. Both used the i7-5820K platform, driver 617.42 and 64 GiB installed RAM.

Storage reads bypass the OS page cache using a flushed 256 MiB temporary file, 8 MiB blocks and queue depth one. Device caches can contribute. RAM copy counts copied payload, rather than total DRAM bus traffic. See [measurement methods](measurements.md).

### Earlier 5060 Ti reference at 80k, one client

Both GPU records contain the same 312 historical workload keys across 13 models. The following comparison uses successful controlled-storage repeats, rather than the older 5070 Ti automatic-residency rows. Different expert counts, PCIe widths and measurement times prevent attribution to GPU compute alone; Qwen3.6 had 21 GPU experts per layer on the 5060 Ti and 20 here.

| Model / quant | SATA wall: 5060 → 5070 (s) | NVMe wall: 5060 → 5070 (s) |
| --- | ---: | ---: |
| Flash-Next UD-IQ4_XS | 324.16 → 329.12 | 177.77 → 163.04 |
| Flash-Next IQ3_S | 115.54 → 64.35 | 113.71 → 62.74 |
| Qwen3.6 35B A3B UD-Q4_K_XL | 188.58 → 94.71 | 189.92 → 92.92 |

See the [5060 Ti experiment](5060-ti-storage-comparison.md) for its settings and censored 600-second paired workload.

## Memory, inventory and telemetry

Firmware reports four populated channels through explicit SMBIOS channel/slot labels, four 16 GiB DIMMs and a configured rate of 2133 MT/s. Active controller interleaving is **unverified** for this follow-up. A fresh [CPU-Z](https://www.cpuid.com/softwares/cpu-z.html) controller report was prepared, but Windows rejected normal execution with error 740, requiring administrator consent. The request remains pending. The older 5060 Ti CPU-Z quad-channel report remains historical evidence.

Storage detection confirms Samsung SSD 870 EVO 4TB on SATA and AirDisk 256GB SSD on NVMe. They are recorded in later inventory annotations and transfer snapshots, rather than retroactively asserted for every historical inference session. NVMe sequential reads were approximately 4.99 times the SATA rate.

OS-reported usable RAM differs by 4096 bytes from the original snapshot after reboot. Installed capacity, module count, clocks, CPU/core counts, board, OS and GPU/driver match. Transfer annotations retain this evidence without replacing the original hardware classification.

Whole-run telemetry retains CPU/GPU utilization, RAM/VRAM usage, GPU temperature and clock history, coverage and early/late summaries for all six conditions. Initial SATA timeout telemetry is retained separately from its continuation. Observed peak GPU temperature was 65°C; peak RAM usage was 96.3% during IQ3_S. CPU temperature was unavailable from the active collector. Samples include warmups and other applications; they do not establish thermal throttling or guaranteed sustained performance.

Strata Control owned every model lifecycle operation. All test models and its API were confirmed stopped afterward; desktop-only loading remains enabled.
