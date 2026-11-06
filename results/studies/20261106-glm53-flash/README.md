# GLM-5.3-Flash diagnostic snapshots

Read the [engineering report](../../../docs/glm53-flash-comparison.md) for the
configuration, first observations, Flash-Next comparison and limitations.

`manifest.json` lists immutable snapshot checksums. Each snapshot has compressed
JSON and a CSV summary. JSON retains the privacy-filtered Wattever run, actual
counts, per-request timings, backend timings, MTP counters, per-card power and raw
telemetry. A snapshot is a view of an evolving diagnostic run: overlapping
snapshots are **not additional repeats** and must not be pooled as independent
measurements. Normal machine aggregates remain separate.

The initial `baseline-first-two` snapshot contains exactly two successful
single-client cases. `baseline-full-twelve` retains all twelve baseline attempts:
eleven successes and the two-client 80k deadline failure. The first snapshot
retains the initial session's raw telemetry; the final snapshot's history covers
the expansion session. Their shared cases are not additional repeats.
`runtime1156-oldpack-two` adds two distinct updated-runtime conditions while
retaining the original pack and RAM request. F32-pack, RAM optimization and quality
measurements are pending. Missing scores are not zeros. `figures/` keeps each metric in
a separate chart, with prompt targets as facets and submitted clients on the x-axis.
`baseline-tail-storage.json.gz` retains read/write byte counters from both physical
disks during the end of the final baseline case and its idle transition. It is
an OS activity observation, not cache-bypassed storage bandwidth or per-process I/O.

Decompress a snapshot with Python:

```python
import gzip, json
from pathlib import Path
folder = Path("results/studies/20261106-glm53-flash")
capture = json.loads(gzip.decompress((folder / "baseline-first-two.json.gz").read_bytes()))
```

Preserve quantization, backend, context, serving slots, reasoning treatment, MTP,
cache placement and actual counts when comparing. The requested 40 GiB host tier
is distinct from the rounded 20.46 + 7.25 GiB actually pinned. This study uses the
existing AirDisk NVMe; it contains no Samsung 990 Pro measurement.
