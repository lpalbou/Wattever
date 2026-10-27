---
name: wattever
description: Operate Wattever local LM Studio benchmarks and interpret GPU throughput and power results. Use when inspecting sessions, planning workloads, or using its local HTTP API.
license: MIT
compatibility: Python 3.9+, LM Studio, local Wattever server; Windows, Linux, or macOS.
metadata:
  package-version: "2.4.0"
---

# Wattever

## Purpose and limits

Use Wattever to measure LM Studio prefill, decode and optional GPU power across models, prompt lengths and simultaneous queries. GPU sensor selection does not control inference placement; configure placement in LM Studio. GPU and system estimates are not wall-meter readings. Compare matching settings, model builds, corpus and protocol; inspect session provenance for mixed planner versions. The hosted browser mode uses a separate client-timed protocol and is not directly comparable to the helper.

## Install and verify

Extract the v2.4.0 release source archive or clone `https://github.com/lpalbou/Wattever.git`; install Python 3.9+ and LM Studio, download models and run `lms bootstrap`. Launch `Wattever.bat` on Windows, `Wattever.command` on macOS, or `sh Wattever.sh` on Linux, or run `python -m wattever.server` from the repository. Keep the server open. Starting can reuse an existing instance and replace an idle older instance; do not restart it while a benchmark is active.

Check the printed URL with this read-only probe. Replace the URL with the printed address; the server may select a different port. Compare the printed version with `metadata.package-version`; the installed version's documentation wins when they differ.

```python
import json
import urllib.error
import urllib.request

base = "http://127.0.0.1:8713"  # Replace with the printed local URL.
try:
    with urllib.request.urlopen(base + "/api/version", timeout=3) as response:
        data = json.load(response)
    if not isinstance(data, dict) or data.get("application") != "Wattever" or not data.get("version"):
        raise ValueError("Not a Wattever version response")
    print("Wattever", data["version"])
except (urllib.error.URLError, OSError, ValueError) as exc:
    print("Wattever unavailable or invalid response:", exc)
    raise SystemExit(1)
```

## Mental model

- One benchmark file contains cases (model, Standard/MTP mode, prompt size, concurrency, repeat) and execution sessions. Live results appear as cases finish.
- Defaults: one query, one repeat, 256 output tokens, per-test context, 512 headroom and 5% prompt margin. Shared KV capacity scales with concurrency. Fixed context uses the largest allocation per model and mode.
- MTP is off by default and requires a supported bundled head. Warmup is on; placement prefers GPU with fallback. Load and test timeouts default to 900 and 1800 seconds.
- Errors return JSON `error` with HTTP 400 for invalid inputs or conflicts, 404 for missing runs, 409 for active-run edits or deletion, and 500 for unexpected failures. Handle connection and JSON errors too.

## Capability map

- Hosted browser mode: open `https://lpalbou.github.io/Wattever/`, connect explicitly to an OpenAI-compatible endpoint, and use browser storage and JSON import/export. It requires CORS and may require browser local-network permission. No helper install is needed. The optional key is memory-only; do not put it in conversation or result files.
- Read version, environment, models, status, events and runs via GET endpoints in the API reference. `GET /api/bench/extend-plan?run_id=<id>` returns extension options.
- Start, cancel, resume, preview and extend through `/api/bench/*`; these execute inference or change state except planning. Add missing, retry failed/skipped, or rerun selected cases.
- Edit metadata, delete to local trash, export JSON/CSV, start or unload LM Studio, and stop Wattever through the local API. Results and snapshots are written under `results/`.
- There is no control of LM Studio placement through the GPU sensor index and no combined measurement of all GPUs. Hardware discovery and power availability are best effort.
- Browser mode has manual hardware descriptions and no power telemetry, model load/unload, MTP or reasoning control, resume or same-file extension. Provider settings govern model execution and context. Closing the page stops its run; download in-memory results if storage fails.

## Workflow

1. Inspect `/api/version`, `/api/status`, `/api/env` and `/api/models` before acting. An empty model list means check the LM Studio server and downloaded models.
2. Choose the requested models, sizes, concurrency and placement. Starting a run can take a long time, load and unload models and write files. Never interrupt an active run for inspection.
3. Use the Run page or `POST /api/bench/start` with the chosen configuration. Poll `/api/events?cursor=0`, then pass back the returned cursor, or read `/api/status`. Read the saved run by ID after completion and report failures and missing power honestly.
4. For a continuation, use **Add / rerun tests** in Results or obtain extension options and submit a plan with `run_id`, `model_modes` (IDs mapped to arrays of `off`/`on`), `sizes`, `concurrency_levels`, and optional boolean `retry_failed` or `rerun_existing`. Review plan counts before `POST /api/bench/extend` with the same selection. Default adds missing cases; retries include failed and skipped; rerun includes successes. Each replaced case moves to `previous_attempts` and is not averaged with the replacement. An exact snapshot is kept in `results/.history/`.
5. Compare only compatible results. Extension and resume check hardware, GPU/driver, corpus and LM Studio version. Review a result before sharing it.

## Rules and sharp edges

Do not start, cancel, unload models, delete runs, or shut down a server without authorization for that action. A benchmark is resource intensive and modifies LM Studio state. The selected sensor measures one GPU only. Missing power remains unavailable. Model execution may use other GPUs or CPU; record the actual setup. Failed and skipped cases are results, not zero performance. An extended legacy benchmark can contain different planner versions; inspect each session.

The helper applies a deadline based on prior comparable prefill and a decode inactivity limit. It verifies model unload and request worker exit before continuing; if recovery cannot be verified it stops. Browser timeouts abort requests and stop the run but cannot verify provider-side cancellation or unload; check the provider before restarting.

## When things fail

- No models: start LM Studio and confirm downloads and `LMS_BASE_URL`.
- CLI missing: run `lms bootstrap` and relaunch.
- Power unavailable: check `nvidia-smi`; on macOS optional `powermetrics` requires authorization. Speed can still be measured.
- Context/load failure: reduce size or concurrency, or choose a smaller model. Retry after changing conditions.
- Extension compatibility error: start a separate benchmark when hardware, driver, corpus or LM Studio version differs or cannot be verified.

## Go deeper

- [Getting started](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/docs/getting-started.md): setup and first run.
- [Browser mode](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/docs/browser.md): hosted setup and limitations.
- [Measurements](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/docs/measurements.md): metric boundaries and session semantics.
- [API](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/docs/api.md): routes and side effects.
- [Troubleshooting](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/docs/troubleshooting.md): symptoms and remedies.
- [Full documentation](https://raw.githubusercontent.com/lpalbou/Wattever/v2.4.0/llms-full.txt): complete context.
