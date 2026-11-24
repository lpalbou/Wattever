# Local HTTP API

This reference describes the local Python helper. Hosted [browser mode](browser.md) uses an in-page adapter, not this service. The helper binds to `127.0.0.1` on port 8713 by default, or the next available port. `GPU_BENCH_PORT` changes the starting port; `GPU_BENCH_NO_BROWSER=1` suppresses browser opening. Keep this interface on a trusted local machine.

JSON errors have an `error` field. Invalid input and runtime conflicts generally return 400, missing runs 404, active-run edits or deletion 409, and unexpected failures 500.

JSON request bodies are limited to 32 MiB. This includes capability imports; a suite's supported question count does not guarantee that its completed audit fits in one HTTP request.

State-changing requests must come from the helper's own browser origin, or an Origin-free local API client. Send JSON bodies with `Content-Type: application/json`; rejected cross-origin or non-JSON changes return 403. Authenticated model discovery must use POST; GET query parameters cannot carry an API key or select a credential environment variable. See [security](../SECURITY.md).

## Read and control routes

Read endpoints are `GET /api/version`, `/api/env`, `/api/system`, `/api/system/history`, `/api/models`, `/api/status`, `/api/events?cursor=0`, `/api/runs`, `/api/runs/<id>`, `/api/runs/<id>/download`, `/api/preview?tokens=512`, `/api/export.json` and `/api/export.csv`. Pass the returned event cursor to subsequent polls. Exports accept comma-separated `ids`; without them they include all runs. `/api/env` lists the models of the last saved connection, which is empty when that connection was an endpoint that is now offline; `/api/env?provider=lmstudio` always lists LM Studio's own catalog.

`GET /api/system` returns a Unix-seconds `timestamp`, a `telemetry` snapshot (or null), `scope`, `source`, `error`, `sampling_interval_ms` and `capture`. The helper samples the machine once per second from startup to shutdown, independent of viewers, model and endpoint connections; a sensor that cannot start is reported in `error` and retried. `telemetry.t` is the reading's Unix time and `telemetry.sample_age_s` its age. While a benchmark measures power, System reads that benchmark's sampler instead of running a second sensor stream, and `capture.sampler` is `benchmark`. Readings describe the benchmark computer and can include other processes.

`GET /api/system/history?start=<unix s>&end=<unix s>&max_points=900` returns the recorded readings in that range. The defaults are the last 15 minutes and 900 points; the range is clamped to the last 24 hours and `max_points` to 10-4000. When the range holds more 1-second readings than `max_points`, they are averaged into aligned buckets of 1, 2, 5, 10, 15, 30, 60, 120, 300 or 600 seconds (`bucket_s`); each averaged point has `n` readings and the peaks in `max`. Points carry `t` and the same field names as `telemetry`, with per-card rows in `gpus`; a missing reading is absent, not zero. Empty buckets, and times when the helper was not running, are left out: draw a gap where consecutive points are more than `gap_s` apart. The response also has `start`, `end`, `oldest`, `retention_s` and `log`. `log.mode` is `owner` when this helper writes the log, `reader` when another local helper does and this one serves its log, or `memory` when no log could be opened (the last hour is then kept in memory only). Invalid numbers or `end` before `start` return 400.

`GET /api/system/cpu-sensor` reports the optional Windows CPU sensor: `supported` (Windows only), `installed`, `pawnio_version`, and the last setup's `phase` (`idle`, `downloading`, `waiting_for_approval`, `done`, `failed` or `cancelled`), `message` and `log`. `POST /api/system/cpu-sensor` with `{"action":"install"}` or `{"action":"uninstall"}` starts it and returns that status; Windows then shows one administrator prompt. Only start it at the user's explicit request. See [measurements](measurements.md) for what it installs.

The log is kept in `results/.telemetry/` (override with `GPU_BENCH_TELEMETRY_DIR`): one JSON line per reading, one file per UTC hour, finished hours compressed, files older than 24 hours deleted. It is gitignored, never read by Results, Share, exports or published catalogs, and stays on this computer. Writes are batched every 10 seconds, so a hard kill can lose the last few seconds; a torn last line is skipped. With two GPUs a reading is about 560 bytes: about 2 MB for the open hour and 0.1-0.35 MB per compressed hour, under 10 MB for the whole day. The first query over many hours after a start reads the compressed files (a few seconds for a full day); finished hours are then served from memory.

