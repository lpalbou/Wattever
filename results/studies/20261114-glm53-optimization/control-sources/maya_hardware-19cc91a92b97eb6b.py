"""Pure validation for Control's fresh Maya hardware plan. No engine starts."""
import hashlib
import json
import math
from pathlib import Path

from gpu_selection import validate_inventory


def ram_target(value):
    if isinstance(value, str) and value.strip().lower() == 'auto':
        return None
    if isinstance(value, bool):
        raise ValueError('Expert RAM must be Auto or a finite nonnegative GiB value.')
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError('Expert RAM must be Auto or a finite nonnegative GiB value.') from None
    if not math.isfinite(result) or result < 0:
        raise ValueError('Expert RAM must be Auto or a finite nonnegative GiB value.')
    return result



def parse_ram_tier_request(value):
    """Separate a percentage request from the existing Auto/plain-GiB contract."""
    if isinstance(value, str) and value.strip().endswith('%'):
        try:
            percent = float(value.strip()[:-1].strip())
        except ValueError:
            raise ValueError('Expert RAM percentage must be finite in 0..99.9%.') from None
        if not math.isfinite(percent) or not 0 <= percent <= 99.9:
            raise ValueError('Expert RAM percentage must be finite in 0..99.9%.')
        return {'mode': 'percent', 'requested_percent': percent}
    target = ram_target(value)
    return {'mode': 'auto' if target is None else 'gib', 'ram_tier_gib': target}


def resolve_ram_tier_percent(value, memory, headroom_gib=12):
    """Resolve an upper budget from one fresh snapshot; no OS reads or allocation."""
    request = parse_ram_tier_request(value)
    if request['mode'] != 'percent':
        raise ValueError('Resolve a percentage request here; pass Auto/GiB directly to configured.')
    snapshot = dict(memory)
    physical = snapshot.get('available_phys_gib')
    commit = snapshot.get('available_commit_gib')
    values = (physical, commit, headroom_gib)
    if any(type(number) not in (int, float) or not math.isfinite(number) or number < 0
           for number in values) or headroom_gib < 12:
        raise RuntimeError('RAM percentage preflight: fresh physical/commit room or headroom is invalid. No model was started.')
    room = min(physical, commit)
    if room < headroom_gib:
        raise RuntimeError(f'RAM percentage preflight: {room:.2f} GiB physical/commit room; '
                           f'at least {headroom_gib:g} GiB headroom is required. No model was started.')
    eligible = room - headroom_gib
    return {'mode': 'percent', 'requested_percent': request['requested_percent'],
            'entry_memory_snapshot': snapshot, 'reserved_headroom_gib': headroom_gib,
            'eligible_ram_gib': eligible,
            'resolved_upper_budget_gib': eligible * (request['requested_percent'] / 100),
            'scope': 'percentage_of_fresh_min_physical_commit_after_headroom',
            'native_budget_state': 'pending',
            'actual_native_cache_gib': None}


def reserve_mib(value='auto', default=1500):
    if isinstance(value, str) and value.strip().lower() == 'auto':
        value = default
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise ValueError('VRAM reserve must be Auto or a nonnegative integer MiB value.')
    return int(value)


