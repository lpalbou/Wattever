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
single-client cases. Remaining contexts, concurrency, optimization and quality
measurements are pending at this checkpoint. Missing scores are not zeros.

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