The environment's `hardware.transfer_coverage` reports detected/measured NVMe counts and incomplete or unverified physical-drive coverage. Transfer captures include per-volume `storage_volumes` evidence and `storage_discovery` counts; volume discovery itself performs no transfer I/O. `POST /api/hardware/bandwidth` includes mounted NVMe volumes automatically and returns `transfer_coverage` alongside the capture. Run it while inference is idle.

`POST /api/bench/start` accepts benchmark configuration and starts inference. `/api/bench/cancel` requests cancellation. `POST /api/runs/<id>/meta` changes a benchmark's name and description with `{"label": "...", "notes": "..."}`: a name of 1 to 160 characters, a description of at most 5000, either field optional, measurements untouched. A running benchmark can be renamed; the edit is served at once and written with that benchmark's next result. `DELETE /api/runs/<id>` moves a run to local trash; `POST /api/shutdown` stops Wattever. An active run cannot be deleted. Benchmark runs write results.

Both `POST /api/bench/cancel` and `POST /api/capabilities/cancel` accept optional
`{"expected_run_id":"<owned-run-id>"}`. The helper checks that ID and requests
cancellation atomically against new starts. An empty/non-string ID returns 400;
a different active ID returns 409 without cancelling it. Omitting the field
retains unscoped cancellation. Automated operators can check
`GET /api/version` for `features.atomic_expected_run_id_cancel: true` before
relying on this guarantee. Client cancellation does not establish that an
external model engine has stopped.

Speed configurations accept `hardware_probe_policy: "auto"` (default) or
`"defer"`. Deferred collection launches no fresh transfer probes; a compatible
saved profile may be reused with its original capture timestamp. Otherwise the
run records deferred/unavailable evidence. Capability evaluations use this
policy independently of their generation settings. The version response lists
supported policies in `features.hardware_probe_policy`.

Managed LM Studio additionally supports `POST /api/lmstudio/start` and `/api/lmstudio/unload`. Generic endpoints never use the LM Studio CLI to load or unload their models.

`POST /api/hardware/memory` with `{}` refreshes the built-in OS/firmware memory topology, returning `memory` with `channel_topology`. It requires no installed hardware utility and does not run a bandwidth test. Optional `gpu_index` selects a sensor. With `run_id`, the helper checks matching GPU/driver, CPU, motherboard, RAM capacity/modules and reported clocks, backs up the benchmark, and attaches a timestamped later annotation. Original inference rows and session snapshots remain unchanged. An unavailable probe preserves saved metadata. The route rejects active benchmarks with 409. `GET /api/env` includes `hardware_follow_ups` for pending checks whose GPU is present; successfully annotating that benchmark completes its memory check. See [topology provenance](measurements.md).

`GET /api/env?connect=0` describes the computer and the saved form without contacting any inference engine: `models` and `default_models` are empty, `lms.server` and `lms.version` are null, and a saved local endpoint address is still returned in `endpoint_connection`. The page opens this way and contacts an engine only on **Connect** (`/api/env?provider=lmstudio` for managed LM Studio, `POST /api/endpoint/check` for an endpoint). Without `connect=0` the route behaves as before and asks the last saved connection for its models. `GET /api/env` also returns `gpu_setup`: LM Studio retains its own GPU allocation settings, while a compatible local Strata Control installation can accept a saved placement choice. `POST /api/hardware/gpu-setup` accepts `{"mode":"auto"}` or `{"mode":"single","gpu_index":1}`. It saves only the next desktop model-load choice; it never starts, stops or switches a model. The route rejects active benchmarks. Strata Control defaults to all eligible NVIDIA cards for Flash-Next, with its main/output/MTP card last; its Qwen3.6 engine uses one selected main card. Set `WATTEVER_STRATA_CONTROL_DIR` to integrate another compatible desktop installation; otherwise the helper checks the sibling `flash-next` directory. Unsupported endpoints remain externally managed. Primary GPU selection affects benchmark identity; telemetry covers all detected NVIDIA cards independently and in combination.

