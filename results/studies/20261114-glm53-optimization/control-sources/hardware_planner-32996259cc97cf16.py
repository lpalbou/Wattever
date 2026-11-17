"""Read model metadata and budget each detected device before a Control load.

No lifecycle operations live here. VRAM is budgeted per device: Flash duplicates
dense/KV state on a split, while q36 keeps that state on its main device.
"""
from __future__ import annotations

import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import struct

GiB = 1024 ** 3
MiB = 1024 ** 2
_FORMATS = {0: 'B', 1: 'b', 2: 'H', 3: 'h', 4: 'I', 5: 'i', 6: 'f',
            7: '?', 10: 'Q', 11: 'q', 12: 'd'}


def memory_inventory():
    import psutil
    vm = psutil.virtual_memory()
    return {'total': vm.total, 'available': vm.available}


def parse_ram_use(value='auto'):
    """Return an explicit percentage, or None for conservative Auto planning."""
    if isinstance(value, str) and value.strip().lower() == 'auto':
        return None
    if isinstance(value, bool):
        raise ValueError('RAM cache use must be Auto or 0–99.9%, with at most one decimal place')
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or not 0 <= number <= Decimal('99.9'):
            raise InvalidOperation()
        if number != number.quantize(Decimal('.1')):
            raise InvalidOperation()
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError('RAM cache use must be Auto or 0–99.9%, with at most one decimal place') from None
    return float(number)


def ram_budget_preview(total_bytes, available_bytes, device_count, ram_use_percent='auto',
                       conversation_cache_mib=0, packed=True, expert_bytes=None):
    """Pure memory policy shared by the UI preview and the actual load planner.

    Percentages control the explicit expert allocation, not total process/OS RAM.
    Manual values opt into using spare RAM; Auto retains the conservative guards.
    Windows may use otherwise available RAM for file-backed expert pages at 0%.
    """
    for value in (total_bytes, available_bytes):
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid available RAM inventory')
    if available_bytes > total_bytes:
        raise ValueError('Available RAM exceeds total RAM')
    if type(device_count) is not int or device_count < 1:
        raise ValueError('No GPUs available for the model')
    try:
        cache_mib = int(conversation_cache_mib)
        if str(conversation_cache_mib) != str(cache_mib) or not 0 <= cache_mib <= 1048576:
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Invalid conversation cache budget') from None
    percent = parse_ram_use(ram_use_percent)
    if percent == 0 and not packed:
        raise ValueError('This model requires a RAM expert cache; 0% file-backed mode is unavailable')
    base = max(2 * GiB, int(device_count * .75 * GiB)) + cache_mib * MiB
    available_for_cache = max(0, int(available_bytes) - base)
    if percent is None:
        headroom = max(4 * GiB, int(total_bytes * .08))
        runtime = max(2 * GiB, int(total_bytes * .07)) if packed else 0
        overhead = base + runtime
        budget = max(0, int(available_bytes) - headroom - overhead)
        mode = 'auto'
    else:
        runtime = 0
        overhead = base
        budget = int(available_for_cache * percent / 100)
        headroom = available_for_cache - budget
        mode = 'file' if percent == 0 else 'resident'
        if available_bytes <= base:
            raise ValueError('Insufficient available RAM for required runtime buffers')
    if expert_bytes is not None and packed:
        budget = min(int(expert_bytes), budget)
    # Native zero means "no numeric budget supplied", not a zero-byte cache.
    # File-backed mode is represented by bare --mmap-experts instead.
    if percent is not None and percent > 0 and budget < MiB:
        raise ValueError('Insufficient available RAM for a nonzero expert cache; use 0% file-backed mode')
    return {'ram_budget_bytes': budget, 'ram_headroom_bytes': headroom,
            'host_overhead_bytes': overhead, 'runtime_host_allowance_bytes': runtime,
            'ram_cache_available_bytes': available_for_cache,
            'ram_use_percent': percent, 'ram_cache_mode': mode}


