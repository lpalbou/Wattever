## Driver-aware RAM planning and completed tuning results

Updated 2026-11-14T16:14:38+00:00.

**Model:** GLM-5.3-Flash, approximately 321B total / 18B active parameters; Maya-XS mixed IQ1_S/IQ2_XXS quantization (`glm-5.3-flash-maya-xs-iq1_s`). This is the Flash model. XS GGUF shards total 73,411,389,088 bytes (68.37 GiB); routed expert payload is 62.74 GiB. Exact source hashes and mixed tensor types are preserved in the evidence.

**Hardware:** i7-5960X, 64 GiB DDR4-2133, two 16 GiB RTX 5070 Ti cards, Windows 10, Samsung 990 PRO NVMe on D:. All starts and unloads use Strata Control; benchmark clients only send HTTP requests. Downloads pause during measured groups.

**Protocol:** MTP on; INT8 streamed KV; one slot; no expert skipping; temperature 0, top-p 1, seed 20261114, reasoning none. Output caps are listed per cohort. Each size has one excluded warm-up and three measured requests. Native prefill/decode phases, client TTFT, and client end-to-end wall time have separate columns. Values are median [minimum-maximum]. Substantial cached prompt reuse is rejected.

The opt-in native WDDM cap queries fresh driver budgets for the selected GPUs before pinning experts. It caps plans before allocation and avoids destructive capacity probes when the driver query succeeds. The 40 GiB requested direct-I/O configuration reached ready in 57.04 s with about 26.62 GiB applied, 62.3% peak physical RAM usage and 24.07 GiB minimum available RAM. This validates startup for that measured configuration; inference results are listed separately. The cap uses equipment and live driver data, rather than a fixed 26 GiB machine allowance.

