# RTX 5060 Ti: Strata SSD and NVMe comparison

Open **Results**, select **RTX 5060 Ti**, and use the visible **Strata · SSD vs NVMe** panel. It opens at **80k prompt tokens**, showing both query counts. Change **Prompt tokens**, **Queries**, **Measure** or **Model** to inspect the full matrix. Expand **Individual repeats & query timings** for each measured attempt and query. **Export both drives CSV** downloads all 144 baseline/alternative records.

The [benchmark JSON](../results/runs/20261030-211908_rtx-5060-ti_af3694cca3.json) retains 312 primary Samsung cases plus 72 alternative NVMe observations in the same machine entry. `storage_comparison` contains frozen baseline and alternative rows, per-workload medians, a per-model summary, copy verification and comparison settings. Main charts retain Samsung measurements for the GPU comparison.

## Measured inference effect

| Model / quant | Median completion speedup | Matched cases | Samsung passed | NVMe passed |
| --- | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B A3B UD-Q4_K_XL | 1.002x | 24 | 24/24 | 24/24 |
| Flash Next IQ3_S | 1.013x | 24 | 24/24 | 24/24 |
| Flash Next UD-IQ4_XS | 1.745x | 22 | 22/24 | 24/24 |

Speedup is the median Samsung/NVMe wall-time ratio over matching successful cases across all six prompt sizes and both query counts. A timeout is excluded from that ratio. The following 80k values retain both individual repeats; seconds cover completion of all queries in the case.

| Model / quant | Queries | Samsung repeat 1 / 2 (s) | NVMe repeat 1 / 2 (s) |
| --- | ---: | --- | --- |
| Qwen 3.6 35B A3B UD-Q4_K_XL | 1 | 191.28 / 185.88 | 192.46 / 187.37 |
| Qwen 3.6 35B A3B UD-Q4_K_XL | 2 | 368.98 / 371.94 | 370.73 / 370.85 |
| Flash Next IQ3_S | 1 | 115.42 / 115.67 | 114.16 / 113.26 |
| Flash Next IQ3_S | 2 | 225.89 / 227.68 | 226.04 / 226.79 |
| Flash Next UD-IQ4_XS | 1 | 323.77 / 324.55 | 179.20 / 176.34 |
| Flash Next UD-IQ4_XS | 2 | timeout at 600s / skipped | 353.48 / 350.29 |

UD-IQ4_XS benefits substantially from NVMe, including successful 80k paired workloads. IQ3_S and Qwen 3.6 inference timings are essentially unchanged. The UD-IQ4_XS observation includes physical-drive I/O samples during inference; a faster sequential disk rate alone does not establish an inference benefit.

## Machine and transfer measurements

| Component / path | Recorded condition or median rate |
| --- | --- |
| GPU | RTX 5060 Ti 16 GB; PCIe 3 x8 under transfer |
| CPU / RAM | Intel i7-5820K; 64 GB; firmware reports 2133 MT/s; BIOS unchanged |
| Samsung SSD 870 EVO SATA to RAM | 0.54 GB/s |
| AirDisk NVMe (F:) to RAM | 2.82 GB/s |
| RAM copy, one / six workers | 9.93 / 20.63 GB/s copied payload |
| Pinned RAM to VRAM | 6.58 GB/s |

The sequential NVMe read probe is 5.23x faster. RAM copy payload throughput is not peak DRAM bus bandwidth. All 78 copied files (195.7 GiB) passed source-stream and destination-readback SHA256 checks; Samsung originals remain available.

## Comparison conditions

- Six targets: 512, 5k, 10k, 20k, 40k and 80k; one/two simultaneous clients; two repeats; 256 output-token limit; 600-second case limit. Actual input/output counts remain in the raw query records.
- Same model files, quantizations, 98,304-token engine context and Strata 0.1.52. MTP is requested/reported on; effective execution remains unknown where native counters do not establish it. One backend slot queues the two clients.
- Flash Next uses the same 40 GiB host expert-cache cap on both drives, with observed pageable fallback after Windows refused the pinned allocation. The 5070 Ti benchmark used automatic host residency, so its Strata memory policy differs.
- Native Qwen 3.6 matches 21 GPU experts per layer and 18.75 GiB pinned host residency. The requested GPU budget is 3 GiB on Samsung and 2.65 GiB on NVMe to preserve that effective placement.
- OS standby caches were not forcibly flushed. Model-loading observations are not controlled cold-start timings. Two repeats describe these workloads; they do not establish universal speedup or statistical significance.

See [measurement definitions](measurements.md) for client timing, concurrency and cache boundaries.