def flash_tuning_args(advanced, mtp=True, logical_cpus=None):
    """Validated expert controls; Auto leaves native hardware tuning enabled."""
    result = {}
    if mtp:
        depth = str(advanced.get('mtp_depth', '4'))
        if depth not in ('2', '3', '4'):
            raise ValueError('MTP verify window must be 2, 3 or 4 tokens')
        try:
            confidence = float(advanced.get('mtp_min_p', '0.5'))
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Draft confidence must be a finite number between 0 and 1') from None
        result.update({'--spec': depth, '--spec-min-p': f'{confidence:g}'})
    workers = str(advanced.get('cpu_workers', 'auto')).strip().lower()
    if workers != 'auto':
        available = logical_cpus if logical_cpus is not None else (os.cpu_count() or 1)
        if not workers.isdigit() or not 1 <= int(workers) <= available:
            raise ValueError(f'CPU expert workers must be Auto or between 1 and {available}')
        result['--pool-workers'] = str(int(workers))
    share = str(advanced.get('pcie_fraction', 'auto')).strip().lower()
    if share != 'auto':
        try:
            number = float(share)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('GPU share of cache misses must be Auto or between 0 and 1') from None
        result['--pcie-frac'] = f'{number:g}'
    return result


def gguf_header(path):
    """Read scalar metadata and tensor byte spans, never tensor contents."""
    path = Path(path)
    file_size = path.stat().st_size
    with path.open('rb') as stream:
        def number(fmt):
            raw = stream.read(struct.calcsize(fmt))
            if len(raw) != struct.calcsize(fmt):
                raise ValueError('Truncated GGUF header')
            return struct.unpack('<' + fmt, raw)[0]

        def string(keep=True):
            count = number('Q')
            if count > file_size - stream.tell():
                raise ValueError('Invalid GGUF string')
            if not keep:
                if count < MiB:
                    stream.read(count)
                else:
                    stream.seek(count, 1)
                return None
            if count > MiB:
                raise ValueError('Oversized GGUF metadata string')
            return stream.read(count).decode('utf-8')

        def value(kind, keep=True):
            if kind in _FORMATS:
                return number(_FORMATS[kind])
            if kind == 8:
                return string(keep)
            if kind == 9:
                item, count = number('I'), number('Q')
                if count > 16_000_000:
                    raise ValueError('Oversized GGUF metadata array')
                if item in _FORMATS:
                    stream.seek(struct.calcsize(_FORMATS[item]) * count, 1)
                elif item == 8:
                    for _ in range(count):
                        string(False)
                else:
                    raise ValueError('Unsupported GGUF array')
                return None
            raise ValueError('Unsupported GGUF metadata type')

        if stream.read(4) != b'GGUF' or number('I') not in (2, 3):
            raise ValueError('Unrecognized GGUF model')
        count, nmeta = number('Q'), number('Q')
        if count > 1_000_000 or nmeta > 100_000:
            raise ValueError('Invalid GGUF header counts')
        metadata = {}
        for _ in range(nmeta):
            key, kind = string(), number('I')
            val = value(kind, not key.startswith('tokenizer.'))
            if val is not None and not key.startswith('tokenizer.'):
                metadata[key] = val
        tensors = []
        for _ in range(count):
            name, ndims = string(), number('I')
            if not 1 <= ndims <= 8:
                raise ValueError('Invalid GGUF tensor dimensions')
            shape = [number('Q') for _ in range(ndims)]
            kind, offset = number('I'), number('Q')
            tensors.append({'name': name, 'shape': shape, 'type': kind, 'offset': offset})
        alignment = int(metadata.get('general.alignment', 32))
        if alignment <= 0 or alignment > MiB:
            raise ValueError('Invalid GGUF alignment')
        start = (stream.tell() + alignment - 1) // alignment * alignment
        tensors.sort(key=lambda t: t['offset'])
        for i, tensor in enumerate(tensors):
            end = tensors[i + 1]['offset'] if i + 1 < len(tensors) else file_size - start
            tensor['bytes'] = max(0, end - tensor['offset'])
    return metadata, tensors


