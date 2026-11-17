"""Live observations and lifecycle operations for Strata Control. No UI code."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

import httpx
import psutil

from desktop_policy import require_desktop
from model_catalog import ModelManager, ModelSwitchError, SPECS
import gpu_selection
import model_downloads
import storage_volumes
from control_lifecycle import lifecycle_lock, configuration_signature, owned_backend_configuration

ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ.get('LOCALAPPDATA', '')) / 'Strata'
RUNTIME = ROOT / 'desktop-runtime'
SETTINGS = ROOT / 'desktop-settings.json'
API = 'http://127.0.0.1:8086/v1'
REASONING = {'Model default': None, 'Off': 'none', 'Low': 'low', 'Medium': 'medium', 'High': 'high'}


def load_settings(path=None):
    path = Path(path or SETTINGS)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Desktop settings must be a JSON object')
    return data


def lan_address():
    """Best-effort LAN IPv4 for display. Binding still uses 0.0.0.0 when LAN is enabled."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('8.8.8.8', 80))
            address = sock.getsockname()[0]
            if address and not address.startswith('127.'):
                return address
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if address and not address.startswith('127.'):
                return address
    except OSError:
        pass
    return None


def bind_host(settings=None):
    settings = load_settings() if settings is None else settings
    return '0.0.0.0' if settings.get('lan_access') else '127.0.0.1'


def api_url(settings=None, port=8086):
    """Advertised OpenAI-compatible base URL for the active bind policy."""
    settings = load_settings() if settings is None else settings
    if not settings.get('lan_access'):
        return f'http://127.0.0.1:{int(port)}/v1'
    address = lan_address() or '127.0.0.1'
    return f'http://{address}:{int(port)}/v1'


def endpoint_urls(settings=None, port=8086):
    """Local loopback URL always; LAN URL only when exposure is enabled."""
    settings = load_settings() if settings is None else settings
    port = int(port)
    local = f'http://127.0.0.1:{port}/v1'
    if not settings.get('lan_access'):
        return {'local': local, 'lan': None, 'advertised': local}
    address = lan_address() or '127.0.0.1'
    lan = f'http://{address}:{port}/v1'
    return {'local': local, 'lan': lan, 'advertised': lan}


@dataclass(frozen=True)
class Process:
    pid: int
    born: float
    role: str
    exe: str


def processes():
    """Inspect identities, including legacy installed runtime hosts. Never trust PID files."""
    found, uncertain = [], False
    try:
        from maya_control import owned_records
        found.extend(owned_records())
    except (OSError, ValueError, KeyError, RuntimeError, psutil.Error):
        uncertain = True
    maya_pids = {p.pid for p in found}
    for proc in psutil.process_iter(['pid', 'name']):
        name = (proc.info['name'] or '').lower()
        if name not in ('strata.exe', 'strata-q36-multigpu.exe', 'maya.exe', 'python.exe', 'pythonw.exe'):
            continue
        if proc.pid in maya_pids:
            continue
        try:
            exe = Path(proc.exe()).resolve()
            args = proc.cmdline()
            role = None
            if name == 'strata-q36-multigpu.exe':
                role = 'engine' if exe == (ROOT / 'native-engine/strata-q36-multigpu.exe').resolve() else 'unverified engine'
            elif name == 'maya.exe':
                role = 'unverified engine'
            elif name == 'strata.exe':
                role = 'engine' if exe.is_relative_to(HOME.resolve()) else 'unverified engine'
            elif exe.is_relative_to(HOME.resolve()):
                if 'strata_control.runtime' in args and '--host' in args:
                    role = 'runtime host'
                elif 'serve.server' in args or str(ROOT / 'desktop_backend.py') in args:
                    role = 'model server'
            elif str(ROOT / 'gateway.py') in args or ('gateway:app' in args and str(ROOT) in args):
                role = 'gateway'
            if role:
                found.append(Process(proc.pid, proc.create_time(), role, str(exe)))
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError):
            # Unreadable unrelated Python processes do not establish Strata ownership.
            if name in ('strata.exe', 'strata-q36-multigpu.exe', 'maya.exe'):
                uncertain = True
    return found, uncertain


