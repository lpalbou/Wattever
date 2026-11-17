"""Coordination shared by the desktop and its API process."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading

from model_catalog import ModelSwitchError, SPECS

_process_lock = threading.Lock()


@contextmanager
def lifecycle_lock(runtime):
    """Hold one model transition across threads and Control/gateway processes."""
    if not _process_lock.acquire(blocking=False):
        raise ModelSwitchError('Another Strata Control model operation is in progress.', 'backend_busy')
    stream = None
    locked = False
    try:
        runtime = Path(runtime)
        runtime.mkdir(parents=True, exist_ok=True)
        stream = (runtime / 'model-lifecycle.lock').open('a+b')
        if stream.seek(0, 2) == 0:
            stream.write(b'\0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise ModelSwitchError('Another Strata Control model operation is in progress.', 'backend_busy') from None
        yield
    finally:
        try:
            if stream is not None:
                try:
                    if locked:
                        stream.seek(0)
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                finally:
                    stream.close()
        finally:
            _process_lock.release()


def configuration_signature(model, settings, gpu_choice, inventory, engine=None):
    """Capacity/identity/configuration only: owned allocations must not invalidate it."""
    fields = ('index', 'uuid', 'name', 'memory_mib', 'compute_capability', 'power_limit_w')
    cards = [{key: card.get(key) for key in fields} for card in inventory]
    cards.sort(key=lambda card: card['index'])
    preferences = (settings.get('model_preferences') or {}).get(model) or {}
    selected = {key: settings.get(key) for key in ('model_storage', 'advanced', 'vision', 'lan_access', 'conversation_cache')}
    payload = {'version': 2, 'model': model, 'settings': selected, 'preferences': preferences,
               'gpu_choice': gpu_choice, 'inventory': cards, 'engine': engine}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()


def owned_backend_configuration(root, home, runtime, state, psutil_module):
    """Verify the actual listener's executable and config before gateway replacement."""
    backend = state.get('backend') or {}
    data = backend.get('data') or {}
    known = {spec.id: spec for spec in SPECS}
    spec = known.get(data.get('model'))
    if spec is None or state.get('uncertain'):
        raise ModelSwitchError('Backend ownership cannot be verified; no model was stopped.', 'unowned_backend')
    try:
        pids = {entry.pid for entry in psutil_module.net_connections(kind='tcp')
                if entry.status == psutil_module.CONN_LISTEN and entry.laddr.port == 8081
                and entry.laddr.ip in ('127.0.0.1', '0.0.0.0', '::', '::1')}
        if len(pids) != 1 or None in pids:
            raise ValueError('ambiguous listener')
        process = psutil_module.Process(next(iter(pids)))
        if not Path(process.exe()).resolve().is_relative_to(Path(home).resolve() / 'terminal'):
            raise ValueError('foreign executable')
        args = process.cmdline()
        if 'serve.server' not in args and str(Path(root) / 'desktop_backend.py') not in args:
            raise ValueError('foreign server')
        related = {process.pid, *(child.pid for child in process.children(recursive=True))}
        for ancestor in process.parents():
            if (Path(ancestor.exe()).resolve().is_relative_to(Path(home).resolve() / 'terminal')
                    and 'strata_control.runtime' in ancestor.cmdline()):
                related.add(ancestor.pid)
        if any(row.get('role') != 'gateway' and row.get('pid') not in related
               for row in state.get('processes', [])):
            raise ValueError('another Strata model is present')
        path = Path(args[args.index('--config') + 1]).resolve()
        allowed = {(Path(runtime) / 'model.json').resolve(),
                   (Path(home) / 'launch' / (spec.slug + '.json')).resolve()}
        if path not in allowed:
            raise ValueError('foreign configuration')
        config = json.loads(path.read_text(encoding='utf-8'))
        valid_exe = config.get('exe') == 'component:strata-engine'
        if not spec.packed:
            valid_exe = valid_exe or Path(str(config.get('exe'))).resolve() == (Path(root) / 'native-engine/strata-q36-multigpu.exe').resolve()
        if config.get('model_name') != spec.id or not valid_exe:
            raise ValueError('foreign model')
        if any(config.get(key) for key in ('before_load', 'beforeLoad', 'mcp_servers', 'mcpServers', 'api_key')):
            raise ValueError('unverified hooks')
        if config.get('port') != 8081 or config.get('host') not in ('127.0.0.1', '0.0.0.0'):
            raise ValueError('foreign port')
        return config
    except (OSError, ValueError, KeyError, IndexError, TypeError, psutil_module.Error):
        raise ModelSwitchError('Backend ownership cannot be verified; no model was stopped.', 'unowned_backend') from None
