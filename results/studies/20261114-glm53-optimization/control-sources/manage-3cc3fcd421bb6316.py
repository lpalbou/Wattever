#!/usr/bin/env python3
"""Manage the Flash Next provisioning process and validation."""
import argparse
import ctypes
import json
import msvcrt
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx
from model_catalog import ModelSwitchError, SPECS

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / '.venv' / 'Scripts' / 'python.exe'
SELF = Path(__file__).resolve()
LAUNCHER = ROOT / 'launch-engine.ps1'
STATE = ROOT / 'provisioning-state.json'
HIDDEN = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
DETACHED = HIDDEN | getattr(subprocess, 'DETACHED_PROCESS', 0)
EXPECTED_DOWNLOAD = 98_900_000_000  # Approximate advertised bytes, includes MTP.


class AlreadyRunning(RuntimeError):
    pass


def download_progress():
    completed = 0
    errors = []
    for path in (ROOT / 'models').rglob('*.gguf'):
        if path.with_name(path.name + '.done').is_file():
            try:
                completed += path.stat().st_size
            except OSError as exc:
                errors.append(str(exc))
    for side in (ROOT / 'models').rglob('*.gguf.part.chunks'):
        try:
            data = json.loads(side.read_text(encoding='utf-8'))
            total, size = int(data['total']), int(data['chunk'])
            if data.get('v') != 1 or total <= 0 or size <= 0:
                raise ValueError('invalid chunk metadata')
            count = (total + size - 1) // size
            done = set()
            for first, end in data['done']:
                if not (0 <= first <= end <= count):
                    raise ValueError('invalid chunk range')
                done.update(range(first, end))
            completed += sum(min(size, total - i * size) for i in done)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(f'{side}: {exc}')
    now = time.time()
    result = {'completed_bytes': completed, 'expected_bytes_approx': EXPECTED_DOWNLOAD,
              'sample_unix': now, 'errors': errors}
    weights = ROOT / 'models/models/unsloth-UD-IQ4_XS'
    result['weights_verified'] = all(
        (weights / f'Qwen3.8-Flash-Next-UD-IQ4_XS-{i:05d}-of-00003.gguf.done').is_file()
        for i in range(1, 4))
    result['mtp_prepared'] = (ROOT / 'models/mtp/rt/experts.bin').is_file()
    result['download_complete'] = result['weights_verified'] and result['mtp_prepared']
    if result['download_complete']:
        result['eta_seconds_approx'] = 0
        return result
    try:
        previous = json.loads(STATE.read_text(encoding='utf-8'))['download_progress']
        elapsed = now - previous['sample_unix']
        delta = completed - previous['completed_bytes']
        if elapsed > 0 and delta > 0:
            result['bytes_per_second'] = delta / elapsed
            result['eta_seconds_approx'] = max(0, EXPECTED_DOWNLOAD - completed) / result['bytes_per_second']
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return result


def lock(name):
    handle = (ROOT / (name + '.lock')).open('a+b')
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise AlreadyRunning(f'{name} already running')
    return handle


def strata_status():
    cmd = Path(os.environ.get('LOCALAPPDATA', '')) / 'Strata/terminal/current/bin/strata.cmd'
    try:
        p = subprocess.run(['cmd.exe', '/d', '/c', str(cmd), 'launch', '--status', '--output', 'json'],
                           capture_output=True, text=True, timeout=20, creationflags=HIDDEN)
        # This Strata release prints a human summary after its JSON status object.
        data, _ = json.JSONDecoder().raw_decode(p.stdout.lstrip())
        if p.returncode or not data.get('ok'):
            raise ValueError('Strata status failed')
        return data
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {'error': str(exc), 'running': []}


def owned_backend(data):
    """Read current Control ownership; legacy launch rows are not authority."""
    try:
        import strata_desktop as desktop
        from control_lifecycle import owned_backend_configuration
        state = desktop.observe()
        if state.get('state') != 'Model ready':return None
        config = owned_backend_configuration(desktop.ROOT,desktop.HOME,desktop.RUNTIME,state,desktop.psutil)
        if config.get('model_name') not in {spec.id for spec in SPECS}:return None
        row = {'name':'desktop-control','profile':'desktop-control','port':8081,
               'config':str(desktop.RUNTIME/'model.json')}
        return row, config
    except (ModelSwitchError, OSError, ValueError, TypeError, KeyError):
        pass
    return None


