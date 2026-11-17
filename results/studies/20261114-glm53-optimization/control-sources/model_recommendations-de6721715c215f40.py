"""Recommend safe launch defaults from the selected model and live machine capacity."""
from __future__ import annotations

import advanced_params


def _ram_gib(hardware=None):
    ram = (hardware or {}).get('ram') or {}
    total = ram.get('total_bytes') or 0
    available = total - (ram.get('used_bytes') or 0)
    # Prefer reported totals; available is estimated when only used/total exist.
    return total / (1024 ** 3), max(0.0, available / (1024 ** 3))


def _gpu_count(hardware=None):
    return len((hardware or {}).get('gpus') or [])


def _pick_context(maximum, candidates):
    usable = [value for value in candidates if value <= maximum]
    return usable[-1] if usable else min(maximum, candidates[0])


def recommend(choice, hardware=None):
    """
    Return recommended UI values for the selected model.
    Keys: context, mtp, reasoning_label, advanced, summary
    """
    choice = choice or {}
    engine = choice.get('engine') or 'strata'
    maximum = int(choice.get('max_context') or 65536)
    total_ram, free_ram = _ram_gib(hardware)
    gpus = _gpu_count(hardware)
    advanced = advanced_params.defaults_for('maya' if engine == 'maya' else 'strata')

    if engine == 'maya':
        return {'context': 'Auto', 'mtp': True, 'reasoning_label': 'Low',
                'advanced': advanced,
                'summary': 'GPU selection and native RAM budgeting are recalculated at each load. Low reasoning favors answer quality. Auto context uses the reviewed measured default; Max uses model metadata.'}

    # Plan against live model bytes and free memory at the actual load, after
    # unloading an owned model. A UI snapshot cannot budget its own allocations.
    advanced = advanced_params.defaults_for(choice.get('family', 'strata'))
    return {
        'context': 'Auto',
        'mtp': True,
        'reasoning_label': 'Off',
        'advanced': advanced,
        'summary': 'Automatic context, RAM budget and GPU placement are recalculated from the connected hardware at each load.',
    }


def library_status(paths=None, now=None):
    """Describe extra local assets that are not yet Controllable launch targets."""
    import time
    from pathlib import Path
    roots = paths or [Path(r'C:\tests\models')]
    now = time.time() if now is None else now
    rows = []
    known = {
        'Qwen3-Coder-Next-GGUF': {
            'title': 'Qwen3-Coder-Next · UD-IQ4_XS',
            'note': 'Weights present under C:\\tests\\models, but not prepared as a Strata Control launch pack yet.',
        },
        'Qwen3.5-122B-A10B-MTP-GGUF': {
            'title': 'Qwen3.5-122B-A10B · UD-IQ3_S',
            'note': 'Download incomplete (partial shards). Not loadable until finished and packed.',
        },
        'GLM-5.3-Flash-Maya-XS': {
            'title': 'GLM-5.3 Flash · Maya-XS backup/parts',
            'note': 'Verified backup exists; Control loads the prepared Maya profile when released.',
        },
    }
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.name not in known:
                continue
            part_files = list(child.rglob('*.part'))
            part_markers = list(child.rglob('*.part.json'))
            complete = any(p.suffix == '.gguf' and p.stat().st_size > 1_000_000_000 for p in child.rglob('*.gguf'))
            info = known[child.name]
            if part_files and not complete:
                newest = max(p.stat().st_mtime for p in part_files)
                # Older than ~1h with no growth ⇒ stalled, not an active transfer.
                state = 'stalled download' if (now - newest) > 3600 else 'incomplete download'
            elif complete:
                state = 'present'
            else:
                state = 'partial'
            rows.append({
                'id': child.name,
                'title': info['title'],
                'state': state,
                'note': info['note'],
                'path': str(child),
                'part_markers': len(part_markers),
            })
    # DeepSeek is supported by Maya/Strata upstream. Codex asked for it, but no weight tree
    # or active transfer is present on C:/D: (F: MayaModels volume is currently absent).
    rows.append({
        'id': 'deepseek-v4.1-flash',
        'title': 'DeepSeek V4.1 Flash',
        'state': 'not found',
        'note': 'Requested in prior Codex work, but no local weight download/pack or active transfer is present now.',
        'path': None,
    })
    return rows
