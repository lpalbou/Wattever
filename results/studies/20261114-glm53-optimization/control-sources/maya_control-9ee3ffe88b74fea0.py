"""Isolated experimental Maya profile owned by the Strata Control desktop."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import threading
import time

import psutil
import httpx

from desktop_policy import require_desktop
from strata_desktop import Controller, Process, ROOT, RUNTIME, bind_host, endpoint_urls, observe
from control_lifecycle import lifecycle_lock
import maya_hardware
from maya_startup import StartupFatalCursor, cleanup_failed_start

BASE = ROOT / 'experimental-maya'
PREPARED = BASE / 'prepared.json'
RELEASE = BASE / 'run-authorization.json'
REGISTRY = BASE / 'owned-processes.json'
PORT = 8097
API = f'http://127.0.0.1:{PORT}/v1'
MODEL = 'glm-5.3-flash-maya-xs-iq1_s'
QUANTIZATION = 'Maya-XS mixed IQ1_S + IQ2_XXS + BF16-compat'
MODEL_S24 = 'glm-5.3-flash-maya-s24-iq2_xxs_s'
REVIEWED_REVISIONS = ('3c935e411cd6625a9af0f292dd0af52598e866d8',
                      '22fc101f2c109b7e7174c975ff3c2d8e9ce0a867',
                      '027992cb1f09829488e650e466233ca351d3d6ef')
MODEL_PROFILES = {
    MODEL: {'label': 'Maya-XS', 'shards': 2, 'revision': '6728cf78a5b198a6a6f84b7fb88956a17ec56521',
            'quantization': QUANTIZATION},
    MODEL_S24: {'label': 'Maya-S24', 'shards': 3, 'revision': '5ca1df1d2436bd1ab270b169c5ea6c47aad23641',
                'quantization': 'Maya-S24 IQ2_XXS_S GGUF + BF16-compat'},
}


def model_identity(profile=None):
    """The reviewed prepared quant determines identity; MODEL remains the legacy XS default."""
    profile = profile or {}
    identifier = profile.get('model_id', MODEL)
    if identifier not in MODEL_PROFILES:
        raise ValueError('Prepare a reviewed GLM-5.3-Flash Maya-XS or Maya-S24 profile first.')
    return identifier


def model_quantization(profile=None):
    profile = profile or {}
    return profile.get('quantization') or MODEL_PROFILES[model_identity(profile)]['quantization']


def model_label(profile=None):
    return 'GLM-5.3 Flash · ' + model_quantization(profile)


def maya_bind_host(settings=None):
    """Same LAN policy as the native OpenAI gateway: 0.0.0.0 when exposed."""
    return bind_host(settings)


def maya_api_urls(settings=None):
    return endpoint_urls(settings, port=PORT)


def probe(port=PORT, route='/v1/status', api_key=None, expected_model=None):
    """Maya's actual service identity, independent of the native Strata probe."""
    headers = {'Authorization': 'Bearer ' + str(api_key).split(',')[0]} if api_key else {}
    try:
        if expected_model is None:
            expected_model = model_identity(prepared_profile(verify_files=False)[0])
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f'http://127.0.0.1:{port}{route}', headers=headers)
            response.raise_for_status()
            data = response.json()
        if (not isinstance(data, dict) or data.get('service') != 'maya'
                or data.get('backend') != 'project-maya-experimental'
                or data.get('model') != expected_model or data.get('model_routing') != 'active-only'
                or data.get('auto_load') is not False):
            return {'state': 'unknown', 'data': data, 'error': 'Maya identity or desktop lifecycle policy did not match.'}
        return {'state': 'up', 'data': data}
    except (httpx.HTTPError, OSError, ValueError) as error:
        return {'state': 'unknown', 'data': {}, 'error': type(error).__name__}