`POST /api/hardware/bandwidth` with `{}` measures storage → RAM and pinned RAM → VRAM while no benchmark is running. Every detected NVIDIA device is probed sequentially, with its index/name, independent samples and before/during-transfer PCIe snapshots in `ram_to_vram_devices`. Physical slot labels and wiring remain unverified. Optional `gpu_index` selects which result is also exposed as the legacy `ram_to_vram` field; it does not limit the device sweep. `storage_probe_directory` chooses an existing folder on the storage volume to test (default: results folder). It returns `transfer_bandwidth` with capture time, raw samples, methods and explicit unavailable states. Native transfer probes also run automatically before throughput/capability benchmarks. Optional `storage_probe_directories` is a list of up to eight existing additional folders. Their independent results appear in `storage_volumes`; RAM copy results appear in `ram_copy_single` and `ram_copy_parallel`. Throughput and capability starts accept the same private storage folder overrides. Cached captures are rejected when the detected GPU mapping or recorded hardware configuration changes. See [measurement methods](measurements.md).

Include `run_id` to attach a later transfer profile to an existing benchmark. Hardware class and selected GPU/driver must match. The file is backed up, inference results stay unchanged, and metadata identifies the later capture time. This does not reconstruct historical transfer conditions.

A hardware-class difference caused solely by a page-aligned usable-RAM change of at most 64 KiB can be accepted when installed RAM, modules, reported clocks, CPU/core counts, board, OS, system model and all GPUs/drivers match independently. The capture records this evidence in `hardware_match`; the original classification remains unchanged. Different installed memory, clocks, CPUs, boards or GPUs are rejected.

`POST /api/bench/link` accepts `{"run_ids":["<managed-run>","<endpoint-run>"],"label":"Machine benchmark"}`. It groups stopped benchmarks on matching hardware/GPU/driver, with local inference explicitly asserted for generic endpoints. The first ID is the group root. Each run keeps its provider, settings and measurements; **Open machine benchmark** in Results selects all available members for charts, tables and multi-run JSON/CSV exports. Send all desired member IDs when updating a group. Files are backed up before group metadata is saved.

`POST /api/bench/consolidate` accepts the same `run_ids` and a `label` to store their measurements in **one benchmark file and one library entry**. This is what **Merge into one…** in Results sends. Put the destination first. Sources must have matching hardware/GPU/driver, corpus and measurement protocol, with identified execution sessions. Backups are saved and the other source files move to local trash. The destination retains every result, configuration fingerprint, execution session and source metadata. With several engines its aggregate configuration uses `provider: "mixed"`; `engine_groups` and each session's `engine_group_id` identify actual providers and settings. Benchmarks of a single engine become one plain benchmark with more sessions, as if extended with Add tests. Charts and CSV preserve engine attribution. Both managed and external-engine tests can be repaired directly within that benchmark after connecting the matching engine.

Two optional fields cover what the default refuses:

- `assume_local` lists endpoint benchmarks that were saved as remote and that the operator states were served by the computer that launched them. Such a benchmark keeps an inventory of that computer in `measurement_client`; it is accepted only when that inventory matches the other benchmarks' machine. The merged file records the statement in `inference_local_assertion` (`source: "user_reported"`, `declared_during: "merge"`, `power_sampled: false`) and in `consolidation_history`. Power was not sampled for such a benchmark and stays unavailable.
- `overlap` decides a test measured more than once. `"error"` (default) refuses. `"newest"` keeps one measurement per test in `results`, a completed one before an incomplete one and otherwise the most recent, and moves the others to `previous_attempts` with `archive_reason: "superseded_by_merge"`.

`POST /api/bench/merge-plan` accepts `run_ids` (one or more, in any order) and optional `assume_local`, and changes nothing. It returns the benchmarks in merge order (`run_ids`, destination first: an already merged benchmark, else the largest locally measured one), `ok`, the `machine`, and `issues`: `block` cannot be merged, `confirm` needs `assume_local`, `warn` is informational. Each names the benchmarks it concerns. It also reports the resulting `engines`, `models`, `tests`, the `superseded` count, and `also_here`: other benchmarks of that machine that could join.

`GET /api/bench/merge-suggestions` lists groups of benchmarks from different engines that ran on one machine and can be merged, including endpoint benchmarks matched through the computer that launched them.

`POST /api/bench/merge-undo` accepts `{"run_id":"<merged benchmark>"}` and restores the exact files that existed before its most recent merge. It is refused once that benchmark has changed. `GET /api/runs` reports `merge: {sources, at, undo}` for merged benchmarks.

### GPU telemetry boundaries