| Condition | Quant | Reserved context | Output cap | Actual prompt tk | Output tk | Cached tk | Prefill s | Prefill tk/s | Decode s | Decode tk/s | Client TTFT s | Client wall s |
|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|---|
| C1 | XS | 131,072 | 512 | 5,001-5,001 | 138-325 | 0 | 10.27 [9.63-10.54] | 486.70 [474.70-519.40] | 9.32 [9.14-21.06] | 15.40 [14.80-15.40] | 10.32 [9.65-10.56] | 19.71 [18.98-31.38] |
| C1 | XS | 131,072 | 512 | 20,000-20,003 | 143-155 | 0 | 27.31 [27.22-27.36] | 732.40 [730.90-734.90] | 9.86 [8.75-10.11] | 15.30 [15.00-16.30] | 27.39 [27.29-27.44] | 37.31 [36.05-37.50] |
| C1 | XS | 131,072 | 512 | 79,999-80,000 | 194-207 | 0 | 80.02 [79.38-80.11] | 999.80 [998.70-1007.90] | 11.81 [11.01-12.88] | 16.80 [16.10-17.60] | 80.21 [79.60-80.28] | 92.03 [91.30-92.48] |
| C2 | XS | 131,072 | 128 | 5,001-5,001 | 128-128 | 0 | 11.56 [10.47-11.56] | 432.60 [432.60-477.50] | 7.55 [7.22-7.59] | 17.00 [16.90-17.70] | 11.60 [10.52-11.60] | 18.81 [18.10-19.15] |
| C2 | XS | 131,072 | 128 | 20,000-20,003 | 128-128 | 0 | 25.74 [24.62-25.75] | 777.10 [776.80-812.40] | 7.45 [7.25-7.79] | 17.20 [16.40-17.70] | 25.82 [24.69-25.82] | 33.27 [31.94-33.61] |
| C3 | XS | 131,072 | 512 | 5,001-5,001 | 142-164 | 0 | 9.41 [9.34-9.49] | 531.40 [527.10-535.60] | 9.29 [9.22-9.58] | 15.80 [15.40-17.10] | 9.46 [9.36-9.54] | 18.76 [18.66-19.04] |
| C3 | XS | 131,072 | 512 | 20,000-20,003 | 128-152 | 0 | 22.27 [22.10-22.28] | 898.00 [897.70-905.00] | 8.01 [7.65-8.90] | 16.70 [16.60-17.10] | 22.33 [22.19-22.35] | 30.34 [29.83-31.25] |
| C3 | XS | 131,072 | 512 | 39,998-40,004 | 150-168 | 0 | 38.72 [38.59-39.03] | 1033.00 [1025.00-1036.40] | 9.55 [9.03-10.16] | 16.60 [16.50-17.40] | 38.85 [38.70-39.18] | 48.40 [48.20-48.86] |
| C3 | XS | 131,072 | 512 | 79,999-80,000 | 189-215 | 0 | 72.65 [72.56-72.91] | 1101.20 [1097.30-1102.60] | 11.25 [11.06-12.91] | 17.10 [16.70-18.10] | 72.84 [72.78-73.09] | 84.03 [83.90-86.00] |
| C3 | XS | 131,072 | 512 | 119,999-120,000 | 137-168 | 0 | 97.30 [96.92-97.66] | 1233.40 [1228.70-1238.10] | 8.87 [7.07-9.02] | 18.90 [18.10-19.40] | 97.53 [97.20-97.92] | 106.07 [104.99-106.55] |
| C4 | XS | 131,072 | 512 | 5,001-5,001 | 146-167 | 0 | 9.33 [9.19-9.36] | 535.90 [534.50-544.10] | 10.19 [9.91-12.33] | 14.30 [13.50-15.30] | 9.36 [9.23-9.40] | 19.54 [19.14-21.73] |
| C4 | XS | 131,072 | 512 | 20,000-20,003 | 131-154 | 0 | 22.16 [22.11-22.40] | 902.80 [892.90-904.70] | 9.56 [8.94-9.91] | 14.70 [14.30-16.10] | 22.18 [22.16-22.43] | 31.99 [31.13-32.07] |
| C5 | XS | 131,072 | 512 | 5,001-5,001 | 158-179 | 0 | 9.20 [9.12-9.29] | 543.90 [538.40-548.60] | 10.44 [9.95-10.86] | 16.30 [15.90-16.50] | 9.23 [9.14-9.32] | 19.77 [19.18-20.00] |
| C5 | XS | 131,072 | 512 | 20,000-20,003 | 128-150 | 0 | 22.05 [21.90-22.38] | 907.20 [893.60-913.20] | 8.29 [7.44-8.92] | 16.90 [16.80-17.20] | 22.12 [21.93-22.42] | 30.41 [29.86-30.85] |
| C6 | XS | 131,072 | 512 | 39,998-40,004 | 148-164 | 0 | 37.91 [37.74-38.09] | 1055.20 [1050.00-1060.10] | 7.81 [7.69-7.99] | 19.20 [19.10-20.50] | 38.02 [37.84-38.20] | 45.84 [45.83-45.90] |
| C6 | XS | 131,072 | 512 | 119,999-120,000 | 164-512 | 0 | 95.82 [95.57-96.12] | 1252.30 [1248.50-1255.60] | 11.47 [8.48-27.78] | 18.40 [18.20-19.30] | 96.13 [95.84-96.43] | 107.31 [104.61-124.22] |
| C7 | XS | 131,072 | 512 | 5,001-5,001 | 144-202 | 0 | 8.83 [8.77-8.95] | 566.30 [558.80-570.40] | 8.22 [7.84-11.78] | 18.40 [17.10-18.70] | 8.87 [8.81-8.98] | 17.19 [16.66-20.66] |
| C7 | XS | 131,072 | 512 | 20,000-20,003 | 147-159 | 0 | 21.31 [21.20-21.37] | 938.50 [935.90-943.40] | 8.77 [8.71-9.02] | 17.50 [16.80-18.30] | 21.39 [21.25-21.47] | 30.24 [30.10-30.28] |
| C7 | XS | 131,072 | 512 | 79,999-80,000 | 186-231 | 0 | 71.26 [71.21-71.37] | 1122.60 [1120.90-1123.40] | 9.89 [9.61-11.92] | 19.40 [19.40-19.80] | 71.45 [71.43-71.57] | 81.32 [81.18-83.38] |
| C8 | XS | 200,000 | 512 | 79,999-80,000 | 192-192 | 0 | 72.32 [72.18-72.60] | 1106.30 [1101.90-1108.40] | 10.70 [10.35-11.46] | 17.90 [16.70-18.50] | 72.51 [72.34-72.79] | 83.21 [83.14-83.81] |
| C8 | XS | 200,000 | 512 | 189,999-190,000 | 146-233 | 0 | 146.92 [146.88-147.47] | 1293.30 [1288.40-1293.50] | 8.03 [7.12-12.44] | 19.70 [18.70-20.50] | 147.34 [147.32-147.90] | 155.94 [154.45-159.78] |
| C9 | XS | 131,072 | 512 | 20,000-20,003 | 127-207 | 0 | 28.54 [28.08-28.55] | 700.90 [700.50-712.50] | 16.34 [14.56-20.56] | 9.50 [8.70-10.10] | 28.61 [28.12-28.62] | 44.95 [42.68-49.18] |
| C9 | XS | 131,072 | 512 | 39,998-40,004 | 163-177 | 0 | 46.04 [46.02-46.37] | 868.70 [862.60-869.30] | 17.25 [17.19-17.69] | 9.90 [9.50-10.00] | 46.14 [46.14-46.54] | 63.39 [63.33-64.23] |
| C10 | XS | 131,072 | 512 | 5,001-5,001 | 156-175 | 0 | 10.23 [9.74-10.23] | 489.00 [488.90-513.70] | 10.76 [9.59-10.90] | 16.10 [15.10-16.30] | 10.27 [9.76-10.28] | 20.52 [19.86-21.18] |
| C10 | XS | 131,072 | 512 | 20,000-20,003 | 144-176 | 0 | 22.76 [22.70-22.81] | 878.70 [877.00-881.10] | 9.96 [9.72-11.22] | 15.70 [14.80-16.00] | 22.81 [22.78-22.86] | 32.74 [32.54-34.08] |
| C11 | XS | 98,304 | 128 | 5,001-5,001 | 128-128 | 0 | 9.46 [9.41-9.52] | 528.40 [525.10-531.50] | 7.86 [7.66-7.92] | 16.30 [16.20-16.70] | 9.50 [9.45-9.55] | 17.41 [17.11-17.42] |
| C12 | XS | 98,304 | 128 | 5,001-5,001 | 128-128 | 0 | 9.45 [9.43-9.48] | 529.10 [527.30-530.30] | 7.84 [7.57-7.92] | 16.30 [16.20-16.90] | 9.49 [9.46-9.51] | 17.31 [17.08-17.41] |
| C12 | XS | 98,304 | 128 | 20,000-20,003 | 128-128 | 0 | 21.90 [21.86-22.22] | 913.10 [900.10-915.00] | 7.59 [6.93-8.21] | 16.90 [15.60-18.50] | 21.98 [21.93-22.27] | 29.52 [28.91-30.49] |
| C13 | XS | 98,304 | 128 | 5,001-5,001 | 128-128 | 0 | 9.30 [9.26-9.47] | 538.00 [528.00-539.90] | 7.78 [7.58-7.89] | 16.50 [16.20-16.90] | 9.33 [9.28-9.49] | 17.11 [17.07-17.17] |
| C13 | XS | 98,304 | 128 | 20,000-20,003 | 122-128 | 0 | 22.09 [21.50-22.30] | 905.50 [897.20-930.40] | 7.29 [7.07-7.58] | 17.60 [16.10-18.10] | 22.12 [21.56-22.32] | 29.41 [28.63-29.90] |
| C14 | XS | 98,304 | 128 | 5,001-5,001 | 128-128 | 0 | 8.21 [8.19-8.25] | 609.00 [606.50-610.60] | 7.64 [7.46-7.68] | 16.80 [16.70-17.20] | 8.51 [8.41-8.52] | 16.04 [15.97-16.20] |
| C14 | XS | 98,304 | 128 | 20,000-20,003 | 128-128 | 0 | 24.11 [24.09-24.23] | 829.50 [825.60-830.40] | 7.93 [7.32-8.18] | 16.10 [15.70-17.50] | 24.46 [24.45-24.57] | 32.39 [31.78-32.75] |