def probe(port, route):
    """Only an explicit connection refusal proves absence. Timeouts stay unknown."""
    try:
        listeners = [entry for entry in psutil.net_connections(kind='tcp')
                     if entry.status == psutil.CONN_LISTEN and entry.laddr.port == port
                     and entry.laddr.ip in ('127.0.0.1', '0.0.0.0', '::', '::1')]
        if not listeners:
            return {'state': 'absent'}
    except (psutil.Error, OSError):
        pass
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.6):
            pass
    except ConnectionRefusedError:
        return {'state': 'absent'}
    except OSError as exc:
        return {'state': 'unknown', 'error': str(exc)}
    try:
        response = httpx.get(f'http://127.0.0.1:{port}{route}', timeout=1, trust_env=False)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get('service') != 'strata':
            raise ValueError('Another service is using this port')
        return {'state': 'up', 'data': data}
    except (httpx.HTTPError, ValueError) as exc:
        return {'state': 'unknown', 'error': str(exc)}


def observe():
    procs, uncertain = processes()
    backend = probe(8081, '/v1/status')
    gateway = probe(8086, '/health')
    data = backend.get('data', {})
    engines = [p for p in procs if 'engine' in p.role]
    stale = uncertain or 'unknown' in (backend['state'], gateway['state'])
    if stale:
        state, detail = 'Unknown', 'A live check failed. Strata may still be using memory.'
    elif engines and backend['state'] == 'up' and data.get('loaded') is True:
        state = 'Model ready' if gateway['state'] == 'up' else 'API offline'
        detail = 'Ready for requests.' if state == 'Model ready' else 'A model is still loaded. Stop it to release memory.'
    elif backend['state'] == 'up' or engines or any(p.role != 'gateway' for p in procs):
        state, detail = 'Model not ready', 'A Strata process is present; its model is not confirmed ready.'
    elif gateway['state'] == 'up':
        state, detail = 'Server ready', 'Choose a model below, then click Load model.'
    elif procs:
        state, detail = 'Not ready', 'A Strata process is present, but its API is not responding.'
    else:
        state, detail = 'Stopped', 'No Strata engine, model server or API was detected.'
    routing = (gateway.get('data') or {}).get('model_routing')
    if gateway['state'] == 'up' and routing not in (None, 'active-only', 'prepared-presets-v1'):
        state, detail = 'Needs attention', 'Unrecognized API model routing. Stop Strata before restarting.'
    return dict(state=state, detail=detail, model=data.get('model'),
                context=data.get('context'), activity=data.get('activity'),
                backend=backend, gateway=gateway, processes=[asdict(p) for p in procs],
                uncertain=uncertain, checked=time.time(), monotonic=time.monotonic())


def gguf_context(path):
    """Read the model's declared context from its GGUF header, without loading weights."""
    formats = {0:'B', 1:'b', 2:'H', 3:'h', 4:'I', 5:'i', 6:'f', 7:'?', 10:'Q', 11:'q', 12:'d'}
    with Path(path).open('rb') as stream:
        def number(fmt):
            return struct.unpack('<' + fmt, stream.read(struct.calcsize(fmt)))[0]
        def string():
            size = number('Q')
            if size > 1024 * 1024:
                raise ValueError('Invalid model metadata string')
            return stream.read(size).decode('utf-8')
        def skip(kind):
            if kind == 8:
                stream.seek(number('Q'), 1)
            elif kind == 9:
                item, count = number('I'), number('Q')
                if item in formats:
                    stream.seek(struct.calcsize(formats[item]) * count, 1)
                elif item == 8 and count < 2000000:
                    for _ in range(count):
                        skip(item)
                else:
                    raise ValueError('Unsupported model metadata array')
            else:
                stream.seek(struct.calcsize(formats[kind]), 1)
        if stream.read(4) != b'GGUF' or number('I') not in (2, 3):
            raise ValueError('Unrecognized model file')
        number('Q')
        count = number('Q')
        for _ in range(min(count, 4096)):
            key, kind = string(), number('I')
            if key.endswith('.context_length') and kind in formats:
                value = number(formats[kind])
                if type(value) is int and 1024 <= value <= 2147483647:
                    return value
                raise ValueError('Invalid model context limit')
            skip(kind)
    raise ValueError('The model does not declare a context limit')