NVIDIA helper telemetry samples every detected NVIDIA card. Primary `gpu_index` identifies the benchmark; engine placement is configured separately. Live and saved observations retain per-card power, utilization, VRAM, temperature and clocks, with explicit unavailable readings. Host CPU temperature is `cpu_temp_c` with `cpu_temp_source` (for example `linux_hwmon:coretemp:Package id 0`, `windows_hwinfo_shared_memory:CPU Package`, `windows_librehardwaremonitor_web:CPU Package`, `windows_coretemp_shared_memory:hottest_core` or `windows_acpi_thermal_zone:<zone>`); when it is null, `cpu_temp_note` gives the reason. Combined board power and physical VRAM use readings from the same monitor timestamps; physical memory on separate cards remains separate allocation pools.

All-card history uses scope `benchmark_client_all_nvidia_gpus`, with individual readings in `gpus` and per-card summary statistics in `summary.gpus`. Single-request `power.gpus` records per-card phase windows. Concurrent cases retain one `group_power` interval for all requests, so overlapping request energy is counted once. The global VRAM peak is the peak of simultaneous sums. Historical selected-card captures preserve their recorded boundary; missing second-card energy is not reconstructed. Generic endpoint attribution requires `endpoint_inference_local: true`. See [measurement definitions](measurements.md) for coverage, estimates and exported fields.

## Generic endpoint configuration

Use `provider: "openai"` for an externally managed streaming Chat Completions endpoint, including LM Studio used without CLI management. This integration uses an OpenAI-compatible transport subset; it does not implement the complete OpenAI API.

`requested_mtp` records external-engine intent as `on`, `off` or `unknown`.
It is metadata only: it neither configures the engine nor adds a parameter to
Chat Completions. Check `GET /api/version` for
`features.external_requested_mtp_metadata: true` before depending on its
retention. Effective MTP remains unknown without control-plane evidence;
per-request draft counters are recorded separately when supplied by the server.

```json
{
  "provider": "openai",
  "endpoint_base_url": "http://127.0.0.1:8000/v1",
  "endpoint_api_key": "<key, if the endpoint requires one>",
  "endpoint_inference_local": true,
  "models": ["<served-model-id>"],
  "sizes": [512, 5000],
  "concurrency_levels": [1],
  "max_tokens": 256,
  "repeats": 2,
  "reproducibility": {
    "quantization": "IQ3_S",
    "context_tokens": 32768,
    "kv_cache_dtype": "int8"
  }
}
```

Send this to `POST /api/bench/start` only when ready to run inference. Omit `endpoint_api_key` if authentication is unnecessary. The key is sent to the endpoint as a Bearer token and held in memory only: it is not written to benchmark files, the saved form, exports or recorded errors, so every request that needs it (`/api/endpoint/check`, start, extend, capability start) carries it again. A pasted `Bearer ` prefix is ignored; a key must be printable text without spaces. A script can instead set `endpoint_api_key_env` to the name of an environment variable defined before the helper started. URLs must use HTTP(S) without embedded credentials, query or fragment. A root URL gets `/v1`; an explicit API path is preserved. Do not supply `/chat/completions` or `/models` as the base.

`endpoint_inference_local` defaults to **false**, including for loopback addresses. Set it true only when inference executes on the benchmark computer. This user assertion permits local power attribution; neither route-origin detection nor a display name verifies server hardware.

`endpoint_reasoning_effort` defaults to null in the throughput API, which omits the request field. The UI defaults **Requested reasoning** to **None**. Set the API field to `none`, `minimal`, `low`, `medium`, `high` or `xhigh` to request standard Chat Completions `reasoning_effort`. Unsupported requests fail visibly. The requested value is recorded separately from the engine's unverified effective behavior. Short budgets may otherwise end during hidden reasoning before a visible answer appears.

`token_limit_field` selects `max_tokens` (default) or `max_completion_tokens`. Explicit `temperature: null` omits that parameter; numeric values request the specified temperature, with UI default 0. Keep parameter choices consistent across comparisons. These are request controls, not verification of server behavior.

`POST /api/endpoint/check` accepts these connection fields and probes discovery without running inference. It returns `ok`, `models`, `manual_model_ids: true`, `provider`, resolved `endpoint_identity`, and an `error` on discovery failure. `helper_lmstudio: true` means the address is the LM Studio on this computer that the helper's `lms` CLI controls (no API key, LM Studio's port, every served ID in LM Studio's catalog): benchmark it with `provider: "lmstudio"` to choose context, GPU placement and MTP. Exact served model IDs can always be supplied in `models`; discovery alone does not verify streaming inference or usage reporting. `GET /api/models?provider=openai&endpoint_base_url=...` is also available without credential forwarding; use POST for authenticated discovery and to keep connection details out of URL histories.

