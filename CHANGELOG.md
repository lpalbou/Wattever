# Changelog

## Unreleased

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
