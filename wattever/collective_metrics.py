"""Collective prefill/decode rates for multi-request benchmark cases.

For concurrency N>1, phase throughput uses unions of client-observed windows.
Prefill is a TTFT proxy including queue/network time and may overlap another
request's decode; it is not isolated engine prompt-processing time.
N=1 keeps each stream's client-observed prefill/decode rates.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

_SOURCES = {
    "prefill_tps": "client_ttft_prefill_proxy; sum(prompt_tokens)/union_of_request_start_to_first_observed_output_intervals; includes_queue_network_and_possible_other_request_decode",
    "decode_tps": "sum(decode_token_count)/union_of_observed_decode_intervals",
    "group_output_tps": "sum(completion_tokens)/group_wall_s including queue and prefill",
}
_MAX_SAFE_INTEGER = 2 ** 53 - 1


def observed_output_protocol(item: Dict[str, Any]) -> bool:
    """Only an explicit machine-readable boundary identifies the new timer."""
    from . import PHASE_PROTOCOL
    scope = item.get("timing_scope")
    return (isinstance(scope, dict) and scope.get("phase_protocol") == PHASE_PROTOCOL
            and scope.get("decode_end_boundary") == "last_observed_nonempty_output")


def _finite(values: Iterable[Any]) -> List[float]:
    out: List[float] = []
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                number = float(value)
            except OverflowError:
                continue
            if math.isfinite(number) and number >= 0:
                out.append(number)
    return out


def _median(values: Iterable[Any]) -> Optional[float]:
    sorted_vals = sorted(_finite(values))
    if not sorted_vals:
        return None
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2:
        return sorted_vals[mid]
    return sorted_vals[mid - 1] + (sorted_vals[mid] - sorted_vals[mid - 1]) / 2.0


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _sum_int(values: Iterable[Any]) -> Optional[int]:
    nums = []
    for value in values:
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or
                value < 0 or value > _MAX_SAFE_INTEGER or not math.isfinite(value) or int(value) != value):
            return None
        nums.append(int(value))
    total = sum(nums)
    return total if nums and total <= _MAX_SAFE_INTEGER else None


def request_rows(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    concurrency = int(row.get("concurrency") or 1)
    requests = row.get("requests")
    if isinstance(requests, list) and requests:
        return [item for item in requests if isinstance(item, dict)]
    if concurrency == 1:
        return [row]
    return []


def _union_duration(intervals):
    intervals.sort()
    start, end = intervals[0]
    duration = 0.0
    for next_start, next_end in intervals[1:]:
        if next_start <= end:
            end = max(end, next_end)
        else:
            duration += end - start
            start, end = next_start, next_end
    return duration + end - start


def collective_prefill_phase(requests: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Client TTFT proxy, not engine-only prompt processing."""
    if not requests:
        return None
    tokens = _sum_int(item.get("prompt_tokens") for item in requests)
    if tokens is None:
        return None
    intervals = []
    for item in requests:
        offset, ttft = item.get("start_offset_s"), item.get("ttft_s")
        if len(_finite([offset, ttft])) != 2 or ttft <= 0:
            return None
        wall = item.get("wall_s")
        if wall is not None and (not _number(wall) or wall < 0 or ttft > wall + 1e-6):
            return None
        end = offset + ttft
        if not math.isfinite(end) or end <= offset:
            return None
        intervals.append((offset, end))
    duration = _union_duration(intervals)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(tokens / duration):
        return None
    return {"tps": tokens / duration, "prefill_s": duration, "prefill_tokens": tokens,
            "prompt_tokens": tokens}