def collect_logs(owned):
    if not owned:
        return []
    row, config = owned
    runtime = Path(os.environ.get('STRATA_RUNTIME_DIR') or (Path(os.environ.get('LOCALAPPDATA', '')) / 'Strata/runtime'))
    paths = [runtime / (row['profile'] + '.server.log')]
    paths.append(Path(row['config']).with_suffix('.engine.log'))
    if isinstance(config.get('log'), str):
        paths.append(Path(config['log']))
    copies = []
    for i, source in enumerate(paths):
        try:
            if source.is_file():
                dest = ROOT / f'strata-engine-{i}.log'
                shutil.copyfile(source, dest)
                copies.append(str(dest))
        except OSError:
            pass
    return copies


def memory_audit():
    """Interpret the successful full-prefix pin plus exact-suffix lock startup path."""
    path = Path(os.environ.get('LOCALAPPDATA', '')) / 'Strata/launch/flashnext32k.engine.log'
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
        starts = [i for i, line in enumerate(lines) if '[strata]' in line and 'engine started:' in line]
        if starts:
            lines = lines[starts[-1]:]
        arena = next((line for line in reversed(lines) if 'expert arena:' in line), '')
        loaded = next((line for line in reversed(lines) if re.search(r'loaded [\d.]+ GiB at', line)), '')
        complete = bool(re.search(r'expert arena: locked \d+ MiB via working-set minimum \+ VirtualLock;', arena)
                        and re.search(r'\d+ slices pinned', arena) and 'stopped' not in arena.lower()
                        and re.search(r'loaded 55\.4\d GiB', loaded))
        return {'full_expert_ram_allocation': 'confirmed_at_last_startup' if complete else 'unverified',
                'expert_gib': 55.43 if complete else None, 'evidence': [arena, loaded],
                'log': str(path), 'model': 'qwen3.8-flash-next-unsloth-ud-iq4_xs',
                'scope': 'Historical FlashNext32K startup evidence only, not the current model or memory residency. Other process and system memory may page.'}
    except OSError:
        return {'full_expert_ram_allocation': 'unverified'}


def save_state(status, **fields):
    audit = memory_audit()
    data = {'updated_utc': datetime.now(timezone.utc).isoformat(), 'status': status,
            'full_expert_ram_allocation': audit['full_expert_ram_allocation'],
            'startup_memory_audit': audit, 'download_progress': download_progress(), **fields}
    tmp = STATE.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(data, indent=2), encoding='utf-8')
    os.replace(tmp, STATE)


def resources():
    class Memory(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong),
                    ('total_phys', ctypes.c_ulonglong), ('avail_phys', ctypes.c_ulonglong),
                    ('total_page', ctypes.c_ulonglong), ('avail_page', ctypes.c_ulonglong),
                    ('total_virtual', ctypes.c_ulonglong), ('avail_virtual', ctypes.c_ulonglong),
                    ('avail_extended', ctypes.c_ulonglong)]
    result = {}
    if os.name == 'nt':
        m = Memory()
        m.length = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            result['ram'] = {'total_bytes': m.total_phys, 'available_bytes': m.avail_phys}
    try:
        p = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,memory.used,memory.free',
                            '--format=csv,noheader,nounits'], capture_output=True, text=True,
                           timeout=10, creationflags=HIDDEN)
        result['gpu'] = p.stdout.strip() if p.returncode == 0 else p.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['gpu_error'] = str(exc)
    return result


def read_pid(name):
    try:
        pid = int((ROOT / (name + '.pid')).read_text().strip())
        return pid if pid > 0 else None
    except (OSError, ValueError):
        return None


def tracked(name):
    pid = read_pid(name)
    if not pid:
        return None
    try:
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if ($p) {{ $p.CommandLine }}"],
            capture_output=True, text=True, timeout=10, creationflags=HIDDEN)
    except (OSError, subprocess.TimeoutExpired):
        return None
    cmd = p.stdout.strip()
    normalized = cmd.lower().replace('\\', '/')
    root = str(ROOT).lower().replace('\\', '/')
    if name == 'launcher':
        valid = str(LAUNCHER).lower().replace('\\', '/') in normalized and 'powershell' in normalized
    elif name == 'gateway':
        valid = root in normalized and 'uvicorn' in normalized and 'gateway:app' in normalized and '8086' in normalized
    else:
        valid = str(SELF).lower().replace('\\', '/') in normalized and ('watch' in normalized or 'finish' in normalized)
    return {'pid': pid, 'command_line': cmd} if p.returncode == 0 and valid else None