Discovered models preserve safe endpoint-reported quantization, context and state metadata. Managed LM Studio also uses its native model catalog to capture separately labeled `quantization_method` (QAT, QAD or UD when reported), with `quantization_method_source`. Result rows and optional quantization identity fields `method`/`method_source` retain these labels independently of the storage format. Older identities without method fields remain valid. When quantization appears only in a recognized model-ID suffix, `quantization_source` is `model_identifier`; explicit endpoint metadata uses `endpoint_reported`. An inferred suffix does not verify the served weights. Unknown quantization or context stays unknown. The UI uses a manual model ID only when discovery returns no models.

Results uses the current connection when discovering models for additional tests. With a generic endpoint, **Review new benchmark** opens the current served-model catalog and stages a separate run in Benchmarks; it does not modify historical measurements or start inference. Managed LM Studio retains same-file extension. Switching connection invalidates an open or cached model selection. Historical chart filters describe saved measurements, rather than the endpoint's current inventory.

The inference engine controls loaded models, context/KV allocation, quantization, placement, reasoning and speculative decoding. Effective generic settings use `context_policy` and `gpu_offload` of `provider_managed`, with no fallback reload. Result states such as `mtp`, `context_length` and `reasoning_disabled` remain unknown. The helper cannot launch an endpoint's model with MTP: `mtp_modes` of `["off", "on"]` runs the standard pass only and records `measurement_scope.mtp_pass` as requested but not run, and `["on"]` alone is rejected with 400. A client context estimate does not prove the engine's context setting. Same-file additions and repairs require a fresh connection matching the saved external engine; separate-file resume is managed LM Studio only. Cancellation aborts client work but cannot verify provider-side termination; a timeout stops the run for operator inspection.

Optional `reproducibility` fields are `engine`, `engine_version`, `model_revision`, `quantization`, `context_tokens`, `kv_cache_dtype` and `mtp_draft_tokens`. Results mark these `user_reported`. Optional `performance_claims` entries contain `precision` (`FP4`, `INT4`, `FP8`, `INT8`, `FP16`, `BF16`, `FP32`), finite non-negative `value`, `unit`, `kind` (`advertised_peak`, `measured`, `estimate`), `sparsity` (`dense`, `sparse`, `unknown`), `execution_path` and `source`. Source and execution path are required. Use `TOPS` for integer precision and `TFLOP/s` for floating point. These annotations stay separate from detected hardware and Wattever measurements; see [measurements](measurements.md).

Endpoint URL, credential variable name and credentials are excluded from public results and exports. Connection configuration is not persisted for generic resume. Review public free-text labels, notes, model names and sources before sharing; see [security](../SECURITY.md).

### Automatic endpoint identity

No endpoint label or public ID is required. Connect and direct API starts resolve them automatically. Optional `endpoint_name_override` supplies one public display name; leave it empty to use detection. Legacy `endpoint_id`/`endpoint_label` configuration remains accepted as an operator override.

Identity uses recognized official public cloud hosts or explicit service/backend metadata. Local/private `/health`, `/props` and `/version` probes are read-only and bounded by an overall deadline; unsupported probes fall back without guessing from ports or model aliases. Unknown software receives a **Local endpoint**, **Private endpoint** or **Remote endpoint** name. `endpoint_identity` records `id`, `label`, `source`, `service`, `version`, `location`, `route_registry` and `evidence`. Explicit service metadata is `detected`; recognized cloud hosts and the declared managed LM Studio provider are `inferred`; optional naming overrides are `operator_override`; unsupported detection stays `unknown`. Location describes the route origin, not verified inference hardware.

Canonical known public API routes use stable public provider IDs for community aggregation. Other routes, including custom paths on recognized cloud hosts, receive random opaque IDs stable for the operational base URL in a private local registry. The helper stores that mapping in ignored `results/.endpoint-routes.json`; browser mode uses local storage. URLs are neither hashed into public IDs nor included in public configuration fingerprints. Do not share the registry. Removing it or clearing browser storage can generate a new identity for a privately mapped route. A cloud provider label identifies the public service, not its underlying inference engine or weights.