def collective_decode_phase(requests: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return scoped tokens and union duration for successful observed intervals."""
    if not requests:
        return None
    tokens = _sum_int(item.get("decode_token_count") for item in requests)
    if tokens is None:
        return None
    intervals = []
    for item in requests:
        offset, ttft, duration = (item.get(key) for key in ("start_offset_s", "ttft_s", "decode_s"))
        if len(_finite([offset, ttft, duration])) != 3 or duration <= 0:
            return None
        wall = item.get("wall_s")
        if wall is not None and (not _number(wall) or wall < 0 or ttft + duration > wall + 1e-6):
            return None
        start = offset + ttft
        end = start + duration
        if not math.isfinite(start) or (ttft > 0 and start <= offset) or not math.isfinite(end) or end <= start:
            return None
        intervals.append((start, end))
    duration = _union_duration(intervals)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(tokens / duration):
        return None
    return {"tps": tokens / duration, "decode_s": duration, "decode_tokens": tokens}


def collective_row_metrics(row: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
    """Return derived row metrics from per-request observations."""
    rows = request_rows(row)
    concurrency = int(row.get("concurrency") or (len(rows) if len(rows) > 1 else 1))
    if concurrency <= 1:
        return {}
    ok = [item for item in rows if item.get("status") == "ok"]
    if not ok:
        return {}

    requests = row.get("requests")
    complete = (row.get("status") == "ok" and isinstance(requests, list)
                and len(requests) == concurrency and len(rows) == concurrency and len(ok) == concurrency)
    if not complete and (not partial or row.get("status") == "ok"):
        return {}

    wall = row.get("wall_s")
    completion = _sum_int(item.get("completion_tokens") for item in ok)

    ttfts = [item.get("ttft_s") for item in ok]
    prompt_counts = [item.get("prompt_tokens") for item in ok]
    prompt_median = _median(prompt_counts) if _sum_int(prompt_counts) is not None else None
    metrics: Dict[str, Any] = {
        "ttft_s": _median(ttfts) if len(_finite(ttfts)) == len(ok) else None,
        "prompt_tokens": prompt_median,
    }
    if completion is not None:
        metrics["completion_tokens"] = int(completion)
        if len(_finite([wall])) == 1 and wall > 0 and math.isfinite(completion / wall):
            metrics["group_output_tps"] = completion / wall
    phase = collective_decode_phase(ok)
    if phase is not None:
        metrics["decode_tps"] = phase["tps"]
    prefill = collective_prefill_phase(ok)
    if prefill is not None:
        metrics["prefill_tps"] = prefill["tps"]
    return {key: value for key, value in metrics.items() if value is not None}


def enrich_result_row(row: Dict[str, Any], *, partial: bool = False) -> bool:
    """Explicitly derive collective metrics, preserving replaced observations."""
    updates = collective_row_metrics(row, partial=partial)
    changed = False
    scope = row.get("metric_sources")
    if not isinstance(scope, dict):
        scope = {}
    def source_note(key):
        note = _SOURCES[key]
        if row.get("status") != "ok":
            note += "; successful requests only; failed or cancelled work is not counted"
        if key == "decode_tps" and any(not observed_output_protocol(item)
                                       for item in request_rows(row) if item.get("status") == "ok"):
            note += "; approximate: recorded historical decode_s may include trailing transport and yield a lower rate"
        return note
    if (row.get("concurrency") or 1) > 1:
        for key in ("prefill_tps", "decode_tps"):
            old_source = scope.get(key)
            legacy = isinstance(old_source, str) and "group_wall_clock" in old_source
            replacing = key in updates and (row.get(key) != updates[key] or old_source != source_note(key))
            if row.get(key) is not None and (legacy or replacing):
                recorded_key = "recorded_" + key
                if recorded_key not in row:
                    row[recorded_key] = row[key]
                    changed = True
                if old_source is not None:
                    recorded = row.setdefault("recorded_metric_sources", {})
                    if isinstance(recorded, dict) and key not in recorded:
                        recorded[key] = old_source
                        changed = True
                if legacy and key not in updates:
                    row[key] = None
                    scope[key] = "unavailable; recorded legacy group-wall metric is not a phase rate"
                    changed = True
    for key, value in updates.items():
        if row.get(key) != value:
            row[key] = value
            changed = True
    if changed and row.get("metric_sources") is not scope:
        row["metric_sources"] = scope
    if row.get("concurrency", 1) > 1 and updates:
        for key, note in _SOURCES.items():
            note = source_note(key)
            if key in updates and scope.get(key) != note:
                scope[key] = note
                changed = True
        if row.get("metric_sources") is not scope:
            row["metric_sources"] = scope
            changed = True
    return changed


def _walk_result_rows(run: Dict[str, Any]):
    for row in run.get("results") or []:
        if not isinstance(row, dict):
            continue
        yield row
        for attempt in row.get("previous_attempts") or []:
            if isinstance(attempt, dict):
                yield attempt


def enrich_run(run: Dict[str, Any], *, partial: bool = False) -> int:
    changed = 0
    for row in _walk_result_rows(run):
        if enrich_result_row(row, partial=partial):
            changed += 1
    return changed