def maya_choice():
    """List the prepared GLM profile when the isolated Maya package is present."""
    try:
        from maya_control import PREPARED, model_identity, model_label, model_quantization, prepared_profile, release_allowed
        if not PREPARED.exists():
            return None
        profile, config = prepared_profile(verify_files=False)
        try:
            release_allowed()
            released = True
        except (OSError, ValueError, RuntimeError):
            released = False
        maximum = profile.get('model_context_tokens')
        if type(maximum) is not int or maximum <= 0:
            maximum = gguf_context(Path(profile['model_files'][0]['path']))
        if type(maximum) is not int or maximum <= 0:
            raise ValueError('Prepared GLM context metadata is missing.')
        args = config.get('args') or []
        auto_context = profile.get('auto_context')
        if auto_context is None and '--max-context' in args:
            auto_context = int(args[args.index('--max-context') + 1])
        if type(auto_context) is not int or not 1024 <= auto_context <= maximum:
            raise ValueError('Prepare a measured GLM automatic context within the model metadata bound.')
        return dict(
            id=model_identity(profile),
            label=model_label(profile),
            max_context=maximum,
            auto_context=auto_context,
            mtp=True,
            multi_gpu=True,
            engine='maya',
            family='glm',
            released=released,
            quantization=model_quantization(profile),
        )
    except (OSError, ValueError, KeyError, RuntimeError, TypeError):
        return None


def model_choices():
    """Every prepared model Controllable from the desktop: native Qwen packs plus GLM when prepared."""
    manager = ModelManager(ROOT)
    result = []
    for spec in SPECS:
        try:
            manager._prepared(spec)
            maximum = gguf_context(ROOT / 'models' / spec.weights[0])
            label = ('Qwen3.6 35B A3B' if spec.family == 'qwen36' else 'Qwen3.8 Flash Next') + ' · ' + spec.quantization
            result.append(dict(id=spec.id, label=label, max_context=maximum, mtp=True,
                               multi_gpu=True, engine='strata', family=spec.family))
        except (OSError, ValueError, KeyError, struct.error, RuntimeError):
            continue
    maya = maya_choice()
    if maya:
        result.append(maya)
    return result

