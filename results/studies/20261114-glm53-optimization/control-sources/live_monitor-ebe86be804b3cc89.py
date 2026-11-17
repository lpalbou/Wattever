"""Read-only live hardware and request telemetry for Strata Control."""
from __future__ import annotations

import csv
import io
import math
import subprocess
import time

import httpx
import psutil
from gateway_timings import valid_trace


def hardware_snapshot(runner=None):
    """System RAM plus per-GPU VRAM, util, temperature, and power from nvidia-smi."""
    memory = psutil.virtual_memory()
    gpus = []
    command = [
        'nvidia-smi',
        '--query-gpu=index,name,memory.used,memory.total,utilization.gpu,utilization.memory,temperature.gpu,power.draw',
        '--format=csv,noheader,nounits',
    ]
    try:
        if runner is None:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or 'nvidia-smi failed')
            stdout = result.stdout
        else:
            stdout = runner(command)
        for row in csv.reader(io.StringIO(stdout)):
            if len(row) != 8:
                continue
            index, name, used, total, gpu_util, mem_util, temp, power = [part.strip() for part in row]

            def number(value):
                if value in ('', 'N/A', '[N/A]', '[Not Supported]'):
                    return None
                return float(value)

            gpus.append({
                'index': int(index),
                'name': name,
                'memory_used_mib': number(used),
                'memory_total_mib': number(total),
                'gpu_util_pct': number(gpu_util),
                'memory_util_pct': number(mem_util),
                'temperature_c': number(temp),
                'power_w': number(power),
            })
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {
            'ram': {
                'used_bytes': memory.used,
                'available_bytes': memory.available,
                'total_bytes': memory.total,
                'percent': memory.percent,
            },
            'gpus': [],
            'error': str(exc),
            'checked': time.time(),
            'monotonic': time.monotonic(),
        }
    return {
        'ram': {
            'used_bytes': memory.used,
            'available_bytes': memory.available,
            'total_bytes': memory.total,
            'percent': memory.percent,
        },
        'gpus': gpus,
        'error': None,
        'checked': time.time(),
        'monotonic': time.monotonic(),
    }


REQUEST_DISPLAY_LIMIT = 200