![Native prefill and decode versus actual prompt length](../results/studies/20261114-glm53-optimization/glm53-context-throughput.png)

[Standalone SVG](../results/studies/20261114-glm53-optimization/glm53-context-throughput.svg) and [CSV medians/ranges](../results/studies/20261114-glm53-optimization/completed-tuning.csv). The figure selects comparable 131,072-context cohorts; it does not combine accuracy-only timings.

### Configuration and memory observations

`cap1-direct-ram40` requests 40 GiB with unbuffered I/O. `bufdirect1-ram999` requests 99.9% of fresh available headroom after the existing 12 GiB guard, uses exact buffered reads, and has no saved conversation disk slots. The driver cap can reduce the requested allowance. `rd1` and `rd4` are per-request decode read-chunk settings; prefill retains its startup chunk setting. `auto`/`pcie1` in the label denotes request PCIe routing. These strategic cohorts differ in more than one knob unless explicitly stated; small differences with overlapping ranges are not established gains.

| Run | Measured requests | Retrieval records correct | Peak RAM % | Min available GiB | Max output gap s | Max heavy page-out interval s |
|---|---:|---:|---:|---:|---:|---:|
| C1 | 9 | 45 | 70.70 | 18.73 | 0.198 | 3.593 |
| C2 | 6 | 30 | 71.70 | 18.10 | 0.167 | 0.000 |
| C3 | 15 | 75 | 71.90 | 17.99 | 0.181 | 2.081 |
| C4 | 6 | 30 | 68.30 | 20.24 | 0.213 | 1.530 |
| C5 | 6 | 30 | 69.20 | 19.67 | 0.170 | 0.000 |
| C6 | 6 | 30 | 73.10 | 17.20 | 0.156 | 0.000 |
| C7 | 9 | 45 | 72.10 | 17.81 | 0.166 | 0.000 |
| C8 | 6 | 30 | 72.30 | 17.71 | 0.165 | 0.000 |
| C9 | 6 | 30 | 31.10 | 44.07 | 0.300 | 0.000 |
| C10 | 6 | 30 | 67.60 | 20.73 | 0.188 | 0.000 |
| C11 | 3 | 15 | 67.30 | 20.92 | 0.175 | 0.000 |
| C12 | 6 | 30 | 71.30 | 18.32 | 0.173 | 0.000 |
| C13 | 6 | 30 | 69.50 | 19.49 | 0.186 | 0.000 |
| C14 | 6 | 30 | 62.80 | 23.76 | 0.181 | 0.000 |