def model_memory(spec, model_root):
    root = Path(model_root)
    metadata, tensors = {}, []
    for relative in spec.weights:
        values, shard = gguf_header(root / relative)
        metadata.update(values)
        tensors.extend(shard)
    arch = metadata.get('general.architecture', '')
    def field(name, default=0):
        return metadata.get(arch + '.' + name, default)
    packed = bool(spec.packed)
    experts = sum(t['bytes'] for t in tensors if '_exps.' in t['name'])
    if packed:
        header = (root / 'packs' / spec.pack / 'native_experts.txt').read_text(encoding='utf-8').splitlines()[0]
        match = re.search(r'total\s+(\d+)', header)
        if not match:
            raise ValueError('Expert pack has no byte count')
        experts = int(match.group(1))
    dense = sum(t['bytes'] for t in tensors if '_exps.' not in t['name']
                and 'per_layer_token_embd' not in t['name']
                and (not packed or t['name'] != 'token_embd.weight'))
    if not packed:
        # q36 builds additional packed decode projections from GGUF tensors.
        # Their allocation is larger than the file spans alone.
        dense = int(dense * 1.8)
    layers = int(field('block_count'))
    interval = int(field('full_attention_interval', 4))
    kv_heads = int(field('attention.head_count_kv'))
    key = int(field('attention.key_length', int(field('embedding_length', 0)) // max(1, int(field('attention.head_count', 1)))))
    val = int(field('attention.value_length', key))
    attention_layers = max(1, layers // max(1, interval))
    # q36 stores FP32 K/V plus FP16 prompt copies; Flash's int8 cache
    # also carries per-group scales and a draft layer's cache.
    kv = ((attention_layers + 1) * kv_heads * (key + val) * 4
          + kv_heads * (key + val) * 5) if not packed else int((attention_layers + 1) * kv_heads * (key + val) * 1.25)
    return {'expert_bytes': experts, 'dense_bytes': dense, 'kv_bytes_per_token': kv,
            'architecture': arch, 'layers': layers, 'metadata': metadata}


def plan(spec, model_root, placement, maximum, context='Auto', mtp=True,
         advanced=None, memory=None, metadata=None):
    """Return a fresh resource plan; injections make this testable without CUDA."""
    if memory is None:
        memory = memory_inventory()
    stats = metadata if metadata is not None else model_memory(spec, model_root)
    advanced = dict(advanced or {})
    maximum = int(maximum)
    for key in ('total', 'available'):
        if type(memory.get(key)) not in (int, float) or not math.isfinite(memory[key]) or memory[key] < 0:
            raise ValueError('Invalid available RAM inventory')
    if memory['available'] > memory['total']:
        raise ValueError('Available RAM exceeds total RAM')
    if maximum < 1024:
        raise ValueError('Invalid model context limit')
    auto = context in (None, 'Auto', 'auto')
    requested = maximum if auto or context == 'Max' else int(context)
    if not 1024 <= requested <= maximum:
        raise ValueError(f'Context must be between 1,024 and the model limit of {maximum:,} tokens')
    cards = [dict(c) for c in placement['cards']]
    if not cards:
        raise ValueError('No GPUs available for the model')
    dense = int(stats['dense_bytes'])
    experts = int(stats['expert_bytes'])
    kv = int(stats['kv_bytes_per_token'])
    if min(dense, experts, kv) < 0 or kv == 0:
        raise ValueError('Invalid model memory metadata')
    reserve_setting = advanced.get('vram_reserve_mib', 'auto')
    if reserve_setting != 'auto':
        try:
            if not 0 <= int(reserve_setting) <= 1048576:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Invalid VRAM reserve') from None
    free = {c['index']: int(float(c.get('free_mib', c['memory_mib'])) * MiB) for c in cards}
    reserve = {c['index']: (max(512 * MiB, int(c['memory_mib'] * MiB * 0.075))
                            if reserve_setting == 'auto' else int(reserve_setting) * MiB) for c in cards}
    reserve = {index: value // MiB * MiB for index, value in reserve.items()}
    main = placement['main_gpu']
    packed = bool(spec.packed)
    # Allocator/driver scratch, recurrent state and prompt buffers. This is
    # resource overhead, not a model-specific context ceiling.
    overhead = {c['index']: dense + int(1.75 * GiB) + (int(1.0 * GiB) if mtp and c['index'] == main else 0)
                for c in cards}
    if not packed:
        overhead = {c['index']: dense + int(4.5 * GiB) for c in cards}
    excluded = []
    if packed and auto:
        viable = [c for c in cards if free[c['index']] - reserve[c['index']] - overhead[c['index']] >= kv * 1024 + 512 * MiB]
        excluded = [c['index'] for c in cards if c not in viable]
        if not viable:
            raise ValueError('Insufficient free VRAM for the model dense weights and context on any GPU')
        if main in excluded:
            main = max(viable, key=lambda c: free[c['index']])['index']
        cards = [c for c in viable if c['index'] != main] + [c for c in viable if c['index'] == main]
        overhead = {c['index']: dense + int(1.75 * GiB) + (int(GiB) if mtp and c['index'] == main else 0) for c in cards}
    # Match the native interface exactly. Flash has one reserve for stage 0
    # and one for later stages; q36 accepts one reserve for all its devices.
    if packed and len(cards) > 1:
        later_reserve = max(reserve[c['index']] for c in cards[1:])
        for card in cards[1:]:
            reserve[card['index']] = later_reserve
    elif not packed:
        for card in cards:
            reserve[card['index']] = reserve[main]
    if packed:
        limits = [(free[c['index']] - reserve[c['index']] - overhead[c['index']]
                   - max(512 * MiB, int(free[c['index']] * .12))) // kv for c in cards]
        capacity = min(limits)
    else:
        # The native engine can compute any remaining experts from bounded RAM.
        # Do not turn a desired GPU cache percentage into a context ceiling.
        main_experts = min(experts, 512 * MiB)
        capacity = (free[main] - reserve[main] - overhead[main] - main_experts) // kv
    if capacity < 1024:
        raise ValueError('Insufficient free VRAM for model weights, buffers and context')
    tokens = min(requested, capacity) if auto else requested
    if not auto and tokens > capacity:
        raise ValueError(f'Context needs more free VRAM; estimated capacity {capacity:,} tokens with current weights and devices')
    if auto and tokens < maximum:
        tokens = int(tokens // 1024 * 1024)
    cache_mib = advanced.get('conversation_cache_mib', '0') if packed else '0'
    ram = ram_budget_preview(memory['total'], memory['available'], len(cards),
                             advanced.get('ram_use_percent', 'auto'), cache_mib,
                             packed=packed, expert_bytes=experts)
    ram_budget = ram['ram_budget_bytes']
    if ram['ram_use_percent'] is None and ram_budget < min(experts, GiB):
        raise ValueError('Insufficient available RAM for the model expert cache and system headroom')
    args = {'--max-context': str(tokens), '--prefill': str(advanced.get('prefill', 'auto'))}
    flags = []
    if packed:
        args.update({'--kv': 'int8', '--expert-cache': str(advanced.get('expert_cache', 'auto')),
                     '--vram-reserve-mib': str(reserve[cards[0]['index']] // MiB)})
        args.update(flash_tuning_args(advanced, mtp=mtp))
        if len(cards) > 1:
            args['--vram-reserve-later-mib'] = str(reserve[cards[1]['index']] // MiB)
        if ram['ram_cache_mode'] == 'file':
            flags.append('--mmap-experts')
        else:
            # Keep Auto's historical rounding; manual small caches need finer units.
            decimals = 2 if ram['ram_use_percent'] is None else 6
            factor = 10 ** decimals
            args['--resident-budget-gib'] = f'{math.floor(ram_budget / GiB * factor) / factor:.{decimals}f}'
        # Omitting this flag restores the engine's nonzero default.
        args['--conversation-cache-mib'] = str(int(cache_mib))
    else:
        prefill = args['--prefill']
        if prefill != 'auto' and not 1 <= int(prefill) <= 4096:
            raise ValueError('This model supports prefill chunks up to 4096 tokens')
        args['--vram-reserve-mib'] = str(reserve[main] // MiB)
        args['--expert-devices'] = 'auto' if len(cards) > 1 else 'off'
        args['--main-device'] = str(len(cards) - 1)
        args['--host-expert-budget-mib'] = str(ram_budget // MiB)
        args['--device-vram-budget-mib'] = ','.join(str(free[c['index']] // MiB) for c in cards)
    estimates = [{'index': c['index'], 'free_bytes': free[c['index']], 'reserve_bytes': reserve[c['index']],
                  'context_bytes': tokens * kv if packed or c['index'] == main else 0} for c in cards]
    return {'context': tokens, 'model_context': maximum, 'context_policy': 'auto' if auto else 'explicit',
            'args': args, 'flags': flags, 'gpus': [c['index'] for c in cards], 'main_gpu': main,
            'excluded_gpus': excluded, **ram,
            'model_memory': {k: v for k, v in stats.items() if k != 'metadata'}, 'devices': estimates}
