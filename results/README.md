# Benchmark sessions

This guide describes throughput files in `results/runs/`. Rule-scored capability
files are a separate result family under `results/capabilities/`; see
[capability evaluation](../docs/capabilities.md) for their schema, datasets,
grading, public identities and independent audit import.

One benchmark is one JSON file in `runs/`. A session contains every model,
prompt size, concurrency level, repeat and failure measured for one machine
configuration. Changing cards or runtime GPU placement means starting a new
session. Historical unversioned files remain readable without rewriting them.

Recorded storage experiments may add frozen `storage_comparison.baseline_results`
and `alternate_results` to the same machine file. A fresh control stays separate
from older measurements that used different residency settings. The derived
[5070 Ti aggregate CSV](aggregates/rtx-5070-ti-storage.csv) summarizes 36 workload
groups; its [experiment report](../docs/5070-ti-hardware-follow-up.md) explains
client timing, queueing, cache controls and the retained timeout. Raw measurements
and provenance remain in the benchmark JSON.

The [PNY/ASUS/5060 Ti comparison](../docs/pny-5070-ti-comparison.md) adds a
one-repeat PNY primary matrix of 156 cases and a
[three-card aggregate CSV](aggregates/pny-asus-5070-ti-5060-ti.csv). Its cancelled
storage follow-up is preserved separately and excluded from primary statistics.

The [5960X CPU comparison](../docs/5960x-cpu-comparison.md) records 156 cases
across the 13 established models, all matched to the previous PNY/5820K run. Its
[case CSV](aggregates/pny-5820k-vs-5960x.csv) retains both outcomes, token-size
qualifications, measured GPU energy and separate system estimates.

The [5070 Ti plus 5060 Ti report](../docs/5070-plus-5060-comparison.md) compares
matched and supplemental LM Studio configurations with individual and combined
board energy. Its [case CSV](aggregates/5070-plus-5060-comparison.csv) and
[five-configuration catalog](aggregates/5070-plus-5060-all-configurations.csv)
preserve outcomes, token-matching qualifications and the declared power boundaries.
The report states which native Strata grids remain unmeasured.

The [dual RTX 5070 Ti dataset](runs/20261103-100532_rtx-5070-ti_0a91ddc850.json)
contains 144 LM Studio and 36 native Strata cases, with 179 successes and one
native Qwen35 timeout. One measured repeat per condition is retained. The
[two-card report](../docs/two-5070-ti-comparison.md) provides paired tables,
per-model prefill/decode/energy graphs and explicit timing and residency limits.
There are 13 shared model IDs against the single-PNY setup. The first five
configuration files retain their historical measurements. Transfer probes,
execution-strategy tests and profiled captures use separate diagnostic datasets.

## Run and contribute

1. Launch Wattever, select a provider and connect. Generic endpoints support
   discovery or manual served model IDs. Managed LM Studio also supports **Reset
   for all models** to select downloaded models and restore standard settings.
2. Give the session a useful label, such as `RTX 5060 Ti - stock`. Choose prompt
   sizes and concurrent request counts. Default concurrency is one; click **10**
   to select all levels from one through ten. Prompt-length badges work the same way. Review the total request count.
3. With multiple cards, configure placement in the inference engine and describe it in the
   **Runtime GPU placement** field. **Primary GPU** identifies the benchmark and
   does not select inference placement or restrict all-card NVIDIA monitoring. For generic
   endpoints, explicitly assert local inference before attributing client power.
4. Run the benchmark. Failures and partial sessions remain useful data.
5. Download **JSON** in Results, or take the matching file from `results/runs/`.
   Review it for details you do not want to publish.
6. Follow the [sharing guide](../docs/sharing-results.md) to upload the file and
   propose a pull request. Validation runs on the contribution and the shared
   catalog updates after merge. No index or aggregate file changes are needed.

## Standard format

New files use `schema_version: 2`. See [benchmark.schema.json](benchmark.schema.json).
Fields are additive; the existing `gpu`, `host`, `config` and `results` fields remain.
Additive inventory/run metadata uses `metadata_schema_version: 1`; old files
without it remain readable. Unknown data is not inferred from a model or GPU name.

| Field | Meaning |
| --- | --- |
| `application` | Tool version and measurement protocol version |
| `hardware.os` | OS name, release and architecture |
| `hardware.cpu` | CPU name, physical and logical core counts |
| `hardware.memory` | RAM module capacity, reported clocks and provenance where available; channel count/bandwidth stay unknown unless available |
| `hardware.system` | Manufacturer/model and physical RAM in bytes |
| `hardware.gpus` | Separate entries for every discoverable adapter; name, vendor, memory in bytes, driver and optional sensor index |
| `hardware.motherboard`, `hardware.firmware`, `hardware.storage` | Best-effort board model, firmware vendor/version and physical disk models/capacity/interface |
| `classification` | Hardware configuration grouping hash, GPU names and platform; not a unique machine identity |
| `measurement_scope` | Declared power boundary, monitored sensor indices and runtime placement |
| `provider` | Public provider label, managed/external model lifecycle and control boundaries |
| `measurement_client` | Client inventory kept separate when remote inference hardware is unverified |
| `reproducibility` | User-reported engine/version, model revision, quantization, actual context/KV and MTP draft settings |
| `performance_claims` | Sourced, user-reported compute claims with precision, execution path, sparsity and peak/measured/estimate classification |
| `model_configuration`, `configuration_id` | Public model/endpoint settings with requested/reported/verified/unknown provenance and a content-consistency fingerprint |
| `telemetry.history`, `telemetry.summary` | Bounded recent time series plus whole-run observed means/peaks/coverage and descriptive drift |
| `corpus.sha256` | Corpus fingerprint for comparing the exact workload |
| `config` | Selected models, prompt targets, concurrency levels and measurement settings |
| `results` | Measurements, failures, skips and cancellation records |

