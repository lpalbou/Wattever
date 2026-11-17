"""Desktop GPU placement. Reading/saving a choice never starts an engine."""
import csv
import io
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / 'desktop-gpu-settings.json'


def cards():
    result = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.total,compute_cap,power.limit,memory.free,uuid',
                             '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=10,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('NVIDIA GPU discovery failed; placement is unknown.')
    found = []
    for row in csv.reader(io.StringIO(result.stdout)):
        if len(row) != 7:
            raise ValueError('Invalid NVIDIA GPU inventory.')
        index, name, memory, capability, watts, free, uuid = [value.strip() for value in row]
        found.append(dict(index=int(index), name=name, memory_mib=float(memory),
                          compute_capability=float(capability), power_limit_w=float(watts) if watts not in ('N/A', '[N/A]') else 0,
                          free_mib=float(free), uuid=uuid))
    if not found:
        raise ValueError('No NVIDIA GPU was detected.')
    return validate_inventory(found)


def validate_inventory(inventory):
    if not isinstance(inventory, list) or not inventory:
        raise ValueError('GPU inventory is empty or invalid; placement is unknown.')
    seen = set()
    result = []
    for card in inventory:
        if not isinstance(card, dict) or type(card.get('index')) is not int or card['index'] < 0 or card['index'] in seen:
            raise ValueError('Invalid or duplicate GPU index in inventory.')
        seen.add(card['index'])
        copy = dict(card)
        if not isinstance(card.get('name'), str) or not card['name']:
            raise ValueError('Invalid GPU name in inventory.')
        for key in ('memory_mib', 'compute_capability', 'power_limit_w'):
            value = card.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (key != 'power_limit_w' and value == 0):
                raise ValueError(f'Invalid GPU {key} in inventory.')
        if 'free_mib' in card:
            value = card['free_mib']
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= card['memory_mib']:
                raise ValueError('Invalid GPU free memory in inventory.')
        result.append(copy)
    return result


def validate(choice):
    if not isinstance(choice, dict) or set(choice) - {'mode', 'gpu_index', 'gpu_uuid', 'schema_version'}:
        raise ValueError('Invalid GPU selection.')
    if choice.get('schema_version', 1) != 1 or type(choice.get('schema_version', 1)) is not int:
        raise ValueError('Unsupported GPU selection version.')
    mode = choice.get('mode', 'auto')
    if mode not in ('auto', 'single'):
        raise ValueError('Choose automatic GPU placement or one card.')
    index = choice.get('gpu_index')
    if mode == 'single' and (type(index) is not int or not 0 <= index <= 255):
        raise ValueError('Choose a valid GPU index.')
    uuid = choice.get('gpu_uuid')
    if uuid is not None and (not isinstance(uuid, str) or not uuid.startswith('GPU-') or len(uuid) > 100):
        raise ValueError('Invalid GPU identity.')
    result = {'schema_version': 1, 'mode': mode, 'gpu_index': index if mode == 'single' else None}
    if mode == 'single' and uuid:
        result['gpu_uuid'] = uuid
    return result


def read_choice(path=None):
    path = Path(path or SETTINGS)
    if not path.exists():
        return validate({'mode': 'auto'})
    try:
        return validate(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, ValueError) as exc:
        raise ValueError('The saved GPU selection is unreadable; select the cards again.') from exc


def save_choice(choice, path=None):
    choice = validate(choice)
    path = Path(path or SETTINGS)
    fd, temporary = tempfile.mkstemp(prefix=path.stem, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(choice, stream, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return choice


def plan(choice=None, inventory=None, multi_supported=True):
    choice = validate(choice if choice is not None else read_choice())
    inventory = validate_inventory(cards() if inventory is None else inventory)
    eligible = [card for card in inventory if card['compute_capability'] >= 7.5]
    if choice['mode'] == 'single':
        eligible = [card for card in eligible if (card.get('uuid') == choice['gpu_uuid'] if choice.get('gpu_uuid') else card['index'] == choice['gpu_index'])]
        if not eligible:
            raise ValueError('The selected GPU is absent or unsupported. Select a connected card again.')
    if not eligible:
        raise ValueError('No supported NVIDIA GPU was detected.')
    main = max(eligible, key=lambda card: (card.get('free_mib', card['memory_mib']), card['power_limit_w'], -card['index']))
    helpers = [card for card in eligible if card != main] if multi_supported else []
    selected = helpers + [main]  # Strata's output/MTP stage is LAST.
    return {'mode': choice['mode'], 'gpu': [card['index'] for card in selected], 'main_gpu': main['index'],
            'cards': selected, 'layer_split': 'auto' if helpers else None,
            'selection_source': 'live free VRAM and board power; model memory is checked before loading',
            'note': 'Single-GPU engine: using the selected main card.' if not multi_supported and len(eligible) > 1
                    else 'Applies at the next desktop model load. No model is started or switched.'}


def apply(config, multi_supported=True, choice=None, inventory=None):
    placement = plan(choice, inventory, multi_supported)
    for field in ('gpu_roles', 'gpu_none'):
        config.pop(field, None)
    config.update(gpu=placement['gpu'], gpu_order='as_given', gpu_placement=placement)
    if placement['layer_split']:
        config['layer_split'] = 'auto'
        # A manually tuned single-card cache budget must not constrain the pair.
        args = dict(zip(config['args'][::2], config['args'][1::2]))
        args.pop('--expert-cache', None)
        config['args'] = [item for pair in args.items() for item in pair]
    else:
        config.pop('layer_split', None)
    return config