def probe(url, models=False):
    try:
        response = httpx.get(url, timeout=3, trust_env=False)
        response.raise_for_status()
        data = response.json()
        if models and (not isinstance(data, dict) or not data.get('data')):
            raise ValueError('model list is empty')
        return {'ok': True, 'response': data}
    except (httpx.HTTPError, ValueError) as exc:
        return {'ok': False, 'error': str(exc)}


def spawn(args, logfile):
    # Windows PowerShell exits without executing commands under DETACHED_PROCESS.
    # CREATE_NO_WINDOW hides it while preserving script execution and redirected I/O.
    flags = HIDDEN if Path(args[0]).name.lower() in ('powershell.exe', 'pwsh.exe') else DETACHED
    with (ROOT / logfile).open('ab', buffering=0) as log:
        return subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=flags, close_fds=True)


def pidfile(name, pid):
    (ROOT / (name + '.pid')).write_text(str(pid), encoding='ascii')


def status():
    try:
        state = json.loads(STATE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        state = None
    try:
        raw = (ROOT / 'launch.log').read_bytes()
        encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8'
        tail = raw.decode(encoding, errors='replace').splitlines()[-15:]
    except OSError:
        tail = []
    strata = strata_status()
    return {'launcher': tracked('launcher'), 'gateway_process': tracked('gateway'),
            'watcher': tracked('watcher'), 'backend_models': probe('http://127.0.0.1:8081/v1/models', True),
            'gateway_health': probe('http://127.0.0.1:8086/health'), 'resources': resources(),
            'strata_status': strata, 'backend_owned': bool(owned_backend(strata)),
            'download_progress': download_progress(), 'full_expert_ram_allocation': memory_audit()['full_expert_ram_allocation'],
            'startup_memory_audit': memory_audit(),
            'provisioning_state': state, 'launch_log_tail': tail}


def start(validate):
    from desktop_policy import require_desktop
    require_desktop()
    start_lock = lock('start')
    launcher = tracked('launcher')
    if launcher:
        print(f"Launcher already running (PID {launcher['pid']}).")
    elif probe('http://127.0.0.1:8081/v1/models', True)['ok']:
        print('Backend responds already; no launcher started.')
    else:
        p = spawn(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(LAUNCHER)], 'launcher-output.log')
        pidfile('launcher', p.pid)
        save_state('provisioning', launcher_pid=p.pid, resources=resources())
        print(f'Launcher started (PID {p.pid}).')
    if validate:
        watcher = tracked('watcher')
        if watcher:
            print(f"Watcher already running (PID {watcher['pid']}).")
        else:
            p = spawn([str(PYTHON), str(SELF), 'watch'], 'watch.log')
            print(f'Watcher launch requested (launcher PID {p.pid}); see watch.log and status.')


def watch(args):
    from desktop_policy import require_desktop
    require_desktop()
    watch_lock = lock('watcher')
    pidfile('watcher', os.getpid())
    results = {}
    save_state('provisioning', acceptance=results, resources=resources())
    deadline = time.monotonic() + args.wait_timeout
    launcher_missing_since = None
    while not probe('http://127.0.0.1:8081/v1/models', True)['ok']:
        if not tracked('launcher'):
            launcher_missing_since = launcher_missing_since or time.monotonic()
            if time.monotonic() - launcher_missing_since > 120:
                raise RuntimeError('Launcher stopped and backend remained unavailable for two minutes; inspect launch.log.')
        else:
            launcher_missing_since = None
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out waiting for backend; provisioning may still be running.')
        save_state('provisioning', acceptance=results, resources=resources())
        time.sleep(args.poll)
    owned = owned_backend(strata_status())
    if not owned:
        raise RuntimeError('Backend is available but Strata preset and workspace model configuration could not be verified.')
    models = probe('http://127.0.0.1:8081/v1/models', True)['response']['data']
    if not any('qwen3.8-flash-next' in str(item.get('id', '')).lower() for item in models if isinstance(item, dict)):
        raise RuntimeError('Backend model ID does not identify Qwen3.8-Flash-Next.')
    logs = collect_logs(owned)
    save_state('backend_available', acceptance=results, resources=resources())
    if not tracked('gateway'):
        if probe('http://127.0.0.1:8086/health')['ok']:
            raise RuntimeError('Port 8086 has an untracked gateway; refusing to adopt it.')
        p = spawn([str(PYTHON), '-m', 'uvicorn', 'gateway:app', '--host', '127.0.0.1', '--port', '8086', '--app-dir', str(ROOT)], 'gateway.log')
        pidfile('gateway', p.pid)
    deadline = time.monotonic() + 60
    while not probe('http://127.0.0.1:8086/health')['ok']:
        if not tracked('gateway'):
            raise RuntimeError('Gateway exited during startup; inspect gateway.log.')
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out waiting for gateway health.')
        time.sleep(2)
    save_state('validating', acceptance=results, resources=resources())
    for mode in ('quick', 'bench', 'long'):
        output = ROOT / f'acceptance-{mode}.json'
        output.unlink(missing_ok=True)
        cmd = [str(PYTHON), str(ROOT / 'acceptance.py'), mode, '--timeout', str(args.request_timeout), '--output', str(output)]
        with (ROOT / 'acceptance.log').open('ab', buffering=0) as log:
            try:
                completed = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, timeout=args.test_timeout, creationflags=HIDDEN)
                results[mode] = {'exit_code': completed.returncode, 'path': str(output)}
            except subprocess.TimeoutExpired:
                results[mode] = {'error': 'timed out', 'path': str(output)}
                save_state('failed', error=f'{mode} validation timed out', acceptance=results, resources=resources())
                return 1
        if completed.returncode != 0 or not output.exists():
            save_state('failed', error=f'{mode} validation failed; inspect acceptance.log and results', acceptance=results, resources=resources())
            return 1
        save_state('validating', acceptance=results, resources=resources())
    save_state('ready', acceptance=results, resources=resources(), engine_logs=collect_logs(owned) or logs)
    print('Backend and gateway passed quick, bench and long validation; expert allocation: '
          + memory_audit()['full_expert_ram_allocation'])
    return 0