| Condition | Raw cohort | Requested RAM | Native expert RAM GiB | Split requested / applied | VRAM reserve MiB | Prefill request | Decode request tune |
|---|---|---|---:|---|---:|---|---|
| C1 | [xs1159-cap1-bufdirect1-auto-ctx131-pp16384-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-pp16384-pcie1-cap512.json) | Auto | 24.2 | auto / 25 | 1500 | 16384 | {'pcie_frac': 1.0} |
| C2 | [xs1159-cap1-bufdirect1-auto-ctx131-pp4096-pcie1](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-pp4096-pcie1.json) | Auto | 27.5 | auto / 24 | 1500 | 4096 | {'pcie_frac': 1.0} |
| C3 | [xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-cap512.json) | Auto | 26.4 | auto / 24 | 1500 | auto | {'pcie_frac': 1.0} |
| C4 | [xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-rd16-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-rd16-cap512.json) | Auto | 26.4 | auto / 24 | 1500 | auto | {'pcie_frac': 1.0, 'read_chunks': 16} |
| C5 | [xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-rd8-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-ppauto-pcie1-rd8-cap512.json) | Auto | 26.4 | auto / 24 | 1500 | auto | {'pcie_frac': 1.0, 'read_chunks': 8} |
| C6 | [xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512-long](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512-long.json) | Auto | 26.4 | auto / 24 | 750 | auto | {'pcie_frac': 1.0} |
| C7 | [xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx131-vram750-pcie1-cap512.json) | Auto | 26.4 | auto / 24 | 750 | auto | {'pcie_frac': 1.0} |
| C8 | [xs1159-cap1-bufdirect1-auto-ctx200-vram750-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-auto-ctx200-vram750-pcie1-cap512.json) | Auto | 26.0 | auto / 25 | 750 | auto | {'pcie_frac': 1.0} |
| C9 | [xs1159-cap1-bufdirect1-ram0-ctx131-ppauto-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-ram0-ctx131-ppauto-pcie1-cap512.json) | 0.0% | 0.4 | auto / 24 | 1500 | auto | {'pcie_frac': 1.0} |
| C10 | [xs1159-cap1-bufdirect1-ram24-ctx131-ppauto-pcie1-cap512](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-ram24-ctx131-ppauto-pcie1-cap512.json) | 24 | 24.0 | auto / 25 | 1500 | auto | {'pcie_frac': 1.0} |
| C11 | [xs1159-cap1-bufdirect1-ram999-rd1-auto](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-ram999-rd1-auto.json) | 99.9% | 26.6 | 25 / 25 | 1500 | auto | {'read_chunks': 1} |
| C12 | [xs1159-cap1-bufdirect1-ram999-rd4-auto](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-ram999-rd4-auto.json) | 99.9% | 26.6 | 25 / 25 | 1500 | auto | {} |
| C13 | [xs1159-cap1-bufdirect1-ram999-rd4-pcie1](../results/studies/20261114-glm53-optimization/xs1159-cap1-bufdirect1-ram999-rd4-pcie1.json) | 99.9% | 26.6 | 25 / 25 | 1500 | auto | {'pcie_frac': 1.0} |
| C14 | [xs1159-cap1-direct-ram40-ctx98](../results/studies/20261114-glm53-optimization/xs1159-cap1-direct-ram40-ctx98.json) | 40 | 26.6 | 25 / 25 | 1500 | auto | {} |

