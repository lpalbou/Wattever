# Concurrency comparisons

Use **Results → Compare → Concurrency scaling** to see how much work an endpoint completes as simultaneous requests increase, and what each user experiences. The x axis is the number of client requests. Choose a fixed **Prompt per request** before comparing: five 20k prompts are a different workload from five 40k prompts. **Prompt length** opens the context-size comparison with a selected request count. These views use saved results and launch no inference.

## Read throughput and latency together

Four measure tabs separate the questions:

| View | Charts | Interpretation |
|---|---|---|
| Throughput | Total output throughput; speed for each request | Compare total useful output with the generation experience of an individual user. |
| Prefill | Client prompt-processing rate; wait for the first token | See the effect of queueing and prompt admission. Client prefill includes transport and scheduling. |
| Latency | Wait for the first token; wait for a complete response | Inspect typical waits and the fastest/slowest observed requests. Range whiskers are observations, not confidence intervals. |
| Efficiency | Throughput gain over one request; whole-group GPU joules per output token | Compare matched-repeat scaling and measured energy, including prompt processing. The dotted linear line is a reference, not a prediction. |

**Best recorded total** identifies the observed throughput peak among shown conditions. **Each request at that point** reports its median client decode rate. **Highest successful load** is the largest completed request count in the selection, not a verified maximum capacity. More concurrent work can improve total throughput while increasing latency and reducing each request's speed.

The **Concurrency map** shows all selected models and recorded conditions against request counts. Change **Show** to color cells by a different measure. Colors are relative within each row; use the printed values for comparisons between rows. Failed/timed-out, skipped/cancelled, unmeasured and unavailable cells have distinct labels. An asterisk marks estimated output counts. Missing cases are never zero-speed measurements.

Select a map cell or chart point to open **Request evidence**. It includes each request's actual prompt/output counts, TTFT, decode rate, completion duration and status. Timing bars separate the wait for first output from the subsequent response interval. The latter includes scheduler pauses; it does not indicate continuous GPU activity. Each bar starts at that request's own start because absolute dispatch offsets are not retained. First/median/last first-token waits expose uneven admission without inventing a common timing origin.

## Compare recorded execution conditions

Models, quantizations, engines, reasoning, MTP, KV precision, endpoint identities and recorded GPU offload remain separate conditions. GPU offload is printed in the legend/map when recorded. A partial-offload condition can include substantial CPU work and must be distinguished from full-GPU execution. Unreported settings remain unknown; the dashboard cannot authenticate the underlying weights or effective provider settings.

Successful complete groups contribute to median throughput and latency. Concurrent completeness requires a recorded completed-request count or a full set of successful request records; otherwise the group is labeled unverified. Failed, incomplete and unverified groups remain inspectable, including their surviving requests, but do not count as successful scaling or capacity. Throughput gain and decode retention pair successful repeat IDs with an equivalent one-request baseline, prompt target, per-request context allocation, placement and power scope. Unmatched repeats and estimated output counts do not produce a measured scaling claim.

Whole-group throughput uses total output tokens divided by group elapsed time, including prefill and queueing. It is never the sum of per-request decode rates. After-first-token decode includes waiting while other prompts are processed; backend prompt-evaluation timing, when reported, is separate. See [Measurements and sessions](measurements.md) for exact timing and token scopes.

GPU energy includes prompt processing and uses the shared group measurement once. The energy chart requires known scope, complete GPU power coverage and actual output counts; missing or low-confidence energy stays unavailable. Per-card measurements and global integration boundaries are described in [Measurements and sessions](measurements.md#hardware-power-and-provenance).

Use **Export this slice** for CSV across all shown models/conditions at the selected prompt target. It includes metrics, status, repeat counts, matched-baseline counts, configuration provenance and power scope. JSON export retains original observations. Comparing saved results changes no historical data. See [Sharing results](sharing-results.md) to publish reviewed datasets.
