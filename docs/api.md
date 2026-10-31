# Local HTTP API

This reference describes the local Python helper. Hosted [browser mode](browser.md) uses an in-page adapter, not this service. The helper binds to `127.0.0.1` on port 8713 by default, or the next available port. `GPU_BENCH_PORT` changes the starting port; `GPU_BENCH_NO_BROWSER=1` suppresses browser opening. Keep this interface on a trusted local machine.

JSON errors have an `error` field. Invalid input and runtime conflicts generally return 400, missing runs 404, active-run edits or deletion 409, and unexpected failures 500.

State-changing requests must come from the helper's own browser origin, or an Origin-free local API client. Send JSON bodies with `Content-Type: application/json`; rejected cross-origin or non-JSON changes return 403. Authenticated model discovery must use POST; GET query parameters cannot select a credential environment variable. See [security](../SECURITY.md).

## Read and control routes

Read endpoints are `GET /api/version`, `/api/env`, `/api/models`, `/api/status`, `/api/events?cursor=0`, `/api/runs`, `/api/runs/<id>`, `/api/runs/<id>/download`, `/api/preview?tokens=512`, `/api/export.json` and `/api/export.csv`. Pass the returned event cursor to subsequent polls. Exports accept comma-separated `ids`; without them they include all runs.

`POST /api/bench/start` accepts benchmark configuration and starts inference. `/api/bench/cancel` requests cancellation. `POST /api/runs/<id>/meta` changes label and notes; `DELETE /api/runs/<id>` moves a run to local trash; `POST /api/shutdown` stops Wattever. An active run cannot be edited or deleted. Benchmark runs write results.

Managed LM Studio additionally supports `POST /api/lmstudio/start` and `/api/lmstudio/unload`. Generic endpoints never use the LM Studio CLI to load or unload their models.

`POST /api/hardware/bandwidth` with `{}` measures storage → RAM and pinned RAM → VRAM while no benchmark is running. Optional `gpu_index` selects the NVIDIA sensor; `storage_probe_directory` chooses an existing folder on the storage volume to test (default: results folder). It returns `transfer_bandwidth` with capture time, raw samples, methods and explicit unavailable states. Native transfer probes also run automatically before throughput/capability benchmarks. Throughput start accepts the same private `storage_probe_directory` override. See [measurement methods](measurements.md).

Include `run_id` to attach a later transfer profile to an existing benchmark. Hardware class and selected GPU/driver must match. The file is backed up, inference results stay unchanged, and metadata identifies the later capture time. This does not reconstruct historical transfer conditions.

`POST /api/bench/link` accepts `{"run_ids":["<managed-run>","<endpoint-run>"],"label":"Machine benchmark"}`. It groups stopped benchmarks on matching hardware/GPU/driver, with local inference explicitly asserted for generic endpoints. The first ID is the group root. Each run keeps its provider, settings and measurements; **Open machine benchmark** in Results selects all available members for charts, tables and multi-run JSON/CSV exports. Send all desired member IDs when updating a group. Files are backed up before group metadata is saved.

## Generic endpoint configuration

Use `provider: "openai"` for an externally managed streaming Chat Completions endpoint, including LM Studio used without CLI management. This integration uses an OpenAI-compatible transport subset; it does not implement the complete OpenAI API.