def stop():
    """Use Control's verified stop, with an atomic idle check for this CLI."""
    import strata_desktop as desktop
    from control_lifecycle import owned_backend_configuration

    class IdleStopController(desktop.Controller):
        def _stop(self):
            # The public inherited stop() already holds the shared lifecycle
            # lock before invoking this hook. Do not take that lock twice.
            state = desktop.observe()
            if state.get('uncertain') or state.get('state') not in ('Stopped','Server ready','Model ready'):
                raise RuntimeError('Backend state is unknown; no processes were stopped.')
            self.backend_stop_attempted = state['state']=='Model ready'
            if self.backend_stop_attempted:
                config = owned_backend_configuration(desktop.ROOT,desktop.HOME,desktop.RUNTIME,state,desktop.psutil)
                data = (state.get('backend') or {}).get('data') or {}
                activity = data.get('activity')
                if not isinstance(activity,dict):
                    raise RuntimeError('Backend activity is unknown; no processes were stopped.')
                queue = activity.get('queue')
                if data.get('model')!=config.get('model_name'):
                    raise RuntimeError('Backend identity changed; no processes were stopped.')
                if type(activity.get('in_flight')) is not int or activity['in_flight']!=0 or ('queue' in activity and (not isinstance(queue,dict) or type(queue.get('waiting')) is not int or queue['waiting']!=0)):
                    raise RuntimeError('Backend is busy or its activity is unknown; no processes were stopped.')
            elif (state.get('backend') or {}).get('state')!='absent':
                raise RuntimeError('Backend absence is not confirmed; no processes were stopped.')
            return super()._stop()

    controller = IdleStopController()
    controller.stop()
    save_state('stopped',backend_stop_attempted=controller.backend_stop_attempted,resources=resources())
    print('Strata Control verified that the server and model stopped.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status', help='show process, backend, gateway and provisioning status')
    start_parser = sub.add_parser('start', help='start or resume provisioning')
    start_parser.add_argument('--validate', action='store_true', help='also start a detached watcher and run validation')
    sub.add_parser('stop', help='stop verified workspace processes and owned preset')
    watcher = sub.add_parser('watch', aliases=['finish'], help='wait for backend, start gateway and run acceptance')
    watcher.add_argument('--poll', type=float, default=30)
    watcher.add_argument('--wait-timeout', type=float, default=259200)
    watcher.add_argument('--request-timeout', type=float, default=600)
    watcher.add_argument('--test-timeout', type=float, default=7200)
    args = parser.parse_args()
    try:
        if args.command == 'status':
            print(json.dumps(status(), indent=2, ensure_ascii=True))
        elif args.command == 'start':
            start(args.validate)
        elif args.command == 'stop':
            stop()
        else:
            if min(args.poll, args.wait_timeout, args.request_timeout, args.test_timeout) <= 0:
                parser.error('timeouts and poll must be positive')
            return watch(args)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        if args.command in ('watch', 'finish') and not isinstance(exc, AlreadyRunning):
            save_state('failed', error=str(exc), resources=resources())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