def fetch_metrics(port=8081, client=None, gateway_port=8086):
    """Pull the model server's /metrics payload. Absence stays explicit."""
    get = client.get if client is not None else httpx.get
    try:
        # ?requests=all keeps every finished row the engine still has (default JSON is last 12 only).
        response = get(
            f'http://127.0.0.1:{port}/metrics?requests=all',
            timeout=1.2,
            trust_env=False,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError('Invalid metrics payload')
        data = merge_gateway_timings(data, get, gateway_port)
        return {'state': 'up', 'data': data, 'checked': time.time(), 'monotonic': time.monotonic()}
    except httpx.ConnectError:
        return {'state': 'absent', 'data': None, 'checked': time.time(), 'monotonic': time.monotonic()}
    except (httpx.HTTPError, ValueError) as exc:
        return {'state': 'unknown', 'data': None, 'error': str(exc),
                'checked': time.time(), 'monotonic': time.monotonic()}


def merge_gateway_timings(data, get, gateway_port=8086):
    """Join only opaque exact trace IDs; gateway absence never hides metrics."""
    rows = data.get('requests')
    if gateway_port is None or not isinstance(rows, list) or not any(
            isinstance(row, dict) and valid_trace(row.get('gateway_trace_id')) for row in rows):
        return data
    try:
        response = get(f'http://127.0.0.1:{gateway_port}/control/request-timings', timeout=.6, trust_env=False)
        response.raise_for_status()
        timings = response.json()
        if not isinstance(timings, dict) or timings.get('scope') != 'gateway_http':
            return data
        retained = timings.get('requests')
        if not isinstance(retained, list) or len(retained) > 500:
            return data
        indexed, duplicates = {}, set()
        for timing in retained:
            if not isinstance(timing, dict):
                continue
            trace = timing.get('trace_id')
            if not valid_trace(trace):
                continue
            if trace in indexed:
                duplicates.add(trace)
            indexed[trace] = timing
        joined = []
        for row in rows:
            row = dict(row) if isinstance(row, dict) else row
            if not isinstance(row, dict):
                joined.append(row)
                continue
            trace = row.get('gateway_trace_id')
            timing = indexed.get(trace) if trace not in duplicates else None
            if timing is not None and _number(timing.get('wall_s')) is not None:
                row.update(gateway_wall_s=timing['wall_s'], gateway_wall_scope='gateway_http',
                           gateway_started_at=_number(timing.get('started_at')),
                           gateway_finished_at=_number(timing.get('finished_at')),
                           gateway_outcome=timing.get('outcome'))
            joined.append(row)
        return dict(data, requests=joined)
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return data


def _bar(fraction, width=10):
    fraction = 0.0 if fraction is None else max(0.0, min(1.0, float(fraction)))
    filled = int(round(fraction * width))
    return '█' * filled + '░' * (width - filled)


def _gb(mib):
    if mib is None:
        return '–'
    return f'{mib / 1024:.1f}'


def format_hardware_lines(snapshot):
    """Compact human lines for the Hardware panel."""
    if not snapshot:
        return ['Hardware: waiting…']
    lines = []
    ram = snapshot.get('ram') or {}
    total = ram.get('total_bytes') or 0
    used = ram.get('used_bytes') or 0
    pct = (ram.get('percent') or 0) / 100.0
    lines.append(
        f"RAM  {_bar(pct)}  {used / (1024 ** 3):.1f}/{total / (1024 ** 3):.1f} GiB"
        f"  ({ram.get('percent', 0):.0f}%)"
    )
    if snapshot.get('error') and not snapshot.get('gpus'):
        lines.append('GPU  unavailable · ' + snapshot['error'])
        return lines
    for gpu in snapshot.get('gpus') or []:
        total = gpu.get('memory_total_mib') or 0
        used = gpu.get('memory_used_mib') or 0
        frac = (used / total) if total else 0.0
        util = gpu.get('gpu_util_pct')
        temp = gpu.get('temperature_c')
        power = gpu.get('power_w')
        util_text = '–' if util is None else f'{util:.0f}%'
        temp_text = '–' if temp is None else f'{temp:.0f}°C'
        power_text = '' if power is None else f'  {power:.0f} W'
        lines.append(
            f"GPU{gpu['index']} {gpu['name']}  {_bar(frac)}  "
            f"{_gb(used)}/{_gb(total)} GiB  util {util_text}  {temp_text}{power_text}"
        )
    return lines or ['GPU  none detected']


def _fmt_tok_s(value):
    return '–' if value is None else f'{value:.0f}'


def live_request_phase(state):
    """Human phase name for the status strip."""
    return {
        'reading': 'Prefill',
        'generating': 'Decode',
        'queued': 'Queued',
    }.get(state, str(state or 'Busy').replace('_', ' ').title())


def live_request_fraction(live):
    """0..1 progress for the status bar: prefill read/total, else decode generated/cap."""
    if not live or live.get('state') in (None, 'idle', 'unloaded'):
        return None
    state = live.get('state')
    if state == 'reading':
        read = live.get('prompt_read')
        total = live.get('prompt_total') or live.get('prompt_tokens')
        if read is not None and total and total > 0:
            return max(0.0, min(1.0, read / total))
    if state == 'generating':
        generated = live.get('generated')
        cap = live.get('max_tokens')
        if generated is not None and cap and cap > 0:
            return max(0.0, min(1.0, generated / cap))
    return None


def format_live_request_banner(live, engine=None):
    """Rich one-liner for Control's progress strip during an in-flight request."""
    if not live or live.get('state') in (None, 'idle', 'unloaded'):
        return None, None
    state = live.get('state')
    phase = live_request_phase(state)
    prompt_total = live.get('prompt_total') or live.get('prompt_tokens')
    pieces = []
    if state == 'reading':
        read = live.get('prompt_read')
        if read is not None and prompt_total is not None:
            pieces.append(f'Reading prompt {read:,} / {prompt_total:,} tk')
        elif prompt_total is not None:
            pieces.append(f'Prompt {prompt_total:,} tk')
        rate = live.get('prefill_tok_s_mean')
        if rate is not None:
            pieces.append(f'{_fmt_tok_s(rate)} tk/s prefill')
    elif state == 'generating':
        if prompt_total is not None:
            pieces.append(f'Context {prompt_total:,} tk')
        generated = live.get('generated')
        if generated is not None:
            cap = live.get('max_tokens')
            if cap:
                pieces.append(f'Generated {generated:,} / {cap:,} tk')
            else:
                pieces.append(f'Generated {generated:,} tk')
        rate = live.get('tok_s')
        if rate is not None:
            pieces.append(f'{_fmt_tok_s(rate)} tk/s decode')
    else:
        if prompt_total is not None:
            pieces.append(f'Prompt {prompt_total:,} tk')
        generated = live.get('generated')
        if generated is not None:
            pieces.append(f'Generated {generated:,} tk')
    elapsed = live.get('elapsed_s')
    if elapsed is not None:
        pieces.append(f'{elapsed:.1f}s elapsed')
    window = (engine or {}).get('context') or (engine or {}).get('max_context')
    if window and state == 'generating':
        pieces.append(f'max window {window:,} tk')
    detail = ' · '.join(pieces) if pieces else 'Serving a request…'
    return phase, detail


def format_live_request(live):
    """One line for the in-flight request, if any."""
    phase, detail = format_live_request_banner(live)
    if phase is None:
        return None
    return f"{time.strftime('%H:%M:%S')}  {phase}  {detail.replace(' · ', '  ')}"


# One finished request per row. "Prompt" is the whole input; "Cached" is the part
# reused from the conversation cache, so only Prompt - Cached is actually prefilled.
REQUEST_COLUMNS = ('Time', 'Condition', 'Prompt tk', 'Cached tk', 'Output tk',
                   'Prefill time (s)', 'Prefill tk/s', 'Decode time (s)', 'Decode tk/s', 'Wall time (s)')
REQUEST_TIMING_NOTE = (
    'Gateway E2E covers public gateway ingress through the final response body, including model loading and queueing; '
    'client network time is outside this measurement. Backend only excludes gateway model loading and gateway queueing. '
    'Active only also excludes backend preparation and queueing. '
    'Engine subtotal is prefill + decode, not full request time. Prefill speed uses newly read tokens; '
    'decode speed uses engine-generated tokens when reported. Missing measurements remain unavailable.'
)


def _number(value):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _tokens(value):
    value = _number(value)
    return int(value) if value is not None and int(value) == value else None


def _count(value):
    return '–' if value is None else f'{value:,}'


def _seconds(milliseconds):
    return None if milliseconds is None else milliseconds / 1000.0


def _fixed(value, digits=1):
    return '–' if value is None else f'{value:.{digits}f}'


def request_row(row):
    """Cells for one finished request, in REQUEST_COLUMNS order."""
    prompt = _tokens(row.get('prompt_total'))
    if prompt is None:
        prompt = _tokens(row.get('prompt_tokens'))
    seen = _tokens(row.get('prompt_tokens'))
    reused = _tokens(row.get('reused'))
    if reused is not None and prompt is not None and reused > prompt:
        reused = None
    output = _tokens(row.get('output_tokens'))
    prefill_s = _seconds(_number(row.get('prompt_ms')))
    decode_s = _seconds(_number(row.get('decode_ms')))
    # Tokens the engine really read this turn; older payloads report only the prompt size.
    read = _tokens(row.get('prompt_read'))
    if read is None and reused is not None:
        # On cancellation prompt_tokens is only the part actually processed.
        counted = seen if seen is not None else prompt
        if counted is not None and counted >= reused:
            read = counted - reused
    prefill_rate = _number(row.get('prefill_tok_s'))
    if read == 0:
        prefill_rate = None
    elif prefill_rate is None and read and prefill_s:
        prefill_rate = read / prefill_s
    generated = _tokens(row.get('engine_generated'))
    decode_rate = _number(row.get('decode_tok_s'))
    if generated in (0, 1) or output in (0, 1):
        decode_rate = None  # No meaningful first-to-last decode interval.
    elif decode_rate is None and generated is not None and generated >= 2 and decode_s:
        decode_rate = generated / decode_s
    condition = str(row.get('finish') or row.get('state') or 'done')
    wall = _number(row.get('gateway_wall_s')) if row.get('gateway_wall_scope') == 'gateway_http' else None
    if wall is not None:
        scope = 'gateway E2E'
        if (_tokens(row.get('http_request_engine_runs')) or 1) > 1:
            scope = 'shared gateway E2E'
        outcome = row.get('gateway_outcome')
        if outcome in ('disconnected', 'incomplete', 'error'):
            condition += ' / ' + outcome
    else:
        wall = _number(row.get('request_wall_s')) if row.get('wall_scope') == 'backend_http' else None
        scope = 'backend only'
        if wall is not None and (_tokens(row.get('http_request_engine_runs')) or 1) > 1:
            scope = 'shared backend only'
        if wall is None:
            wall = _number(row.get('duration_s'))
            scope = 'active only'
            if wall is None:
                wall = _seconds(_number(row.get('total_ms')))
                if wall is None and prefill_s is not None and decode_s is not None:
                    wall = prefill_s + decode_s
                scope = 'engine subtotal'
    if wall is not None:
        condition += ' · ' + scope
    started = _number(row.get('gateway_started_at')) if row.get('gateway_wall_scope') == 'gateway_http' else None
    if started is None:
        started = _number(row.get('request_started_at'))
    if started is None:
        started = _number(row.get('time'))
    try:
        started_text = '–' if started is None else time.strftime('%H:%M:%S', time.localtime(started))
    except (OverflowError, OSError, ValueError):
        started_text = '–'
    return [
        started_text,
        condition,
        _count(prompt), _count(reused), _count(output),
        _fixed(prefill_s), _fmt_tok_s(prefill_rate),
        _fixed(decode_s), _fmt_tok_s(decode_rate),
        _fixed(wall),
    ]


def request_table_rows(metrics_payload, limit=REQUEST_DISPLAY_LIMIT):
    """Finished request cells, shared by the native table and clipboard export."""
    rows = list((metrics_payload or {}).get('requests') or [])[:limit]
    return [request_row(row) for row in rows]


def request_table(metrics_payload, limit=REQUEST_DISPLAY_LIMIT):
    """Header plus newest-first rows of finished requests."""
    return [list(REQUEST_COLUMNS)] + request_table_rows(metrics_payload, limit)


def requests_tsv(metrics_payload, limit=REQUEST_DISPLAY_LIMIT):
    """Tab-separated table for the clipboard; pastes into a spreadsheet or chat."""
    return '\n'.join('\t'.join(cells) for cells in request_table(metrics_payload, limit))


def format_table(table):
    """Fixed-width text: first two columns left-aligned, numbers right-aligned."""
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]
    return ['  '.join(cell.ljust(width) if i < 2 else cell.rjust(width)
                      for i, (cell, width) in enumerate(zip(row, widths))).rstrip() for row in table]


def format_request_terminal(metrics_payload, limit=REQUEST_DISPLAY_LIMIT):
    """Live request line (if any), then a table of recent finished requests."""
    if not metrics_payload:
        return ['No model server metrics yet.']
    live = metrics_payload.get('live') or {}
    lines = []
    current = format_live_request(live)
    if current:
        lines.append(current)
    table = request_table(metrics_payload, limit)
    if len(table) > 1:
        lines.extend(format_table(table))
    if not lines:
        state = live.get('state') or 'idle'
        totals = metrics_payload.get('totals') or {}
        served = totals.get('requests')
        suffix = '' if served is None else f' · {served} served'
        lines.append(f'{time.strftime("%H:%M:%S")}  {state}{suffix}')
    return lines