def read_registry():
    for attempt in range(8):
        try:
            rows = json.loads(REGISTRY.read_text(encoding='utf-8'))
            if not isinstance(rows, list):
                raise ValueError('Experimental process registry must be a list.')
            return rows
        except FileNotFoundError:
            return []
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.025 * (attempt + 1))


def write_registry(rows):
    temporary = REGISTRY.with_name(f'.owned-processes-{os.getpid()}-{threading.get_ident()}.tmp')
    for attempt in range(8):
        try:
            temporary.write_text(json.dumps(rows, indent=2), encoding='utf-8')
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.025 * (attempt + 1))
    for attempt in range(8):
        try:
            os.replace(temporary, REGISTRY)
            return
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(.025 * (attempt + 1))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def owned_records():
    """Only registry identities verified against the live process, never PID alone."""
    records = read_registry()
    if not records:
        return []
    profile, cfg = prepared_profile()
    engines = {Path(cfg['exe']).resolve()}
    pythons = {Path(profile[key]).resolve() for key in ('python', 'base_python') if profile.get(key)}
    found = []
    for item in records:
        if item.get('role') not in ('engine', 'model server'):
            raise RuntimeError('Unrecognized experimental process role.')
        try:
            proc = psutil.Process(item['pid'])
            exe = str(Path(proc.exe()).resolve())
            if exe != item['exe'] or proc.create_time() != item['born']:
                continue
            if Path(exe) not in (engines if item['role'] == 'engine' else pythons):
                raise RuntimeError('Experimental process executable is not in the prepared allowlist.')
            found.append(Process(proc.pid, item['born'], item['role'], exe))
        except psutil.NoSuchProcess:
            continue
    return found


def prepared_profile(verify_files=False):
    data = json.loads(PREPARED.read_text(encoding='utf-8'))
    if data.get('schema') != 'maya-control-prepared-v1' or data.get('model_id') not in MODEL_PROFILES:
        raise ValueError('Prepare the reviewed GLM-5.3-Flash Maya-XS or Maya-S24 profile first.')
    identity = model_identity(data)
    if data.get('revision') not in REVIEWED_REVISIONS:
        raise ValueError('Unexpected Maya source revision.')
    if data.get('model_revision') != MODEL_PROFILES[identity]['revision']:
        raise ValueError('Unexpected Maya model revision.')
    vendor = Path(data['vendor_root']).resolve(strict=True)
    python = Path(data['python']).resolve(strict=True)
    config_path = Path(data['config']).resolve(strict=True)
    if not all(path.is_relative_to(BASE.resolve()) for path in (vendor, python, config_path)):
        raise ValueError('Prepared software must stay inside the isolated Maya directory.')
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    exe = Path(cfg['exe']).resolve(strict=True)
    if not exe.is_relative_to(vendor):
        raise ValueError('Experimental engine must belong to the pinned Maya checkout.')
    if verify_files:
        checks = [(vendor / 'serve/server.py', data['server_sha256']),
                  (python, data['python_sha256']), (config_path, data['config_sha256']),
                  (exe, data['exe_sha256'])]
        if data.get('base_python'):
            checks.append((Path(data['base_python']).resolve(strict=True), data['base_python_sha256']))
        for path, expected in checks:
            if sha256(path) != expected:
                raise ValueError('Prepared software changed: ' + path.name)
        for item in data.get('control_files', []):
            path = Path(item['path']).resolve(strict=True)
            if path.parent != ROOT.resolve() or path.suffix != '.py' or sha256(path) != item['sha256']:
                raise ValueError('Reviewed desktop policy source changed; review the prepared profile again.')
        if data.get('legacy_window_receipt'):
            item=data['legacy_window_receipt'];path=Path(item['path']).resolve(strict=True)
            if path!=(BASE/'legacy-native-window.json').resolve() or sha256(path)!=item['sha256']:
                raise ValueError('Legacy native UI identity changed; review the prepared profile again.')
        for item in data.get('model_files', []):
            path = Path(item['path']).resolve(strict=True)
            stat = path.stat()
            if stat.st_size != item['bytes'] or stat.st_mtime_ns != item['mtime_ns'] or item.get('sha256_verified') is not True:
                raise ValueError('Verified model file changed; verify it again before loading.')
        model_files = data.get('model_files', [])
        if (len(model_files) != MODEL_PROFILES[identity]['shards'] or
                len({str(Path(item['path']).resolve()) for item in model_files}) != len(model_files)):
            raise ValueError('Every distinct verified model shard is required for the prepared quant.')
        # The converted projections and tokenizer are also part of the model
        # treatment. Preparation hashes them once; each GUI load checks the
        # exact receipt-bound file identity without rereading gigabytes.
        pack_files = data.get('pack_files')
        if pack_files is not None:
            pack_arg = cfg.get('args') or []
            if '--glm-pack' not in pack_arg:
                raise ValueError('Prepared GLM pack path is missing.')
            pack_root = Path(pack_arg[pack_arg.index('--glm-pack') + 1]).resolve(strict=True)
            if not pack_files:
                raise ValueError('Verified GLM pack receipt is empty.')
            for item in pack_files:
                path = Path(item['path']).resolve(strict=True)
                stat = path.stat()
                if (not path.is_relative_to(pack_root) or stat.st_size != item['bytes']
                        or stat.st_mtime_ns != item['mtime_ns'] or item.get('sha256_verified') is not True):
                    raise ValueError('Verified GLM pack changed; prepare and verify it again.')
    return data, cfg