Optional `canonical_model_id`, `model_source`, `reported_reasoning` and `reported_mtp` (`on`, `off`, `unknown`) describe the model separately. They feed `model_configuration`/`configuration_id` with endpoint identity; requested, reported and verified effective values stay distinct. Historical `user_reported` endpoint provenance remains supported. Hashes verify content consistency, not endpoint authenticity or actual served weights.

## Capability routes

Capability evaluation and validated imports are helper-only; hosted mode provides a shared read/export/compare library. It uses separate files and a sequential request protocol described in [capability evaluation](capabilities.md).

| Route | Behavior |
| --- | --- |
| `GET /api/capabilities/suites` | Suite metadata, versions, digests and question counts |
| `GET /api/capabilities/status` | Running state, live item progress and available local telemetry |
| `GET /api/capabilities/events?cursor=0` | Bounded event stream snapshot and next cursor |
| `GET /api/capabilities/runs` | Capability run summaries |
| `GET /api/capabilities/runs/<id>` | Full saved run |
| `GET /api/capabilities/runs/<id>/download` | Public audit JSON download |
| `GET /api/capabilities/export.json?ids=<id>,<id>` | Public `capability_runs` bundle; omitted IDs selects all |
| `POST /api/capabilities/start` | Validate configuration, save run and start inference |
| `POST /api/capabilities/cancel` | Abort client work; provider termination remains unverified |
| `POST /api/capabilities/import` | Validate/re-score one run or a `capability_runs` bundle and save it |

`GET /api/capabilities/suites` also returns `runner` metadata from the running helper process: its `completion_policy`, generation/deadline `defaults`, and accepted `limits`. The UI requires a supported completion policy before launching or validating imports. An older helper can still expose saved results for browsing, but must be relaunched to apply updated capability code; refreshing browser assets alone does not update Python code.

Example start configuration:

```json
{
  "provider": "openai",
  "endpoint_base_url": "http://127.0.0.1:8000/v1",
  "model": "<served-model-id>",
  "suite_id": "gsm8k-test-chat-v1",
  "sample_size": 50,
  "repeats": 1,
  "seed": 0,
  "temperature": 0,
  "top_p": 1,
  "endpoint_reasoning_effort": "none",
  "max_tokens": 32768,
  "timeout_s": 0,
  "endpoint_inference_local": false
}
```

A model ID is required; endpoint naming/identity is resolved automatically for direct starts as well as the UI. `sample_size: 0` selects the full suite; a positive value selects a deterministic seeded subset, capped by suite size. Defaults are Core v1, 24 questions, one repeat, a 32,768-token total generation budget and no wall-clock deadline (`timeout_s: 0`), question-selection seed 0, temperature 0, omitted top-p and requested reasoning `none`. `send_seed` defaults false; true additionally sends `seed + repeat` to the generation API. Null top-p/temperature omit those parameters. Capability `token_limit_field` defaults to `max_tokens`; `max_completion_tokens` is an explicit alternative for endpoints requiring it. Unsupported parameters are visible failures, not silently retried with different sampling. The capability budget includes reasoning plus final output, and is independent of speed-test generation settings. `max_tokens` accepts 1 to 131,072; `timeout_s` accepts 0 to 86,400 seconds, with 0 meaning no wall-clock deadline. Stop remains available. Context and provider limits can still terminate generation. New evaluations record `completion_policy: "finished-stream-visible-answer-v2"`; the policy is part of the evaluation fingerprint. Historical runs without this field remain readable and retain their recorded decisions.

Only one speed or capability run may execute at a time; conflicting starts/imports return 409. Capability tests do not load/unload models for either provider. Local telemetry requires explicit `endpoint_inference_local: true` for both. Outstanding requests after timeout/cancellation continue to block new benchmarks until their client workers exit; `/api/status` reports that wait. External engine cancellation remains unverified, and Wattever does not terminate the external engine. Import recomputes summary/grades from full audit answers and validates hashes; it does not authenticate the reported model or operator.

## Managed LM Studio configuration

The default provider is `lmstudio`. `LMS_BASE_URL` selects its API (default `http://127.0.0.1:1234`). Explicit environment/configuration URLs remain unchanged; historical timings are not adjusted for connection-address differences. For example:

```json
{"models":["<installed-model-id>"],"sizes":[512,5000],"concurrency_levels":[1,2],"max_tokens":256,"repeats":1}
```