### Native expert-cache and MTP observations

| Condition | Prompt target | VRAM hit % | RAM fetches / output token | Expert-file reads / output token | MTP acceptance % | Physical D: reads GiB | Ledger requests passed | Natural stops | Cap hits |
|---|---:|---|---|---|---|---|---:|---:|---:|
| C1 | 5,000 | 69.48 [69.10-69.96] | 85.25 [84.56-87.33] | 23.11 [20.40-24.57] | 89.70 [89.20-94.40] | 24.62 [22.92-34.35] | 3/3 | 3 | 0 |
| C1 | 20,000 | 71.79 [71.55-72.40] | 82.37 [81.97-82.76] | 21.03 [17.92-21.75] | 85.60 [84.20-86.50] | 50.57 [48.17-50.97] | 3/3 | 3 | 0 |
| C1 | 80,000 | 74.85 [73.61-75.21] | 71.52 [69.94-74.27] | 19.64 [16.56-20.43] | 90.40 [89.30-91.10] | 123.88 [122.56-125.85] | 3/3 | 3 | 0 |
| C2 | 5,000 | 68.86 [68.27-69.70] | 96.34 [94.63-98.51] | 14.31 [11.74-15.25] | 90.50 [89.70-92.10] | 16.44 [12.67-17.21] | 3/3 | 0 | 3 |
| C2 | 20,000 | 69.55 [69.32-70.02] | 96.69 [94.64-99.74] | 12.53 [11.12-15.16] | 84.90 [84.90-89.70] | 15.52 [10.37-16.05] | 3/3 | 0 | 3 |
| C3 | 5,000 | 68.91 [68.64-71.94] | 96.26 [85.76-96.73] | 17.22 [15.73-17.40] | 86.90 [83.60-87.00] | 21.00 [20.27-21.65] | 3/3 | 3 | 0 |
| C3 | 20,000 | 69.59 [69.29-70.69] | 92.27 [91.70-95.67] | 14.70 [13.79-17.12] | 87.30 [85.70-90.10] | 19.53 [18.64-19.91] | 3/3 | 3 | 0 |
| C3 | 40,000 | 70.58 [70.27-70.87] | 88.45 [85.74-91.58] | 16.45 [15.95-17.46] | 87.80 [86.70-93.30] | 20.70 [20.21-21.10] | 3/3 | 3 | 0 |
| C3 | 80,000 | 72.90 [72.61-73.79] | 81.40 [78.44-82.53] | 15.62 [14.30-17.48] | 87.30 [86.60-91.60] | 20.03 [19.88-23.41] | 3/3 | 3 | 0 |
| C3 | 120,000 | 72.68 [72.40-73.92] | 82.91 [80.31-85.13] | 11.35 [11.20-14.48] | 92.20 [91.90-92.60] | 17.80 [15.01-18.54] | 3/3 | 3 | 0 |
| C4 | 5,000 | 70.10 [70.07-71.94] | 90.55 [84.99-92.19] | 17.12 [15.97-17.44] | 87.50 [84.20-88.00] | 19.97 [18.07-20.66] | 3/3 | 3 | 0 |
| C4 | 20,000 | 70.03 [69.42-72.10] | 90.89 [86.10-96.65] | 16.37 [13.99-17.38] | 86.80 [82.10-88.80] | 18.63 [18.13-19.48] | 3/3 | 3 | 0 |
| C5 | 5,000 | 70.62 [70.01-71.40] | 90.74 [87.59-91.89] | 16.54 [16.39-16.58] | 85.90 [85.10-86.50] | 20.33 [20.05-20.69] | 3/3 | 3 | 0 |
| C5 | 20,000 | 70.29 [70.09-70.99] | 92.26 [89.09-95.18] | 14.52 [13.38-16.81] | 85.70 [83.10-89.90] | 18.68 [14.15-19.10] | 3/3 | 3 | 0 |
| C6 | 40,000 | 73.57 [71.61-77.70] | 83.21 [67.93-88.53] | 10.75 [10.55-11.54] | 90.40 [90.10-93.90] | 15.96 [14.52-17.27] | 3/3 | 3 | 0 |
| C6 | 120,000 | 74.41 [73.08-74.76] | 79.57 [76.46-81.10] | 12.20 [12.09-14.35] | 93.20 [85.00-94.90] | 17.71 [15.89-29.86] | 3/3 | 2 | 1 |
| C7 | 5,000 | 71.91 [71.81-72.01] | 86.56 [85.69-86.98] | 13.16 [12.18-14.71] | 90.80 [86.50-94.70] | 18.14 [16.65-19.93] | 3/3 | 3 | 0 |
| C7 | 20,000 | 73.70 [71.54-73.82] | 85.58 [84.63-87.29] | 13.09 [10.59-16.62] | 83.40 [80.80-84.80] | 17.61 [17.30-18.32] | 3/3 | 3 | 0 |
| C7 | 80,000 | 75.93 [74.48-76.40] | 74.23 [70.57-78.32] | 12.08 [11.40-12.32] | 92.90 [88.60-93.30] | 18.09 [14.82-18.45] | 3/3 | 3 | 0 |
| C8 | 80,000 | 74.39 [73.55-74.78] | 78.22 [76.31-82.31] | 14.53 [12.42-14.74] | 88.90 [84.70-91.10] | 19.84 [19.56-20.32] | 3/3 | 3 | 0 |
| C8 | 190,000 | 75.49 [74.26-77.66] | 77.99 [67.84-78.37] | 10.36 [8.95-12.27] | 93.10 [91.00-93.10] | 17.69 [17.57-19.09] | 3/3 | 3 | 0 |
| C9 | 20,000 | 72.02 [71.31-73.88] | 2.21 [2.03-2.24] | 98.59 [93.22-103.80] | 86.30 [83.20-88.90] | 41.08 [38.32-43.59] | 3/3 | 3 | 0 |
| C9 | 40,000 | 73.50 [72.27-73.64] | 1.86 [1.84-1.96] | 93.79 [92.00-98.45] | 88.70 [88.20-91.40] | 41.35 [40.20-42.42] | 3/3 | 3 | 0 |
| C10 | 5,000 | 71.28 [69.97-71.45] | 80.87 [80.61-86.15] | 20.53 [20.23-22.70] | 92.50 [86.90-92.90] | 23.60 [22.55-25.26] | 3/3 | 3 | 0 |
| C10 | 20,000 | 69.89 [69.65-72.32] | 85.01 [76.99-87.75] | 23.24 [22.25-23.52] | 86.80 [83.10-92.40] | 23.24 [22.70-25.53] | 3/3 | 3 | 0 |
| C11 | 5,000 | 69.57 [69.08-70.55] | 81.74 [80.34-82.23] | 13.76 [12.70-16.21] | 88.90 [88.10-88.90] | 18.80 [18.64-19.02] | 3/3 | 0 | 3 |
| C12 | 5,000 | 69.59 [69.34-70.05] | 70.30 [67.95-76.33] | 15.39 [15.09-17.21] | 88.90 [88.10-89.70] | 19.84 [19.33-20.14] | 3/3 | 0 | 3 |
| C12 | 20,000 | 69.81 [69.08-71.74] | 75.11 [71.07-81.20] | 17.02 [14.16-17.38] | 89.70 [79.40-91.30] | 19.55 [18.53-19.79] | 3/3 | 0 | 3 |
| C13 | 5,000 | 68.75 [68.21-69.08] | 95.52 [93.79-96.09] | 15.90 [14.57-19.00] | 90.50 [89.70-91.30] | 18.81 [17.82-18.99] | 3/3 | 0 | 3 |
| C13 | 20,000 | 71.19 [70.51-71.75] | 91.30 [91.07-92.38] | 11.79 [10.54-17.19] | 87.30 [84.20-88.10] | 17.98 [12.61-19.21] | 3/3 | 1 | 2 |
| C14 | 5,000 | 69.83 [69.77-71.75] | 74.62 [71.29-75.70] | 14.00 [13.96-14.65] | 91.30 [88.90-92.10] | 29.23 [28.79-29.67] | 3/3 | 0 | 3 |
| C14 | 20,000 | 70.97 [69.19-71.63] | 75.27 [71.60-76.31] | 15.79 [13.66-16.77] | 91.30 [81.70-92.10] | 72.10 [70.86-72.67] | 3/3 | 0 | 3 |

