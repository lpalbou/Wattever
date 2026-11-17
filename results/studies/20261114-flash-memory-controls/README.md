# Flash-Next expert RAM comparisons: 2026-11-14

The [two-5070 report](../../../docs/two-5070-ti-comparison.md#flash-next-expert-ram-controls-2026-11-14)
contains the full timing tables, settings and interpretation.

- [40,960-context JSON](context40960/summary.json) and [CSV](context40960/summary.csv):
  Auto versus 0% file-backed experts, 20k/40k prompt targets, three requests per
  policy/target, 128 output tokens, native phase timings and client latency,
  physical disk counters, RAM, retrieval and MTP observations.
- [131,072-context JSON](context131072/summary.json) and [CSV](context131072/summary.csv):
  separate conservative/aggressive expert-RAM allowance screen, 5k/40k/80k/120k
  targets, three requests per policy/target and up to 256 output tokens.
- [Checksum manifest](manifest.json): SHA-256 and byte sizes of these saved
  summaries. Files are unchanged copies of the local measurement summaries;
  local path strings identify the captured configuration, not portable setup instructions.

These are distinct cohorts, not extra repeats of the historical normal benchmark
or of each other. Statistical cells are median [minimum–maximum], not confidence
intervals. Both studies use the same UD-IQ4_XS package on two RTX 5070 Ti cards,
i7-5960X and 64 GiB RAM, with model files on the Samsung 990 Pro. The 0% expert
budget disables only the explicit expert copy; Windows file caching can still
use most RAM. Physical-drive reads and logical engine reads are separate counters.
No uncached-storage performance claim follows from these warm observations.

The snapshots retain their original provenance/cleanup notes. They do not add a
new inference run or change the serving configuration. See the
[GLM report](../../../docs/glm53-flash-comparison.md) for its separately measured
Maya runtime, quantization, host-KV and storage conditions.

## Publication note

Before publication, the Windows account name in recorded file paths was replaced with `user`.
No measurement was altered. The SHA-256 values of the edited files, and every
reference to them in this folder and its manifest, were recomputed afterwards.
