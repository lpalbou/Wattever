# Changelog

## 2.4.0 - 2026-10-27

- Introduces measurement protocol 2, cross-platform hardware inventory, Linux support, concurrent queries and per-session provenance.
- Stores a benchmark in one JSON file and adds live case results, shared Results filters, chart comparisons and copyable tables.
- Adds same-file extensions, failed and skipped retries, selected-case reruns, attempt history and snapshots. Successful replacements remain separate from archived attempts.
- Plans shared KV capacity per concurrent request with a 5% prompt margin and supports a fixed context policy. Standard and MTP modes can be chosen per model.
- Adds a documentation set and an agent skill for the local API.
- Older concurrent failures may reflect legacy context allocation rather than a model capacity limit; use the session planner information when interpreting them.
- Older result schemas and planner settings remain readable. Extended files may contain sessions using different planners; compare provenance and settings before combining measurements.

## 1.0.0

- Initial release.