### Per-GPU readings for C8

Power and utilization are median [range] of each complete request average, covering both prefill and decode. VRAM is the largest sampled reading. GPU indices are physical NVIDIA indices; native CUDA indices follow the configured order.

| Prompt target | Physical GPU | Peak VRAM MiB | Request-average utilization % | Request-average power W |
|---:|---:|---:|---|---|
| 80,000 | 0 | 15594 | 59.89 [59.35-60.85] | 113.43 [113.00-113.69] |
| 80,000 | 1 | 14582 | 70.08 [69.08-70.66] | 117.86 [117.73-118.33] |
| 190,000 | 0 | 15611 | 70.45 [70.08-70.86] | 135.87 [134.51-136.31] |
| 190,000 | 1 | 14582 | 82.43 [81.31-83.04] | 142.90 [141.54-143.31] |

**Output-quality diagnostics:** the separate 131,072-context / Auto-prefill / 128-token-cap matrix stopped during its 119,999-token warm-up: visible analysis consumed the cap before a completed ledger JSON. The cap-512 / 750 MiB-reserve cohort also has a 119,999-token measured request that retrieved all five values, then repeated its answer until the cap. A ledger pass is a retrieval subtask result, not proof of a complete, non-repetitive answer. Natural stops and cap hits are retained separately above. These failures do not demonstrate context overflow or OOM. Low-reasoning correctness checks are separate from the None throughput protocol.


