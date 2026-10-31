# Changelog

## Unreleased

- Measures sequential storage-to-RAM reads and pinned RAM-to-VRAM transfers before helper benchmarks, with raw samples, methods, capture time, unavailable states and UI/CSV reporting. Supports explicit later hardware-matched annotations on saved runs.
- Links engine-specific runs into one machine comparison while retaining each provider's settings and measurements.
- Captures generic endpoint quantization metadata in throughput result rows and accepts structured quantization formats during discovery.

- Adds total-repeat extension, adjustable retry timeouts and individual-test repair for managed benchmarks, preserving backups, successful measurements and replaced attempts. Managed requests honor the configured timeout; retry controls include explicit CPU-fallback and timeout-skipping policies.
- Captures quantization methods separately from storage formats, including LM Studio QAT, QAD and UD labels, and displays them before runs and throughout results. Catalog backfills retain explicit historical provenance.

- Discovers Results test models from the current authenticated endpoint, prevents stale selections after connection changes, and stages separate generic benchmark runs. Run and Results show quantization with endpoint-versus-model-ID provenance; successful discovery takes precedence over a manual fallback ID.

- Reports capability runner compatibility from the active helper process and blocks outdated launch/import flows with a relaunch message. Suite labels identify AI2 ARC-Challenge science questions separately from ARC-AGI; result headers show subset size, repeat count and descriptive uncertainty.

- Separates capability generation settings from speed tests, defaults to a 32k total thinking-and-answer budget with an optional deadline, and preserves completion metadata for empty final answers. Output limits remain distinct from request failures; new completion-policy fingerprints preserve historical audit compatibility.
- Aligns capability scorecards with a taller evaluation library, centers the workspace, and labels custom diagnostics separately from published dataset adaptations.

- Adds a visual capability dashboard with score/coverage tiles, category profiles, outcome breakdowns, repeat consistency and a filtered, paginated question-by-repeat matrix. An evidence inspector presents prompts, expected/actual output and rule explanations, with raw audit metadata collapsed. Compatible protocols support paired scored-answer deltas; live updates preserve filters and focus. Recorded scoring rules and protocol versions are unchanged.

- Resolves endpoint display names and stable opaque route IDs automatically from connection evidence, with honest generic fallbacks and one optional advanced display-name override. Manual endpoint naming is no longer required; private URL mappings stay outside shared results.

- Adds a capability evaluation tab with original Core tasks and checksum-pinned GSM8K/ARC-Challenge test splits, deterministic chat-adaptation grading, subset/repeat provenance, audit import/export and a read-only shared result library. These scores are separate from throughput and official leaderboard protocols.
- Adds public model/endpoint configuration fingerprints distinguishing requested, user-reported, verified and unknown settings, plus explicit reasoning-effort requests.
- Adds live CPU/GPU/RAM/VRAM telemetry and available temperatures, bounded recent history, whole-run observed means/peaks/coverage and descriptive early/late drift. Selected measures render in separate graphs.
- Identifies helper builds independently of release version, preserves active speed/capability runs during launch, and handles browser disconnects while sending response headers/body.
- Introduces browser timing protocol 2 with exact-usage-only throughput, explicit reasoning/output boundaries and public model configuration identities. Historical browser protocol 1 results remain readable and may include estimates.

- Adds generic OpenAI-compatible streaming Chat Completions to the Python helper, with optional discovery, manual model IDs, environment-based authentication and externally managed model settings. LM Studio remains a managed provider; generic experiments use new runs.
- Adds privacy-conscious CPU, RAM and PCIe provenance, optional user-reported reproducibility settings and sourced precision-specific compute claims. The additive metadata schema is version 1; benchmark schema 2 and historical files remain readable.
- Separates client timing provenance, provider-reported timings and cache usage. Generic throughput stays unavailable without server token usage; local power attribution requires explicit confirmation that inference runs on the benchmark computer.
- Excludes endpoint connection details and credentials from public results and exports.

- Keeps shared Results on GitHub-hosted catalog data, omits local execution diagnostics from public exports, and fails closed if the hosted data client is unavailable.

- Shows readable benchmark-save errors with optional technical details and refreshes shared data consistently after updates.

- Retains the latest RTX 5060 Ti session in the shared results library; removes older runs and preliminary snapshots.

- Adds a shared results catalog to the hosted Results view and a guide for contributing and continuing benchmark files.

## 2.4.1 - 2026-10-27

- Keeps viewport position and keyboard focus stable when toggling measures in Summary, Table and Compare.
- Includes a labelled preliminary RTX 5060 Ti result snapshot with its incompleteness and measurement limitations recorded.

- Preselects existing benchmark models when adding tests and shows each model's successful, failed, skipped and unfinished cases. New and unavailable models are distinguished; saved measurements remain intact when changing the selection.

## 2.4.0 - 2026-10-27

- Introduces measurement protocol 2, cross-platform hardware inventory, Linux support, concurrent queries and per-session provenance.
- Stores a benchmark in one JSON file and adds live case results, shared Results filters, chart comparisons and copyable tables.
- Adds same-file extensions, failed and skipped retries, selected-case reruns, attempt history and snapshots. Successful replacements remain separate from archived attempts.
- Plans shared KV capacity per concurrent request with a 5% prompt margin and supports a fixed context policy. Standard and MTP modes can be chosen per model.
- Adds hosted browser mode with direct OpenAI-compatible connections, local import/export and a separate measurement protocol.
- Adds adaptive request deadlines and verified model unload and worker recovery before continuing after a timeout.
- Adds a documentation set and an agent skill for the local API.
- Older concurrent failures may reflect legacy context allocation rather than a model capacity limit; use the session planner information when interpreting them.
- Older result schemas and planner settings remain readable. Extended files may contain sessions using different planners; compare provenance and settings before combining measurements.

## 1.0.0

- Initial release.