```json
{
  "provider": "openai",
  "endpoint_base_url": "http://127.0.0.1:8000/v1",
  "endpoint_api_key_env": "BENCHMARK_API_KEY",
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

Send this to `POST /api/bench/start` only when ready to run inference. Omit `endpoint_api_key_env` if authentication is unnecessary. Otherwise set that environment variable before starting the helper; the field contains only its name. Literal keys are rejected. URLs must use HTTP(S) without embedded credentials, query or fragment. A root URL gets `/v1`; an explicit API path is preserved. Do not supply `/chat/completions` or `/models` as the base.

`endpoint_inference_local` defaults to **false**, including for loopback addresses. Set it true only when inference executes on the benchmark computer. This user assertion permits local power attribution; neither route-origin detection nor a display name verifies server hardware.

`endpoint_reasoning_effort` defaults to null in the throughput API, which omits the request field. The UI defaults **Requested reasoning** to **None**. Set the API field to `none`, `minimal`, `low`, `medium`, `high` or `xhigh` to request standard Chat Completions `reasoning_effort`. Unsupported requests fail visibly. The requested value is recorded separately from the engine's unverified effective behavior. Short budgets may otherwise end during hidden reasoning before a visible answer appears.

`token_limit_field` selects `max_tokens` (default) or `max_completion_tokens`. Explicit `temperature: null` omits that parameter; numeric values request the specified temperature, with UI default 0. Keep parameter choices consistent across comparisons. These are request controls, not verification of server behavior.

`POST /api/endpoint/check` accepts these connection fields and probes discovery without running inference. It returns `ok`, `models`, `manual_model_ids: true`, `provider`, resolved `endpoint_identity`, and an `error` on discovery failure. Exact served model IDs can always be supplied in `models`; discovery alone does not verify streaming inference or usage reporting. `GET /api/models?provider=openai&endpoint_base_url=...` is also available without credential forwarding; use POST for authenticated discovery and to keep connection details out of URL histories.

Discovered models preserve safe endpoint-reported quantization, context and state metadata. Managed LM Studio also uses its native model catalog to capture separately labeled `quantization_method` (QAT, QAD or UD when reported), with `quantization_method_source`. Result rows and optional quantization identity fields `method`/`method_source` retain these labels independently of the storage format. Older identities without method fields remain valid. When quantization appears only in a recognized model-ID suffix, `quantization_source` is `model_identifier`; explicit endpoint metadata uses `endpoint_reported`. An inferred suffix does not verify the served weights. Unknown quantization or context stays unknown. The UI uses a manual model ID only when discovery returns no models.

Results uses the current connection when discovering models for additional tests. With a generic endpoint, **Review new benchmark** opens the current served-model catalog and stages a separate run in Run; it does not modify historical measurements or start inference. Managed LM Studio retains same-file extension. Switching connection invalidates an open or cached model selection. Historical chart filters describe saved measurements, rather than the endpoint's current inventory.

The inference engine controls loaded models, context/KV allocation, quantization, placement, reasoning and speculative decoding. Effective generic settings use `context_policy` and `gpu_offload` of `provider_managed`, with no fallback reload. Result states such as `mtp`, `context_length` and `reasoning_disabled` remain unknown. A client context estimate does not prove the engine's context setting. Generic runs require reconnection and a new run; resume and same-file extension are unavailable. Cancellation aborts client work but cannot verify provider-side termination; a timeout stops the run for operator inspection.

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

Only one speed or capability run may execute at a time; conflicting starts/imports return 409. Capability tests do not load/unload models for either provider. Local telemetry requires explicit `endpoint_inference_local: true` for both. An active request worker after timeout/cancellation continues to block new benchmarks. Import recomputes summary/grades from full audit answers and validates hashes; it does not authenticate the reported model or operator.

## Managed LM Studio configuration

The default provider is `lmstudio`. `LMS_BASE_URL` selects its API (default `http://localhost:1234`). For example:

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
| `gpu_index` | 0 (telemetry sensor only) |

## Managed LM Studio extensions

`GET /api/bench/extend-plan?run_id=<id>` supplies extension options. `POST /api/bench/extend-plan` previews a selection such as:

```json
{"run_id":"<run-id>","model_modes":{"<installed-model-id>":["off","on"]},"sizes":[512,5000],"concurrency_levels":[1,2],"repeats":2,"retry_failed":false,"rerun_existing":false}
```

Optional booleans `fallback_on_failure` and `skip_after_timeout` control the new session's load-placement fallback and practical timeout skipping. Timeout-based skips are not hard capacity/OOM claims. Optional `per_test_timeout_s` sets the new execution session's timeout (integer 1 through 7200). Existing measurements retain their recorded budgets. `repeats` is the desired total per case (saved repeat count through 100). Raising it adds new slots. To repair individual cases, optionally supply `selected_cases` as a nonempty array of `[model_id, mtp_boolean, concurrency, target_tokens, zero_based_repeat]` tuples within the selected workload. For example, `[["<installed-model-id>",false,2,5000,0]]` with `rerun_existing: true` replaces only that first repeat. Omit `selected_cases` to use the full selection. Case selection applies only to the execution session; the benchmark keeps its full workload configuration.

Review counts and warnings before submitting the selection to `POST /api/bench/extend`. Defaults add missing cases; `retry_failed` includes failed and skipped cases; `rerun_existing` includes successful selected cases. Extension takes a history snapshot and modifies the existing file. `POST /api/bench/resume` with `run_id` creates a separate run for unfinished work. These operations load/unload models and require compatibility checks; see [measurements](measurements.md) and [architecture](architecture.md).