def runtime_tuning(value, logical_cpus):
    """Validate GUI tuning against native limits and the fresh CPU inventory."""
    if value is None:
        return None
    allowed = {'prefill', 'read_chunks', 'cpu_workers', 'pcie_fraction'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('Unknown GLM runtime tuning parameter.')
    result = {}
    for key, maximum, minimum in [('prefill', 65536, 256), ('read_chunks', 16, 1),
                                   ('cpu_workers', min(64, logical_cpus), 0)]:
        item = value.get(key, 'auto')
        if isinstance(item, str) and item.strip().lower() == 'auto':
            result[key] = None
        elif isinstance(item, bool) or not str(item).strip().isdigit() or not minimum <= int(item) <= maximum:
            raise ValueError(f'GLM {key} must be Auto or an integer in {minimum}..{maximum}.')
        else:
            result[key] = int(item)
    item = value.get('pcie_fraction', 'auto')
    if isinstance(item, str) and item.strip().lower() == 'auto':
        result['pcie_fraction'] = None
    else:
        try:
            number = float(item)
        except (TypeError, ValueError):
            number = float('nan')
        if isinstance(item, bool) or not math.isfinite(number) or not 0 <= number <= 1:
            raise ValueError('GLM GPU share must be Auto or a finite number in 0..1.')
        result['pcie_fraction'] = number
    return result


def apply_runtime_tuning_env(env, tuning):
    """Clear stale/inherited aliases before applying an explicit GUI policy."""
    for key, suffix in [('read_chunks', 'READ_CHUNKS'), ('cpu_workers', 'CPU_LANE'),
                        ('pcie_fraction', 'PCIE_SHARE')]:
        for prefix in ('MAYA_GLM_', 'STRATA_GLM_'):
            env.pop(prefix + suffix, None)
            if key == 'cpu_workers':
                env.pop(prefix + 'CPU_LANE_MAX', None)
                for index in range(16):
                    env.pop(prefix + suffix + str(index), None)
            elif key == 'pcie_fraction':
                env.pop(prefix + 'CPU_PLAN', None)
            if tuning[key] is not None:
                env[prefix + suffix] = str(tuning[key])
    return env


def gpu_ids(value):
    if isinstance(value, str) and value.strip().lower() == 'auto':
        return None
    fields = str(value).split(',')
    if not fields or any(not field.strip().isdigit() for field in fields):
        raise ValueError('GPUs must be Auto or comma-separated installed GPU indices.')
    result = [int(field.strip()) for field in fields]
    if len(result) != len(set(result)) or len(result) > 16:
        raise ValueError('Select distinct GPUs, at most the native runtime limit of 16.')
    return result


def compiled_archs(profile):
    receipt = profile.get('build_receipt') or {}
    path = Path(receipt.get('path', ''))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt.get('sha256'):
        raise ValueError('Maya build receipt changed; review the prepared binary again.')
    archs = (json.loads(raw).get('build_metadata') or {}).get('archs')
    if not isinstance(archs, list) or not archs or any(type(a) is not int or a <= 0 for a in archs):
        raise ValueError('The reviewed build must declare its compiled CUDA architectures.')
    return archs


def plan_gpus(value, inventory, architectures, reserve):
    cards = validate_inventory(inventory)
    selected = gpu_ids(value)
    automatic = selected is None
    if automatic:
        # Re-evaluate the installed inventory at every load. No historic index or card count.
        selected = [c['index'] for c in sorted(cards, key=lambda c: (-c.get('free_mib', 0), c['index']))]
    if not selected or len(selected) > 16:
        raise ValueError('Maya supports 1..16 cards; select an explicit subset of this inventory.')
    by_id = {c['index']: c for c in cards}
    for index in selected:
        card = by_id.get(index)
        if card is None:
            raise ValueError(f'GPU {index} is no longer installed; choose Auto or an installed index.')
        capability = round(card['compute_capability'] * 10)
        # Exact audited targets only. A different architecture needs a reviewed build,
        # rather than silently dropping an added card or assuming PTX compatibility.
        if capability not in architectures:
            raise ValueError(f'GPU {index} needs CUDA SM {capability}; rebuild/review Maya for this hardware.')
        if 'free_mib' not in card or card['free_mib'] <= reserve:
            raise ValueError(f'GPU {index} has no known free VRAM above the requested {reserve} MiB reserve.')
    return {'requested': 'auto' if automatic else str(value), 'gpu_order': ','.join(map(str, selected)),
            'cards': [dict(by_id[index]) for index in selected], 'vram_reserve_mib': reserve}


def split_boundary(value, count):
    if value == 'auto':
        return value
    fields = str(value).split(',')
    if len(fields) != count - 1 or any(not f.isdigit() for f in fields):
        raise ValueError('A fixed layer split needs one boundary per GPU transition.')
    bounds = [int(f) for f in fields]
    if any(not 2 <= n <= 44 for n in bounds) or bounds != sorted(set(bounds)):
        raise ValueError('Layer boundaries must increase within 2..44.')
    return ','.join(map(str, bounds))
