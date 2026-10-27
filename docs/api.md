# Local HTTP API

The server binds to `127.0.0.1` on port 8713 by default, or the next available port. Set `GPU_BENCH_PORT` to change the starting port. This local interface is intended for a trusted local user, not exposure to a network. JSON errors have an `error` field; invalid input and runtime conflicts generally return 400, missing runs 404, active-run metadata edits or deletion 409, and unexpected failures 500.

Read endpoints: `GET /api/version`, `/api/env`, `/api/models`, `/api/status`, `/api/events?cursor=0`, `/api/runs`, `/api/runs/<id>`, `/api/runs/<id>/download`, `/api/preview?tokens=512`, `/api/export.json` and `/api/export.csv`. Exports accept comma-separated `ids`; without them they include all runs. `GET /api/bench/extend-plan?run_id=<id>` supplies extension options.

State-changing endpoints: `POST /api/bench/start` accepts benchmark configuration; `/api/bench/cancel` requests cancellation; `/api/bench/resume` accepts `run_id`; `/api/bench/extend-plan` previews a selection; `/api/bench/extend` starts it. Extension selection includes `run_id`, `model_modes` (model IDs mapped to arrays of `off` and/or `on`), `sizes`, `concurrency_levels`, `retry_failed`, and `rerun_existing`. Defaults add missing cases; `retry_failed` includes failed and skipped cases; `rerun_existing` includes successful selected cases. Plan first and review its counts. Starting, resuming or extending loads and unloads models, performs inference and writes results; extension saves a history snapshot.

`POST /api/lmstudio/start` starts the LM Studio server, `/api/lmstudio/unload` unloads models, `POST /api/runs/<id>/meta` changes label and notes, `DELETE /api/runs/<id>` moves a run to local trash, and `POST /api/shutdown` stops Wattever. These calls change local state. The active run cannot be edited or deleted. See [measurements](measurements.md) for compatibility and [architecture](architecture.md) for the system boundary.

## Configuration and examples

`LMS_BASE_URL` selects the LM Studio API (default `http://localhost:1234`). `GPU_BENCH_PORT` selects the starting local port and `GPU_BENCH_NO_BROWSER=1` suppresses the browser. A new benchmark accepts a JSON object such as:

```json
{"models":["<installed-model-id>"],"sizes":[512,5000],"concurrency_levels":[1,2],"max_tokens":256,"repeats":1}
```

Send it to `POST /api/bench/start` only when ready to run inference. Omitted configuration uses these defaults:

| Setting | Default |
| --- | --- |
| `sizes` | 512, 5000, 10000, 20000, 40000 |
| `concurrency_levels`, `repeats`, `max_tokens` | `[1]`, 1, 256 |
| `gpu_offload`, `fallback_on_failure`, `stop_model_after_failure` | `max`, true, true |
| `context_policy`, `context_headroom`, `context_safety_fraction` | `per_test`, 512, 0.05 |
| `mtp_modes`, `warmup`, `disable_thinking` | `["off"]`, true, true |
| `load_timeout_s`, `per_test_timeout_s`, `power_interval_ms` | 900, 1800, 150 |
| `gpu_index` | 0 (telemetry sensor only) |

For `POST /api/bench/extend-plan`, use a selection like:

```json
{"run_id":"<run-id>","model_modes":{"<installed-model-id>":["off","on"]},"sizes":[512,5000],"concurrency_levels":[1,2],"retry_failed":false,"rerun_existing":false}
```

Review the returned counts and warnings before submitting the selection to `/api/bench/extend`. **Add / rerun tests** modifies the existing benchmark after taking a snapshot; `/api/bench/resume` instead creates a separate run with unfinished work and a link to its source. Compatibility checks apply to both; see [measurements](measurements.md).
