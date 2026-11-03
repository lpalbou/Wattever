"""Optional local Strata Control settings bridge; never starts or stops models.

LM Studio already owns its multi-GPU allocation settings. Its CLI does not offer
per-device controls, so we retain those settings rather than rewrite private app
files or pretend that changing the telemetry sensor changes model placement.
"""
import json
import os
from pathlib import Path
import tempfile

from . import power


def control_dir():
    candidate = Path(os.environ.get('WATTEVER_STRATA_CONTROL_DIR') or
                     Path(__file__).resolve().parents[2] / 'flash-next')
    required = ('strata_app.py', 'strata_desktop.py', 'gpu_selection.py')
    if not all((candidate / name).is_file() for name in required):
        return None
    try:
        if json.loads((candidate / 'desktop-only.json').read_text(encoding='utf-8')).get('desktop_only') is not True:
            return None
    except (OSError, ValueError):
        return None
    return candidate


def validate(value):
    if not isinstance(value, dict) or set(value) - {'mode', 'gpu_index', 'schema_version'}:
        raise ValueError('Invalid GPU selection.')
    if value.get('schema_version', 1) != 1 or type(value.get('schema_version', 1)) is not int:
        raise ValueError('Unsupported GPU selection version.')
    mode = value.get('mode', 'auto')
    index = value.get('gpu_index')
    if mode not in ('auto', 'single'):
        raise ValueError('GPU mode must be auto or single.')
    if mode == 'single' and (type(index) is not int or not 0 <= index <= 255):
        raise ValueError('Choose a valid GPU index.')
    return {'schema_version': 1, 'mode': mode, 'gpu_index': index if mode == 'single' else None}


def status():
    directory = control_dir()
    strata = {'available': bool(directory), 'choice': validate({'mode':'auto'}),
              'application': 'next desktop model load', 'lifecycle_control': False}
    if directory:
        path = directory / 'desktop-gpu-settings.json'
        try:
            if path.exists():
                strata['choice'] = validate(json.loads(path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            strata.update(error='Saved GPU selection is unreadable. Select the cards again.', choice=None)
    return {'lmstudio': {'mode':'engine_settings', 'application':'next managed model load',
                         'note':'LM Studio controls GPU allocation. Wattever preserves its GPU settings.'},
            'strata': strata}


def stage(value):
    choice = validate(value)
    directory = control_dir()
    if directory is None:
        raise ValueError('Local Strata Control integration is unavailable. Configure GPUs in your server application.')
    sensors = power.list_gpus()
    if not sensors:
        raise ValueError('GPU discovery is unavailable; no configuration was changed.')
    if choice['mode'] == 'single' and choice['gpu_index'] not in {card['index'] for card in sensors}:
        raise ValueError('The selected GPU is not connected; no configuration was changed.')
    path = directory / 'desktop-gpu-settings.json'
    fd, temporary = tempfile.mkstemp(prefix='desktop-gpu-', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(choice, stream, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'saved': True, 'choice': choice, 'application':'next desktop model load',
            'model_started': False, 'model_switched': False}