def release_allowed():
    value = json.loads(RELEASE.read_text(encoding='utf-8'))
    if value.get('enabled') is not True or value.get('prepared_manifest_sha256') != sha256(PREPARED):
        raise RuntimeError('The experimental load gate has not been released for this prepared profile.')


def set_arg(args, flag, value):
    args = list(args)
    while flag in args:
        i = args.index(flag)
        if i + 1 >= len(args) or str(args[i + 1]).startswith('--'):
            raise ValueError('Malformed engine argument: ' + flag)
        del args[i:i + 2]
    return args + [flag, str(value)]


def remove_arg(args, flag):
    args = list(args)
    while flag in args:
        i = args.index(flag)
        if i + 1 >= len(args) or str(args[i + 1]).startswith('--'):
            raise ValueError('Malformed engine argument: ' + flag)
        del args[i:i + 2]
    return args


def configured(profile, config, context, mtp, gpus, parallel, kv, reasoning, split='auto', kv_streaming='on',
               ram_tier_gib='auto', host=None, vram_reserve_mib='auto', runtime_options=None):
    if type(context) is not int or not 1024 <= context <= 1048576:
        raise ValueError('Context must be an integer in 1024..1048576.')
    ids = maya_hardware.gpu_ids(gpus)
    if ids is None:
        raise ValueError('Resolve Auto GPUs from fresh inventory before configuring Maya.')
    gpus = ','.join(map(str, ids))
    if parallel not in (1, 2):
        raise ValueError('Choose one or two supported request slots.')
    if kv not in ('int8', 'fp16') or reasoning not in ('none', 'low', 'high', 'max'):
        raise ValueError('Unsupported KV precision or reasoning level.')
    if kv_streaming not in ('on', 'off'):
        raise ValueError('KV streaming must be on or off.')
    ram_tier_gib = maya_hardware.ram_target(ram_tier_gib)
    reserve = maya_hardware.reserve_mib(vram_reserve_mib, config.get('control_vram_reserve_mib', 1500))
    split = maya_hardware.split_boundary(split, len(ids))
    # Default stays loopback; MayaController.load passes the LAN bind host explicitly.
    listen = '127.0.0.1' if host is None else host
    if listen not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('Unsupported Maya bind host')
    value = copy.deepcopy(config)
    value.update(model_name=model_identity(profile), host=listen, port=PORT, gpu=gpus,
                 model_management=False, idle_unload_s=0, open_browser=False,
                 reasoning_effort=reasoning, loop_guard=False, mcp_servers=None,
                 vision=None, log=str(BASE / 'engine.log'))
    value['layer_split'] = str(split)
    args = remove_arg(value.get('args') or [], '--layer-split')
    # GLM MTP uses its own pipeline/window selector. The generic --spec flag
    # controls DFlash here and cannot truthfully describe MTP draft depth.
    args = remove_arg(args, '--spec')
    args = remove_arg(args, '--kv-resident')
    for flag, setting in (('--max-context', context), ('--parallel', parallel),
                          ('--kv', kv),
                          ('--kv-streaming', kv_streaming),
                          ('--vram-reserve-mib', reserve)):
        args = set_arg(args, flag, setting)
    if ',' in gpus:
        args = set_arg(args, '--layer-split', split)
    value['args'] = args
    env = value.setdefault('env', {})
    tuning = maya_hardware.runtime_tuning(runtime_options, os.cpu_count() or 1)
    if tuning is not None:
        value['args'] = set_arg(value['args'], '--prefill', tuning['prefill'] or 'auto')
        maya_hardware.apply_runtime_tuning_env(env, tuning)
    for key in ('MAYA_GLM_RAM_GB','STRATA_GLM_RAM_GB','MAYA_GLM_RAM_HEADROOM_GB','STRATA_GLM_RAM_HEADROOM_GB'):
        env.pop(key,None)
    if ram_tier_gib is not None:
        env['MAYA_GLM_RAM_GB']=format(ram_tier_gib, 'g')
    for key in ('MAYA_GLM_DEVS', 'STRATA_GLM_DEVS', 'MAYA_GLM_DEV1', 'STRATA_GLM_DEV1', 'MAYA_GLM_SPLIT', 'STRATA_GLM_SPLIT'):
        env.pop(key, None)
    env['MAYA_GLM_RAM_HEADROOM_GB']='12'
    for key in ('MAYA_GLM_KV_RESIDENT', 'STRATA_GLM_KV_RESIDENT'):
        env.pop(key, None)
    env['MAYA_GLM_KV_RESIDENT'] = '32768' if kv_streaming == 'on' else '0'
    # MTP off excludes the head; this changes placement and remains a distinct condition.
    for key in ('MAYA_GLM_NO_MTP', 'STRATA_GLM_NO_MTP', 'MAYA_GLM_NO_SPEC', 'STRATA_GLM_NO_SPEC'):
        env.pop(key, None)
        if not mtp:
            env[key] = '1'
    for key in ('MAYA_GLM_MTP', 'STRATA_GLM_MTP'):
        env.pop(key, None)
    if mtp:
        env['MAYA_GLM_MTP'] = '1'
    env['MAYA_GLM_SPLIT'] = str(split) if ',' in gpus else '0'
    # Additional sequence arenas otherwise permit a smaller inherited SEQ_CTX.
    # Allocate the requested context for every slot; allocation failure is explicit.
    env['MAYA_GLM_SEQ_CTX'] = str(context)
    value['sampling'] = {'temperature': 0, 'top_p': 1, 'seed': 73129}
    value['control_mtp_enabled'] = bool(mtp)
    value['control_condition'] = dict(backend='project-maya', runtime_revision=profile.get('revision'),
        base_model='zai-org/GLM-5.3-Flash', model_revision=profile.get('model_revision'),
        quantization=model_quantization(profile), requested_mtp=bool(mtp), gpu_order=gpus,
        layer_split=str(split) if ',' in gpus else 'single', context=context, parallel=parallel,
        kv=kv, reasoning=reasoning, sampling=dict(value['sampling']), loop_guard=False)
    value['control_condition']['kv_streaming'] = kv_streaming
    value['control_condition']['kv_resident_requested_positions'] = 32768 if kv_streaming == 'on' else 0
    value['control_condition']['mtp_decode_policy_requested'] = 'vendor-default; head included' if mtp else 'head excluded'
    value['control_condition']['thinking_budget'] = value.get('thinking_budget')
    value['control_condition']['pack_policy'] = profile.get('compatibility_policy', 'vendor-standard BF16-compat')
    value['control_condition']['ram_tier_gib_requested'] = env.get('MAYA_GLM_RAM_GB')
    value['control_condition']['per_slot_context_tokens_requested'] = context
    paired = dict(zip(args[::2], args[1::2]))
    value['control_condition']['memory_policy_requested'] = {
        'ram_tier_policy': 'auto' if ram_tier_gib is None else 'manual',
        'vram_reserve_mib': reserve,
        'ram_tier_global_gib': float(env['MAYA_GLM_RAM_GB']) if env.get('MAYA_GLM_RAM_GB') is not None else None,
        'ram_headroom_min_gib': float(env['MAYA_GLM_RAM_HEADROOM_GB']) if env.get('MAYA_GLM_RAM_HEADROOM_GB') is not None else None,
        'ram_resident_required': bool(int(env.get('MAYA_GLM_RAM_RESIDENT', '0'))),
        'target_disk_skip_weight': float(env.get('MAYA_GLM_DISK_SKIP_W', '0')),
        'target_disk_skip_rank_from': int(env.get('MAYA_GLM_DISK_SKIP_FROM', '8')),
        'target_miss_skip_weight': float(env.get('MAYA_GLM_MISS_SKIP_W', '0')),
        'additional_sequence_context_tokens': context,
        'kv_streaming': paired.get('--kv-streaming'),
    }
    if tuning is not None:
        value['control_condition']['runtime_tuning_requested'] = tuning
    return value