| Setting | Default |
| --- | --- |
| `sizes` | 512, 5000, 10000, 20000, 40000 |
| `concurrency_levels`, `repeats`, `max_tokens` | `[1]`, 1, 256 |
| `gpu_offload`, `fallback_on_failure`, `stop_model_after_failure` | `max`, true, true |
| `context_policy`, `context_headroom`, `context_safety_fraction` | `per_test`, 512, 0.05 |
| `mtp_modes`, `warmup`, `disable_thinking` | `["off"]`, true, true |
| `load_timeout_s`, `per_test_timeout_s`, `power_interval_ms` | 900, 1800, 150 |
| `gpu_index` | 0 (primary GPU identity; all detected NVIDIA GPUs are monitored) |

`gpu_offload` accepts `max`, `auto`, `off` or a fraction from `0.0` to `1.0`. With `fallback_on_failure: true`, a managed load that fails is retried as `auto`, then `0.5`, then `off`, and each result's `gpu_offload` holds the placement that loaded. Send `fallback_on_failure: false` to record a load that does not fit as a failure at the requested placement. See [automatic fallbacks and substitutions](measurements.md#automatic-fallbacks-and-substitutions).

## Model additions, repairs and removal

`GET /api/bench/extend-plan?run_id=<id>` supplies managed extension options. For an external endpoint, use `POST /api/bench/extend-options` with `run_id` and a `connection` object containing fresh endpoint configuration. It returns the catalog, saved execution `config` and optional `engine_group_id`. Include the same connection and group ID in preview/start requests; keep connection credentials out of URLs. `POST /api/bench/extend-plan` previews a selection such as:

```json
{"run_id":"<run-id>","model_modes":{"<installed-model-id>":["off","on"]},"sizes":[512,5000],"concurrency_levels":[1,2],"repeats":2,"retry_failed":false,"rerun_existing":false}
```

Optional booleans `fallback_on_failure` and `skip_after_timeout` control the new session's load-placement fallback and practical timeout skipping. Timeout-based skips are not hard capacity/OOM claims. Optional `per_test_timeout_s` sets the new execution session's timeout (integer 1 through 7200). Existing measurements retain their recorded budgets. `repeats` is the desired total per case (saved repeat count through 100). Raising it adds new slots. To repair individual cases, optionally supply `selected_cases` as a nonempty array of `[model_id, mtp_boolean, concurrency, target_tokens, zero_based_repeat]` tuples within the selected workload. For example, `[["<installed-model-id>",false,2,5000,0]]` with `rerun_existing: true` replaces only that first repeat. Omit `selected_cases` to use the full selection. Case selection applies only to the execution session; the benchmark keeps its full workload configuration.

Review counts and warnings before submitting the selection to `POST /api/bench/extend`. Defaults add missing cases; `retry_failed` includes failed and skipped cases; `rerun_existing` includes successful selected cases. Extension takes a history snapshot and modifies the existing file. `POST /api/bench/resume` with `run_id` creates a separate run for unfinished managed work; an endpoint benchmark continues in its own file through `/api/bench/extend` with a fresh `connection`. `GET /api/runs` and `/api/status` report `error` and `remaining_tests` for a benchmark that stopped early. On an external endpoint a failed row can carry `fail_kind` `model_unavailable` (the endpoint refused the model; its other tests are skipped) or `endpoint_unreachable` (the endpoint disappeared; the run stopped), with `endpoint_check: {verdict, http_status}`. Managed operations load/unload models. External extensions never load/unload models, use only `["off"]` scheduling modes with provider-controlled MTP, and require a fresh matching endpoint connection. Adding a first external engine upgrades the existing file with execution groups. Use distinct served model IDs for variants across engines. All extensions require compatible hardware, GPU/driver and corpus; managed extensions additionally check LM Studio version; see [measurements](measurements.md) and [architecture](architecture.md).

`POST /api/runs/<id>/models/delete` with `{"models":["<model-id>"]}` removes selected models, their results and planned workloads from that benchmark. Other models are preserved. It creates a history backup and returns `run`, `removed_measurements` and `backup_created`. Active work blocks removal. The last model requires whole-benchmark deletion.

A saved `storage_comparison` with protocol `wattever-storage-comparison-v1` can retain frozen `baseline_results`, `alternate_results`, condition labels, paired summary `rows` and cache notes. Both measurement arrays use ordinary result-row fields and stay separate from the primary `results` matrix. JSON exports retain them; Results provides a dedicated comparison CSV.