Available RAM includes reclaimable standby cache. File-backed input faults and logical expert-file reads do not alone prove pagefile thrashing. Systemwide physical D: reads, per-GPU power/utilization/VRAM, native cache-hit/read counters and MTP acceptance remain in every request JSON and associated logs.

### Current exact-answer and long-prompt accuracy checks

These are separate accuracy cohorts. Background downloading is allowed; their timing diagnostics are not controlled throughput comparisons. The local screen uses sixteen frozen, strictly graded JSON answers, one attempt each, with a 2,048-token cap. It is a small diagnostic, not an intelligence score. Each raw file includes its complete prepared profile.

| Cohort | Exact model ID | Reasoning | Exact passes | Answer-content passes | Output-cap hits |
|---|---|---|---:|---:|---:|
| xs-best-low2048 | glm-5.3-flash-maya-xs-iq1_s | low | 13/16 | 13 | 0 |
| xs-best-none2048 | glm-5.3-flash-maya-xs-iq1_s | none | 8/16 | 8 | 0 |

| Long-retrieval cohort | Reserved context | Actual input tk | Output tk | Measured requests | Correct records | Natural stops | Cap hits | Repeated ledger answers |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| xs-best-low-long120-cap2048 | 131072 | 119,998-119,999 | 299-338 | 3 | 15 | 3 | 0 | 0 |
| xs-best-low-long490-cap2048 | 500000 | 489,998-489,998 | 338-338 | 1 | 5 | 1 | 0 | 0 |

