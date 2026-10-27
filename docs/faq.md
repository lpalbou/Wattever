# FAQ

## Does the selected GPU determine where inference runs?

No. It selects a telemetry sensor. Configure LM Studio placement separately and record the setup in notes. See [measurements](measurements.md).

## Are power numbers comparable?

GPU board and SoC rail measurements have different boundaries; estimated whole-system power is not a wall meter and only includes the selected GPU. Compare like sources and settings.

## Can I compare old and new files?

Older files remain readable. Compare protocol, model, corpus, settings, context planner and session provenance; legacy and newer context allocation can differ within an extended benchmark. See [measurements](measurements.md).

## Does a failed case invalidate the benchmark?

No. Failures and skipped cases are recorded and can be retried. See [troubleshooting](troubleshooting.md) for common causes.
