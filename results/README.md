# Benchmark sessions

One benchmark is one JSON file in `runs/`. A session contains every model,
prompt size, concurrency level, repeat and failure measured for one machine
configuration. Changing cards or runtime GPU placement means starting a new
session. Historical unversioned files remain readable without rewriting them.

## Run and contribute

1. Launch Wattever and click **Reset for all models** to select every downloaded
   language model and restore the standard measurement settings.
2. Give the session a useful label, such as `RTX 5060 Ti - stock`. Choose prompt
   sizes and concurrent request counts. Default concurrency is one; click **10**
   to select all levels from one through ten. Prompt-length badges work the same way. Review the total request count.
3. With multiple cards, configure placement in LM Studio and describe it in the
   **Runtime GPU placement** field. The sensor selector controls power readings
   only. It does not change where LM Studio executes the model.
4. Run the benchmark. Failures and partial sessions remain useful data.
5. Download **JSON** in Results, or take the matching file from `results/runs/`.
   Review it for details you do not want to publish.
6. Follow the [sharing guide](../docs/sharing-results.md) to upload the file and
   propose a pull request. Validation runs on the contribution and the shared
   catalog updates after merge. No index or aggregate file changes are needed.

## Standard format

New files use `schema_version: 2`. See [benchmark.schema.json](benchmark.schema.json).
Fields are additive; the existing `gpu`, `host`, `config` and `results` fields remain.

| Field | Meaning |
| --- | --- |
| `application` | Tool version and measurement protocol version |
| `hardware.os` | OS name, release and architecture |
| `hardware.cpu` | CPU name, physical and logical core counts |
| `hardware.system` | Manufacturer/model and physical RAM in bytes |
| `hardware.gpus` | Separate entries for every discoverable adapter; name, vendor, memory in bytes, driver and optional sensor index |
| `hardware.motherboard`, `hardware.firmware`, `hardware.storage` | Best-effort board model, firmware vendor/version and physical disk models/capacity/interface |
| `classification` | Hardware configuration grouping hash, GPU names and platform; not a unique machine identity |
| `measurement_scope` | Which GPU sensor supplies power and user-reported runtime placement |
| `corpus.sha256` | Corpus fingerprint for comparing the exact workload |
| `config` | Selected models, prompt targets, concurrency levels and measurement settings |
| `results` | Measurements, failures, skips and cancellation records |

Unknown metadata is `null` or an empty collection. Hardware discovery is best
effort; an empty adapter list does not prove that the machine has no GPU.
New files omit GPU UUIDs, serial numbers, usernames and hostnames. User-entered
notes and runtime/model names are included; review them before sharing. Historical
files may still contain a GPU UUID; it may be removed without changing measurements.

## Concurrent measurements

Each result row includes `concurrency`; missing means one in historical files.
Single-request prefill/decode retain their original definitions. Groups above
one contain `requests`, including per-request status, TTFT and decode speed.
Each concurrent request has its own leading nonce to invalidate prefix caching.
`lms load --parallel` sets the runtime's prediction capacity for that level.

`aggregate_completion_tps` is successful requests' total output tokens divided
by group wall time, including prefill and queueing. It is not the sum of individual
decode speeds. A group is successful only when every request succeeds. If usage
counts are unavailable, stream chunks supply an estimate and the result says so.
The UI marks estimated token counts and derived throughput with `*`.

`group_power` covers the group interval. Concurrent phases overlap, so they do
not get invented prefill/decode energy splits. Power is for the selected GPU
sensor, never the sum of all connected cards. Whole-system power remains an
estimate and omits other GPUs' load; do not treat it as a wall-meter measurement.
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