def _auto_ram_entry_snapshot():
    """Fresh physical/commit availability; never substitute page-file free space for commit."""
    available = psutil.virtual_memory().available / 2**30
    commit = None
    if os.name == 'nt':
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ('total_phys', 'available_phys',
                'total_commit', 'available_commit', 'total_virtual', 'available_virtual', 'extended_virtual')]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise RuntimeError('Auto RAM preflight: Windows commit availability is unknown. No model was started.')
        # Take both values from this fresh Windows snapshot rather than mixing sampling times.
        available = status.available_phys / 2**30
        commit = status.available_commit / 2**30
    return {'available_phys_gib': available, 'available_commit_gib': commit}


def require_ram_headroom(config, memory=None):
    """Reject insufficient entry headroom; the native Auto budget is determined after GPU allocation."""
    env = config.get('env') or {}
    if 'MAYA_GLM_RAM_GB' not in env:
        snapshot = _auto_ram_entry_snapshot() if memory is None else dict(memory)
        available = snapshot.get('available_phys_gib')
        commit = snapshot.get('available_commit_gib')
        headroom = float(env.get('MAYA_GLM_RAM_HEADROOM_GB', '12'))
        values = [available, headroom] + ([commit] if commit is not None else [])
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
            raise RuntimeError('Auto RAM preflight: memory availability/headroom is invalid. No model was started.')
        if os.name == 'nt' and commit is None:
            raise RuntimeError('Auto RAM preflight: Windows commit availability is unknown. No model was started.')
        room = min(available, commit) if commit is not None else available
        if headroom < 12 or room < headroom:
            raise RuntimeError(f'Auto RAM preflight: {room:.2f} GiB available physical/commit room; '
                               f'at least {max(12, headroom):g} GiB entry headroom is required. No model was started.')
        return dict(snapshot, mode='auto', scope='prestart_entry_headroom_only',
                    reserved_headroom_gib=headroom, required_available_gib=headroom,
                    ram_tier_global_gib=None, native_budget_bytes=None, native_budget_state='pending',
                    budget_owner='native_after_gpu_workspace_allocation', result='pass', at=time.time())
    budget = float(env['MAYA_GLM_RAM_GB'])
    headroom = float(env.get('MAYA_GLM_RAM_HEADROOM_GB', '12'))
    available = psutil.virtual_memory().available / 2**30
    if not math.isfinite(budget) or not math.isfinite(headroom) or budget < 0 or headroom < 12 or available < budget + headroom:
        raise RuntimeError(f'RAM preflight: {available:.2f} GiB available; this {budget:g} GiB global tier requires at least {headroom:g} GiB additional headroom. No model was started.')
    return {'available_phys_gib': available, 'ram_tier_global_gib': budget,
            'reserved_headroom_gib': headroom, 'required_available_gib':budget+headroom, 'result': 'pass', 'at': time.time()}