### Frozen public-question accuracy checks

The public screen requests Low reasoning, greedy sampling and a 2,048-token total output cap, with one attempt per selected question. ARC-Challenge and GSM8K each have 32 questions; MMLU-Pro has a balanced 64-question subset. Selections and suite digests are frozen across quantizations in public-quality-plan.json. Cap-truncated answers count as incorrect. Small-subset scores are descriptive; do not interpret them as full-dataset scores. Only completed suites appear below.

| Cohort | Suite | Correct / planned | Scored | Errors | Truncated | Accuracy |
|---|---|---:|---:|---:|---:|---:|
| xs-best-low-public128 | arc-challenge-test-chat-v1 | 28/32 | 32 | 0 | 0 | 87.50% |
| xs-best-low-public128 | gsm8k-test-chat-v1 | 30/32 | 32 | 0 | 0 | 93.75% |
| xs-best-low-public128 | mmlu-pro-balanced64-chat-v1 | 41/64 | 64 | 0 | 4 | 64.06% |

### Higher-quant comparison status

The S24 mixed IQ2_XXS_S candidate comes from `peasantsmith/GLM-5.3-Flash-Maya-GGUF`, revision `5ca1df1d2436bd1ab270b169c5ea6c47aad23641`: three shards totaling 94,701,676,224 bytes (88.20 GiB). Latest recorded download state: **downloading**, 1/3 verified shards, 76.48/94.70 GB received. It must finish all three checksum verifications and receive its own converted pack before any performance or quality claim. Download completion and a successful load do not establish a better configuration.

[Portable raw evidence and checksum manifest](../results/studies/20261114-glm53-optimization/README.md)

### Quality versus waiting-time comparison: revised roadmap

The user reprioritized practical answer quality, completed-answer latency and minimum-cost improvements. Completed results and ongoing downloads are preserved. The in-flight 490k capacity request is saved before changing models; further large-context grids are deferred.
1. Compare these exact candidates: qwen3.8-flash-next-iq3_s; qwen3.8-flash-next-unsloth-ud-iq4_xs; glm-5.3-flash-maya-xs-iq1_s; and the verified/converted GLM-5.3-Flash Maya-S24. Audit each family’s actual template and backend behavior for thinking disabled and enabled; matching effort strings alone are not evidence of matching modes.
2. Freeze the existing 32 ARC, 32 GSM8K and 64 balanced MMLU-Pro questions as the common 128-question cohort. Add paired executable coding, logical/state-tracking and structured-output tasks. Keep strict formatting, answer content and completed-generation status separate. A small functionality screen cannot establish a winner.
3. Use a common 2,048-token total generation allowance, including reasoning, one attempt per question per condition, deterministic sampling, and paused/completed downloads for timing. Keep the existing background-download accuracy cohort separate. Investigate a larger allowance where truncation changes the conclusion, using a separately frozen complete cohort rather than selectively replacing failed attempts.
4. Measure first visible answer, completed final-answer time, reasoning/output tokens, truncation/repetition, category accuracy, latency ranges/tails, and correct completed answers within 30/60/120 seconds. A partial answer at the deadline does not count.
5. Compare common approximately 20k/80k/120k prompts for distributed retrieval, combining distant evidence, contradictions and missing evidence. Grade final answers; marker presence alone is insufficient. Initially reuse completed representative speed evidence and add warm-up plus three repeats at short/80k only where missing. Expand costly S24/190k work only if quality justifies it.
6. Recommend measured Control presets for daily speed, reasoning quality and long context, with exact runtime/quant/mode/output allowance and requested versus applied GPU/RAM settings. Preserve hardware-based Auto planning and production memory guards.
7. Investigate native Linux feasibility without installing or repartitioning. Price complete used 128–256 GB platforms retaining both cards/NVMe, with delivered cost, fit/power and resale assumptions explicitly verified or left pending. Additional SSDs/expensive GPUs need measured justification; network download improvements remain separate.
8. Publish positive and negative paired results with uncertainty, restore experimental overrides, and unload through Control without automatically loading another model.