Unknown metadata is `null` or an empty collection. Hardware discovery is best
effort; an empty adapter list does not prove that the machine has no GPU.
New files omit GPU UUIDs, serial numbers, usernames and hostnames. User-entered
notes and runtime/model names are included; review them before sharing. Historical
files may still contain a GPU UUID; it may be removed without changing measurements.
Public outputs exclude endpoint URL, credential environment-variable name and
credentials. Review all free text, including labels, reproduction fields and
compute sources. Hardware describes the benchmark client, not verified remote
inference hardware. PCIe encoding ceilings are theoretical, not measured DMA or
device-memory bandwidth, and idle snapshots may downshift. Floating compute
claims use TFLOP/s; integer precision uses TOPS. See [measurement definitions](../docs/measurements.md).
Public endpoint IDs/labels identify routes without connection URLs. Configuration
hashes do not authenticate actual served weights. Recent telemetry can drop old
points while whole-run aggregates retain observations; drift alone does not prove
thermal throttling. Missing temperature or utilization is unavailable, not zero.

## Concurrent measurements

Each result row includes `concurrency`; missing means one in historical files.
Single-request prefill/decode retain their original definitions. Groups above
one contain `requests`, including per-request status, TTFT and decode speed.
Each concurrent request has its own leading nonce to invalidate prefix caching.
In managed LM Studio, `lms load --parallel` sets prediction capacity for that
level. Generic endpoint concurrency and context capacity are engine controlled.

`aggregate_completion_tps` is successful requests' total output tokens divided
by group wall time, including prefill and queueing. It is not the sum of individual
decode speeds. A group is successful only when every request succeeds. Generic
endpoint throughput stays unavailable if server token usage is missing. Legacy
managed results can include explicitly marked stream-chunk estimates; the UI
marks estimated counts and derived throughput with `*`. Client prompt tokens/TTFT
is a prefill proxy including overhead, distinct from optional `reported_timings`.
Missing `cached_prompt_tokens` means unknown cache usage, not zero.
`timing_scope` distinguishes streamed content/reasoning from hidden reasoning and
`decode_token_count` records the scoped decode numerator. Reported unstreamed
reasoning is excluded; ambiguous hidden work can leave decode unavailable.

`group_power` covers the group interval. Concurrent phases overlap, so they do
not get invented prefill/decode energy splits. New NVIDIA helper runs record
every detected card in `gpus` and combine simultaneous readings when complete.
Historical selected-card measurements retain their recorded boundary; missing
cards are not reconstructed. Whole-system power adds CPU and other-system
estimates once to the declared GPU total; it is not a wall-meter measurement.
Compare equivalent model/quantization, context, concurrency, offload, reasoning,
MTP, output budget, protocol and corpus before attributing differences to hardware.

## Local cleanup

**Results -> Delete** removes that session from the local application and
moves its JSON into ignored `results/.trash/`. Active runs must stop first. To
restore a session, move its archived JSON back into `results/runs/` and rename it
to the `id` inside the file plus `.json`, then refresh Results.

Deleting a tracked result produces a local Git deletion. This does not remove
the upstream copy; include only your new session file in a result contribution.

## Add, continue or rerun tests

Managed LM Studio controls model loading for additions and repairs. External
endpoint additions and repairs require a fresh matching connection and operator
model loading; public JSON does not persist connection details. Separate-file
resume remains managed-only. See [sharing results](../docs/sharing-results.md).

In Results, select a benchmark and choose **Add / rerun tests**. Select models and
their Standard/MTP techniques, prompt sizes and query counts. Choose an action:

- **Add missing tests** runs cases without a completed result.
- **Retry failed tests** includes failed and skipped cases as well as missing ones.
- **Rerun selected tests** includes successful cases too. Select one model to
  replace its measurements, or use **Select benchmark models** to select the
  installed models and techniques from the benchmark.

The preview separates new cases, failed retries, successful reruns and existing
measurements. Replacement is explicit; existing successful cases are preserved
when adding or retrying. Measurements use the same `results/runs/<id>.json` file.
Before execution, an exact local backup is written under ignored
`results/.history/`. Replaced rows move into `previous_attempts` with their
original session identifier, archive time and reason. Charts and summary medians
use active results, so replacements are not averaged with archived attempts.

`config` describes the benchmark's combined selections. Each item in `sessions`
records the settings used by that execution; result `session_id` links back to
it. `execution_config` is the most recent workload selection. This provenance
matters when extending an older planner run or adding MTP for one model.
`remaining_tests` counts requested cases with no terminal result.

Partial and completed benchmarks can both be shared. To add missing tests later,
use the same file and follow [sharing results](../docs/sharing-results.md).