def save_settings(updates, path=None):
    """Merge desktop settings. Unknown keys are preserved; validated keys are checked."""
    path = Path(path or SETTINGS)
    current = load_settings(path)
    if not isinstance(updates, dict):
        raise ValueError('Desktop settings updates must be a JSON object')
    merged = dict(current)
    merged.update(updates)
    if 'reasoning_effort' in merged and merged.get('reasoning_effort') not in REASONING.values():
        raise ValueError('Unsupported reasoning setting')
    if 'lan_access' in merged and type(merged['lan_access']) is not bool:
        raise ValueError('LAN access must be true or false')
    if 'vision' in merged and type(merged['vision']) is not bool:
        raise ValueError('Images must be true or false')
    if 'conversation_cache' in merged and type(merged['conversation_cache']) is not bool:
        raise ValueError('Prefix cache must be true or false')
    if 'model_storage' in merged:
        storage = merged['model_storage']
        if storage is not None and not isinstance(storage, dict):
            raise ValueError('Invalid model storage selection')
        if isinstance(storage, dict):
            mode = storage.get('mode')
            if mode not in ('default', 'nvme', 'custom'):
                raise ValueError('Choose default, NVMe, or custom model storage')
            if mode == 'nvme' and not str(storage.get('letter') or '').strip():
                raise ValueError('Choose an NVMe volume')
            if mode == 'custom' and not str(storage.get('path') or '').strip():
                raise ValueError('Choose a custom model storage folder')
    if 'advanced' in merged and merged['advanced'] is not None and not isinstance(merged['advanced'], dict):
        raise ValueError('Advanced settings must be a JSON object')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(merged, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    return merged


def resolve_storage_root(storage, models_root=None, inventory=None, progress=None, cancel=None):
    """
    Turn a Control storage choice into a launch root.
    NVMe mode stages a verified package under <volume>\\StrataModels when needed.
    """
    if not storage:
        return None
    mode = storage.get('mode')
    if mode in (None, 'default'):
        return None
    if mode == 'custom':
        return str(Path(storage['path']).expanduser().resolve())
    if mode != 'nvme':
        raise ValueError('Unsupported model storage mode')
    letter = str(storage.get('letter') or '').strip().rstrip(':').upper()
    volumes = storage_volumes.nvme_volumes(inventory)
    volume = next((item for item in volumes if item['letter'] == letter), None)
    if volume is None:
        raise ValueError(f'NVMe volume {letter}: was not detected')
    models_root = Path(models_root or (ROOT / 'models'))
    spec_id = storage.get('model')
    if not spec_id:
        raise ValueError('A model id is required before staging onto NVMe')
    spec = next((item for item in SPECS if item.id == spec_id), None)
    if spec is None:
        raise ValueError('Choose a prepared model before staging onto NVMe')
    destination = storage_volumes.stage_root_for(volume)
    return str(storage_volumes.stage_model_package(
        models_root, destination, spec, progress=progress, cancel=cancel))


def relocate_model_storage(config, spec, storage_root):
    """Point a desktop launch at an explicitly selected copy of prepared assets."""
    original = (ROOT / 'models').resolve()
    target = Path(storage_root).expanduser().resolve()
    if not target.is_dir():
        raise ValueError('Choose an existing folder containing the copied model package')
    if target == original:
        return config
    required = [original / value for value in spec.weights]
    required += [path.with_name(path.name + '.done') for path in required]
    pack = original / 'packs' / spec.pack
    required += [path for path in pack.rglob('*') if path.is_file()]
    if spec.packed:
        required += [path for path in (original / 'mtp/rt').rglob('*') if path.is_file()]
    for source in required:
        destination = (target / source.relative_to(original)).resolve()
        if (not destination.is_relative_to(target) or not destination.is_file()
                or destination.stat().st_size != source.stat().st_size):
            raise ValueError('The selected model package is incomplete or differs in size')
        if source.suffix == '.done' and destination.read_bytes() != source.read_bytes():
            raise ValueError('The copied model verification marker differs')
    def mapped(value):
        if isinstance(value, str):
            path = Path(value)
            if path.is_absolute() and path.resolve().is_relative_to(original):
                result = (target / path.resolve().relative_to(original)).resolve()
                if not result.is_relative_to(target) or not result.exists():
                    raise ValueError('A copied runtime asset is missing or outside the chosen package')
                return str(result)
        return value
    config['args'] = [mapped(value) for value in config['args']]
    config['tokenizer'] = mapped(config.get('tokenizer'))
    config['model_storage_root'] = str(target)
    config['model_storage_volume'] = target.drive
    return config


def apply_qwen_advanced(args, advanced):
    """Map Advanced-panel choices onto safe native engine flags."""
    from model_catalog import _safe_override
    advanced = advanced or {}
    mapping = {
        'prefill': '--prefill',
        'vram_reserve_mib': '--vram-reserve-mib',
        'expert_cache': '--expert-cache',
        'conversation_cache_mib': '--conversation-cache-mib',
    }
    result = dict(args)
    for key, flag in mapping.items():
        if key not in advanced:
            continue
        value = str(advanced[key])
        if not _safe_override(flag, value):
            raise ValueError(f'Unsupported advanced setting for {flag}')
        if value == '0' and flag == '--conversation-cache-mib':
            result.pop(flag, None)
            continue
        result[flag] = value
    return result


def vision_reader_exe():
    """The picture reader shipped in the installed engine package (newest version present)."""
    base = Path(os.environ.get('LOCALAPPDATA', '')) / 'Strata/components/engine'
    found = sorted(base.glob('*/strata-vision.exe')) if base.is_dir() else []
    if not found:
        raise ValueError('The installed Strata engine has no picture reader (strata-vision.exe).')
    return found[-1]


def vision_section(spec, config):
    """
    The run config's "vision" entry: strata-vision reads pictures with the family's
    encoder. It runs on the CPU, as the Strata installer chooses below 24 GB of VRAM,
    so the cards keep their memory for the expert cache.
    """
    encoder = model_downloads.vision_encoder_for(spec.family)
    if encoder is None:
        raise ValueError('This model has no image encoder.')
    if not model_downloads.is_ready(encoder, ROOT / 'models'):
        raise ValueError('Download the image encoder first, or turn Images off.')
    args = config['args']
    return {
        'exe': str(vision_reader_exe()),
        'mmproj': str(model_downloads.asset_path(encoder, ROOT / 'models')),
        # The text model is opened vocabulary-only; use the copy this launch reads.
        'model': args[args.index('--native') + 1],
        'gpu': False,
        'threads': max(1, min((os.cpu_count() or 8) // 2, 16)),
    }


def build_config(model, context='Auto', mtp=True, storage_root=None, host=None, advanced=None, vision=False):
    spec = next((s for s in SPECS if s.id == model), None)
    if spec is None:
        raise ValueError('Choose a prepared model')
    manager = ModelManager(ROOT)
    config = manager.base_config(spec)
    if storage_root:
        config = relocate_model_storage(config, spec, storage_root)
    model_root = Path(storage_root) if storage_root else manager.models
    maximum = gguf_context(model_root / spec.weights[0])
    listen = host if host is not None else bind_host()
    if listen not in ('127.0.0.1', '0.0.0.0'):
        raise ValueError('Unsupported bind host')
    args = dict(zip(config['args'][::2], config['args'][1::2]))
    import hardware_planner
    placement = gpu_selection.plan(multi_supported=True)
    allocation = hardware_planner.plan(spec, model_root, placement, maximum,
                                       context=context, mtp=mtp, advanced=advanced)
    args.update(allocation['args'])
    if not mtp:
        for flag in ('--mtp', '--spec', '--spec-min-p'):
            args.pop(flag, None)
    config.update(args=[part for pair in args.items() for part in pair] + allocation.get('flags', []),
                  mtp=bool(mtp), drafting='mtp' if mtp else 'off',
                  host=listen, port=8081, parallel=1, open_browser=False,
                  user_args=[], engine_ready_s=1800, log=str(ROOT / 'desktop-engine.log'),
                  lazy_load=False, idle_unload_s=0)
    # The client-facing reasoning default is applied by the gateway per request.
    config.pop('reasoning_effort', None)
    config.update(gpu=allocation['gpus'], gpu_order='as_given', hardware_plan=allocation,
                  gpu_placement=dict(placement, gpu=allocation['gpus'], main_gpu=allocation['main_gpu']))
    if spec.packed and len(allocation['gpus']) > 1:
        config['layer_split'] = 'auto'
    else:
        config.pop('layer_split', None)
    if not spec.packed:
        native = ROOT / 'native-engine' / 'strata-q36-multigpu.exe'
        if not native.is_file():
            raise ValueError('The hardware-aware Qwen35 engine has not been built yet.')
        config['exe'] = str(native)
    if vision:
        # Last, so every earlier step still sees flag/value pairs in args.
        config['vision'] = vision_section(spec, config)
        config['args'] = config['args'] + ['--vision']
    return config

class Job:
    """Closing the app also kills its owned services, even after a crash."""
    def __init__(self):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        self.kernel, self.handle = kernel, kernel.CreateJobObjectW(None, None)
        # JOBOBJECT_EXTENDED_LIMIT_INFORMATION, with architecture-correct alignment.
        class Basic(ctypes.Structure):
            _fields_ = [('a', ctypes.c_longlong), ('b', ctypes.c_longlong), ('flags', wintypes.DWORD),
                        ('minimum', ctypes.c_size_t), ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.handle or not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def add(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            process.terminate()
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        # The primary thread cannot execute or create an engine before job assignment.
        try:
            threads = psutil.Process(process.pid).threads()
            if len(threads) != 1:
                raise RuntimeError('The suspended service did not have one primary thread.')
            thread = self.kernel.OpenThread(0x0002, False, threads[0].id)
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if self.kernel.ResumeThread(thread) == 0xffffffff:
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self.kernel.CloseHandle(thread)
        except Exception:
            process.terminate()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def desktop_configuration_signature(model):
    spec = next((item for item in SPECS if item.id == model), None)
    paths = ([ROOT / 'native-engine/strata-q36-multigpu.exe'] if spec and not spec.packed
             else sorted((HOME / 'components/engine').glob('*/strata.exe')))
    engines = []
    for path in paths:
        try:
            stat = path.stat()
            engines.append({'path': str(path.resolve()), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
        except FileNotFoundError:
            engines.append({'path': str(path), 'missing': True})
    hardware = {'engines': engines, 'ram_total': psutil.virtual_memory().total,
                'logical_cpus': psutil.cpu_count()}
    return configuration_signature(model, load_settings(), gpu_selection.read_choice(), gpu_selection.cards(), hardware)


class Controller:
    def __init__(self):
        self.operation = None
        self.error = None
        self.progress = None
        self.lock = threading.Lock()
        self.job = None
        self.children = []
        self.model_process = None
        self.cancel = threading.Event()

    def report(self, payload):
        self.progress = dict(payload) if payload else None

    def submit(self, operation, *args, done=None):
        if not self.lock.acquire(blocking=False):
            return False
        self.operation, self.error, self.progress = operation, None, None
        self.cancel.clear()
        def work():
            try:
                getattr(self, operation)(*args)
            except Exception as exc:
                self.error = str(exc)
            finally:
                self.operation = None
                self.progress = None
                self.lock.release()
                if done:
                    done()
        threading.Thread(target=work, daemon=True).start()
        return True

    def spawn(self, args, logfile, cwd=ROOT):
        require_desktop()
        if self.job is None:
            self.job = Job()
        # GATEWAY_AUTOLOAD=1: Control-owned OpenAI gateway may list prepared presets and
        # load/switch on the first chat request for a selected model id.
        env = dict(os.environ, GATEWAY_AUTOLOAD='1', GATEWAY_BACKEND='http://127.0.0.1:8081',
                   STRATA_CONTROL_PARENT=str(os.getpid()), PYTHONUNBUFFERED='1')
        with (ROOT / logfile).open('ab', buffering=0) as log:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004)  # CREATE_SUSPENDED
        try:
            self.job.add(process)
            self.job.resume(process)
        except Exception:
            # A suspended child that failed job assignment/resume must not be
            # left outside children/model_process and become unreapable.
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise
        self.children.append(process)
        return process

    def start(self):
        require_desktop()
        state = observe()
        if state['state'] == 'Unknown':
            raise RuntimeError('Cannot confirm current state. Resolve the status error before starting.')
        if any(process['role'] != 'gateway' for process in state.get('processes', [])):
            raise RuntimeError('Stop the existing Strata model before starting the desktop-managed server.')
        if state['gateway']['state'] == 'up':
            if state['state'] == 'Needs attention':
                raise RuntimeError(state['detail'])
            return
        host = bind_host()
        self.report({'phase': 'starting', 'message': f'Starting API on {host}:8086…',
                     'bytes_done': 0, 'bytes_total': 0, 'path': ''})
        process = self.spawn([str(ROOT / '.venv/Scripts/python.exe'), '-m', 'uvicorn', 'gateway:app',
                              '--host', host, '--port', '8086', '--app-dir', str(ROOT)], 'desktop-api.log')
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise RuntimeError('Startup cancelled. Stopping Strata…')
            if process.poll() is not None:
                raise RuntimeError('API exited. See desktop-api.log.')
            result = probe(8086, '/health')
            if result['state'] == 'up' and result['data'].get('model_routing') in (
                    'active-only', 'prepared-presets-v1'):
                return
            time.sleep(.4)
        raise RuntimeError('API did not become ready. See desktop-api.log.')

    def download(self, asset_ids):
        """Fetch pinned model assets (image encoders). Starts no engine."""
        require_desktop()
        for asset_id in asset_ids:
            model_downloads.download(model_downloads.asset(asset_id), ROOT / 'models',
                                     progress=self.report, cancel=self.cancel.is_set)

    def load(self, model, context, mtp, storage=None, advanced=None, vision=False):
        with lifecycle_lock(RUNTIME):
            try:
                return self._load(model, context, mtp, storage, advanced, vision)
            except Exception:
                if self.model_process is not None:
                    self._unload()
                raise

    def _load(self, model, context, mtp, storage=None, advanced=None, vision=False):
        require_desktop()
        state = observe()
        if state['gateway']['state'] != 'up' or state['state'] != 'Server ready':
            raise RuntimeError('Start the server first. Unload the current model before loading another.')
        for process in psutil.process_iter(['name']):
            name = (process.info['name'] or '').lower()
            if name in ('llama-server.exe', 'llama-server', 'ollama_llama_server.exe', 'maya.exe'):
                raise RuntimeError('Another model engine is running. Stop its model before loading Strata.')
        preferences = dict(load_settings().get('model_preferences') or {})
        preferences[model] = {'context': context, 'mtp': bool(mtp), 'vision': bool(vision)}
        save_settings({'model_preferences': preferences})
        selection = storage
        if isinstance(storage, str):
            # Older callers passed a verified custom folder path.
            selection = {'mode': 'custom', 'path': storage}
        if isinstance(selection, dict) and selection.get('mode') == 'nvme':
            selection = dict(selection, model=model)
        self.report({'phase': 'preparing', 'message': 'Preparing model storage…',
                     'bytes_done': 0, 'bytes_total': 0, 'path': ''})
        storage_root = resolve_storage_root(
            selection, progress=self.report, cancel=self.cancel.is_set) if selection else None
        host = bind_host()
        signature = desktop_configuration_signature(model)
        config = build_config(model, context, mtp, storage_root, host=host, advanced=advanced, vision=bool(vision))
        if desktop_configuration_signature(model) != signature:
            raise RuntimeError('Control settings or GPU hardware changed during preparation. Retry the model load.')
        config['control_signature'] = signature
        RUNTIME.mkdir(exist_ok=True)
        config_path = RUNTIME / 'model.json'
        config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        terminal = Path(config['cwd'])
        self.report({'phase': 'loading', 'message': 'Loading model weights. This can take several minutes.',
                     'bytes_done': 0, 'bytes_total': 0, 'path': ''})
        process = self.spawn([str(terminal / 'python/python.exe'), str(ROOT / 'desktop_backend.py'), '--engine', 'strata',
                              '--config', str(config_path), '--host', host, '--port', '8081'], 'desktop-model.log', terminal)
        self.model_process = process
        deadline = time.monotonic() + 1800
        while time.monotonic() < deadline:
            if self.cancel.is_set():
                raise RuntimeError('Loading cancelled. Stopping Strata…')
            if process.poll() is not None:
                raise RuntimeError('Model server exited. See desktop-model.log.')
            result = probe(8081, '/v1/status')
            data = result.get('data', {})
            if result['state'] == 'up' and data.get('loaded') is True:
                if data.get('model') != model:
                    raise RuntimeError('A different model is responding. Stop Strata before retrying.')
                actual = (data.get('context') or {}).get('native')
                expected = int(config['args'][config['args'].index('--max-context') + 1])
                if actual != expected:
                    raise RuntimeError(f'Engine context is {actual}, but {expected} was requested.')
                return
            time.sleep(1)
        raise RuntimeError('Model loading timed out. Click Stop to release any remaining processes.')

    @staticmethod
    def terminate(record):
        try:
            process = psutil.Process(record.pid)
            if process.create_time() != record.born or str(Path(process.exe()).resolve()) != record.exe:
                raise RuntimeError('A process identity changed during stop. Check status before retrying.')
            process.terminate()
            try:
                process.wait(timeout=30 if record.role == 'engine' else 8)
            except psutil.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=10 if record.role == 'engine' else 5)
                except psutil.TimeoutExpired:
                    raise RuntimeError(f'Strata process {record.pid} is still exiting. Stop is not confirmed; check live status before retrying.') from None
        except psutil.NoSuchProcess:
            pass

    def unload(self):
        with lifecycle_lock(RUNTIME):
            return self._unload()

    def _unload(self):
        """Stop verified Strata hosts before their engines, preventing legacy respawns."""
        records, uncertain = processes()
        if uncertain or any(p.role == 'unverified engine' for p in records):
            raise RuntimeError('A Strata process could not be identified. Its stop cannot be confirmed.')
        for role in ('runtime host', 'model server', 'engine'):
            for record in records:
                if record.role == role:
                    self.terminate(record)
        self.model_process = None
        state = observe()
        if state['backend']['state'] != 'absent' or any(p['role'] != 'gateway' for p in state['processes']):
            raise RuntimeError('Model shutdown is not confirmed. Check live status and retry Stop.')
        # Drop exited Popen objects so their Windows process handles can close
        # during repeated gateway switches. Keep a live owned gateway, if any.
        remaining = []
        for process in self.children:
            if process.poll() is None:
                remaining.append(process)
            else:
                process.wait(timeout=0)
        self.children = remaining

    def gateway_load(self, model):
        """Use current Control settings and inventory for an API-selected package."""
        require_desktop()
        with lifecycle_lock(RUNTIME):
            state = observe()
            if state['state'] not in ('Server ready', 'Model ready'):
                raise ModelSwitchError('The current model state is uncertain or not ready; no model was stopped.', 'backend_unavailable')
            # Validate explicit controls before replacing a healthy loaded model.
            # Resource planning still happens only after its allocations are gone.
            settings = load_settings()
            preferences = (settings.get('model_preferences') or {}).get(model) or {}
            advanced = settings.get('advanced') or {}
            import hardware_planner
            percent = hardware_planner.parse_ram_use(advanced.get('ram_use_percent', 'auto'))
            spec = next((item for item in SPECS if item.id == model), None)
            if spec is not None and not spec.packed and percent == 0:
                raise ValueError('This model requires a RAM expert cache; 0% file-backed mode is unavailable')
            if spec is not None and spec.packed:
                hardware_planner.flash_tuning_args(advanced, mtp=preferences.get('mtp', True))
            signature = desktop_configuration_signature(model)
            if state['state'] == 'Model ready':
                data = state['backend']['data']
                config = owned_backend_configuration(ROOT, HOME, RUNTIME, state, psutil)
                activity = data.get('activity') or {}
                if type(activity.get('in_flight')) is not int or activity['in_flight'] != 0:
                    raise ModelSwitchError('The backend is busy; no model was stopped.', 'backend_busy')
                if data.get('model') == model and config.get('control_signature') == signature:
                    args = config.get('args') or []
                    try:
                        expected = int(args[args.index('--max-context') + 1])
                    except (ValueError, IndexError):
                        expected = None
                    if expected and (data.get('context') or {}).get('native') == expected:
                        return data
                self._unload()
            # Re-read free memory only after the old engine is confirmed gone.
            settings = load_settings()
            preferences = (settings.get('model_preferences') or {}).get(model) or {}
            try:
                self._load(model, preferences.get('context', 'Auto'), preferences.get('mtp', True),
                           storage=settings.get('model_storage'), advanced=settings.get('advanced'),
                           vision=preferences.get('vision', settings.get('vision', False)))
            except Exception:
                if self.model_process is not None:
                    self._unload()
                raise
            result = probe(8081, '/v1/status')
            data = result.get('data') or {}
            if result['state'] != 'up' or data.get('loaded') is not True or data.get('model') != model:
                raise ModelSwitchError('The selected model did not become ready.', 'model_load_failed')
            return data

    def stop(self):
        with lifecycle_lock(RUNTIME):
            return self._stop()

    def _stop(self):
        self._unload()
        records, uncertain = processes()
        for record in records:
            if record.role == 'gateway':
                self.terminate(record)
        if self.job:
            self.job.close()
            self.job = None
        for process in self.children:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                raise RuntimeError('An owned process has not exited; stop is not confirmed.')
        self.children.clear()
        if observe()['state'] != 'Stopped':
            raise RuntimeError('Strata is not confirmed stopped. Keep this app open and check its status.')