def _system_console_host_path():
    """Resolve Windows' console helper through the OS, not a mutable env path."""
    if os.name != 'nt':
        return None
    import ctypes
    directory = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetSystemDirectoryW(directory, len(directory))
    if not 0 < length < len(directory):
        raise RuntimeError('Windows system directory could not be verified.')
    return (Path(directory.value) / 'conhost.exe').resolve(strict=True)


class MayaController(Controller):
    def _records(self):
        if self.model_process is None or self.model_process.poll() is not None:
            return []
        parent = psutil.Process(self.model_process.pid)
        rows = []
        profile = self._profile
        allowed_python = {Path(profile[key]).resolve() for key in ('python', 'base_python') if profile.get(key)}
        expected_engine = Path(self._config['exe']).resolve()
        console_host = _system_console_host_path()
        for proc in [parent] + parent.children(recursive=True):
            try:
                exe = Path(proc.exe()).resolve()
                # Only the exact OS console helper among descendants is ignored.
                # It is not a model/server record, and its children remain checked.
                if proc.pid != parent.pid and console_host is not None and exe == console_host:
                    continue
                if exe not in allowed_python | {expected_engine}:
                    raise RuntimeError('Unexpected child executable in the owned experimental job.')
                rows.append(dict(pid=proc.pid, born=proc.create_time(), exe=str(exe),
                                 role='engine' if exe == expected_engine else 'model server'))
            except psutil.NoSuchProcess:
                continue
        return rows

    def _save_records(self):
        write_registry(self._records())

    def load(self, context=32768, mtp=True, gpus='auto', parallel=1, kv='int8', reasoning='none', split='auto', kv_streaming='on', ram_tier_gib='auto', vram_reserve_mib='auto', runtime_options=None):
        with lifecycle_lock(RUNTIME):
            previous_process, previous_job = self.model_process, self.job
            try:
                return self._load_maya(context, mtp, gpus, parallel, kv, reasoning, split, kv_streaming, ram_tier_gib, vram_reserve_mib, runtime_options)
            except Exception as failure:
                cleanup_failed_start(self, previous_process, previous_job, failure)
                raise

    def _load_maya(self, context=32768, mtp=True, gpus='auto', parallel=1, kv='int8', reasoning='none', split='auto', kv_streaming='on', ram_tier_gib='auto', vram_reserve_mib='auto', runtime_options=None):
        require_desktop()
        release_allowed()
        profile, config = prepared_profile(verify_files=True)
        state = observe()
        if state.get('state') not in ('Stopped', 'Server ready') or state.get('uncertain'):
            raise RuntimeError('Strata engine state is not confirmed idle. Unload it in Strata Control first.')
        if self.model_process is not None and self.model_process.poll() is None:
            raise RuntimeError('Unload the current experimental model first.')
        for proc in psutil.process_iter(['name']):
            if (proc.info['name'] or '').lower() in ('llama-server.exe', 'llama-server', 'ollama_llama_server.exe', 'maya.exe'):
                raise RuntimeError('Another inference engine is running. Unload its model before loading Maya.')
        listen = maya_bind_host()
        with socket.socket() as candidate:
            # Probe the intended bind family so LAN exposure fails early if the port is taken.
            candidate.bind((listen, PORT))
        import gpu_selection
        reserve = maya_hardware.reserve_mib(vram_reserve_mib, config.get('control_vram_reserve_mib', 1500))
        placement = maya_hardware.plan_gpus(gpus, gpu_selection.cards(), maya_hardware.compiled_archs(profile), reserve)
        ram_request = maya_hardware.parse_ram_tier_request(ram_tier_gib)
        ram_resolution = None
        if ram_request['mode'] == 'percent':
            snapshot = dict(_auto_ram_entry_snapshot(), at=time.time())
            ram_resolution = maya_hardware.resolve_ram_tier_percent(ram_tier_gib, snapshot)
            ram_tier_gib = ram_resolution['resolved_upper_budget_gib']
        config = configured(profile, config, context, mtp, placement['gpu_order'], parallel, kv, reasoning, split, kv_streaming,
                            ram_tier_gib, host=listen, vram_reserve_mib=reserve, runtime_options=runtime_options)
        if ram_resolution is not None:
            config['control_condition']['ram_tier_percent_requested'] = ram_resolution['requested_percent']
            config['control_condition']['ram_tier_budget_resolution'] = ram_resolution
        config['control_condition']['hardware_plan'] = placement
        config['control_preflight'] = require_ram_headroom(config)
        self._profile, self._config = profile, config
        path = BASE / 'active-config.json'
        path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        argv = [profile['python'], str(ROOT / 'maya_backend.py'), '--vendor-root', profile['vendor_root'],
                '--server-sha256', profile['server_sha256'], '--config', str(path), '--port', str(PORT),
                '--host', listen,
                '--control-parent-birth', str(psutil.Process(os.getpid()).create_time())]
        startup_log = StartupFatalCursor(config['log'])
        self.model_process = self.spawn(argv, 'experimental-maya/server.log', cwd=profile['vendor_root'])
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            self._save_records()
            if self.cancel.is_set():
                raise RuntimeError('Loading cancelled. Unload the experimental model to verify release.')
            if self.model_process.poll() is not None:
                raise RuntimeError('Maya server exited. See the experimental server log.')
            live = probe(PORT, '/v1/status', api_key=config.get('api_key'), expected_model=config['model_name'])
            data = live.get('data') or {}
            if live['state'] == 'up' and data.get('loaded') is True:
                if (data.get('model') != config['model_name'] or data.get('auto_load') is not False
                        or (data.get('context') or {}).get('native') != context):
                    raise RuntimeError('Maya readiness identity, context or manual-load policy did not match.')
                self._save_records()
                return
            fatal = startup_log.poll()
            if fatal is not None:
                raise RuntimeError('Maya startup failed: ' + fatal)
            time.sleep(1)
        raise RuntimeError('Maya loading timed out. Unload its owned processes before retrying.')

    def unload(self):
        with lifecycle_lock(RUNTIME):
            return self._unload_maya()

    def _unload_maya(self):
        require_desktop()
        if self.job is None:
            if self.model_process is not None and self.model_process.poll() is None:
                raise RuntimeError('Owned process has no job; shutdown is unknown.')
            return
        # The job handle owns the tree even if a readiness/registry check failed.
        # Stop only that job; never target an unrelated PID or occupied port.
        owned = []
        for child in self.children:
            if child.poll() is not None:
                continue
            try:
                proc = psutil.Process(child.pid)
                owned.extend([proc] + proc.children(recursive=True))
            except psutil.NoSuchProcess:
                continue
        self.job.close()
        self.job = None
        _, alive = psutil.wait_procs(owned, timeout=30)
        if alive:
            raise RuntimeError('An owned Maya process has not exited; shutdown remains unknown.')
        for child in self.children:
            child.wait(timeout=5)
        self.children.clear()
        self.model_process = None
        write_registry([])

    stop = unload
