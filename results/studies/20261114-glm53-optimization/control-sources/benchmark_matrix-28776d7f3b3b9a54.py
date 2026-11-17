"""Local sequential 64K-context speed comparison; engine timings and raw evidence."""

from desktop_policy import require_desktop
require_desktop()
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'upstream/tools'))
import strata_bench
from strata_bench import Suite, chat as upstream_chat, digest, stat
import manage

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'benchmark-matrix' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
OUT.mkdir(parents=True, exist_ok=True)
STRATA = Path(os.environ['LOCALAPPDATA']) / 'Strata/terminal/current/bin/strata.cmd'
BASE = 'http://127.0.0.1:8081'
NAMES = {'flashnext32k', 'benchiq3s64k', 'benchqwen3664k'}
TASK = ('\n\nThe word list above is background data. Now write a detailed technical tutorial of at least '
        '1500 words about implementing a reliable disk-backed cache in Python, including substantial '
        'Python code, eviction, locking, atomic writes, corruption recovery and tests. Continue until '
        'all sections are complete. Do not summarize the word list. Start with the tutorial.')
REPORT = {'created_utc': datetime.now(timezone.utc).isoformat(),
          'method': 'Custom speed matrix; configured context 65536; thinking off, temperature zero, '
                    'parallel one, vision off, MTP on, 2000 MiB reserve; three unique-prefix requests '
                    'per size, 512 output cap; sizes are actual input lengths, not separate allocations.',
          'models': {}, 'failures': []}
if (OUT / 'results.json').exists():
    REPORT = json.loads((OUT / 'results.json').read_text())

ACTIVE_MODEL = ''

def chat(base, key, messages, max_tokens, think=False, tools=None, timeout=900):
    if ACTIVE_MODEL != 'qwen36' and os.getenv('BENCHMARK_ZERO_PENALTIES') != '1':
        return upstream_chat(base, key, messages, max_tokens, think, tools, timeout)
    body = {'model': 'strata', 'messages': messages, 'stream': True, 'max_tokens': max_tokens,
            'temperature': 0, 'presence_penalty': 0, 'frequency_penalty': 0, 'repetition_penalty': 1,
            'reasoning_effort': 'none', 'chat_template_kwargs': {'enable_thinking': False}}
    if tools:
        body['tools'] = tools
    start = time.perf_counter()
    result = {'first': None, 'text': '', 'usage': None, 'timings': None, 'err': None, 'pieces': 0}
    try:
        with httpx.stream('POST', base + '/v1/chat/completions', json=body, timeout=timeout, trust_env=False) as response:
            if response.status_code != 200:
                raise RuntimeError(f'HTTP {response.status_code}: {response.read()[:300]!r}')
            for line in response.iter_lines():
                if not line.startswith('data:'):
                    continue
                payload = line[5:].strip()
                if payload == '[DONE]':
                    break
                event = json.loads(payload)
                if event.get('error'):
                    result['err'] = str(event['error'])
                for choice in event.get('choices', []):
                    delta = choice.get('delta', {})
                    piece = (delta.get('content') or '') + (delta.get('reasoning_content') or '')
                    if piece:
                        if result['first'] is None:
                            result['first'] = time.perf_counter() - start
                        result['pieces'] += 1
                        result['text'] += delta.get('content') or ''
                if event.get('usage'):
                    result['usage'] = event['usage']
                if event.get('timings'):
                    result['timings'] = event['timings']
    except Exception as exc:
        result['err'] = repr(exc)
    result['total'] = time.perf_counter() - start
    return result

strata_bench.chat = chat

def save():
    (OUT / 'results.json').write_text(json.dumps(REPORT, indent=2), encoding='utf-8')

def cli(args, logfile, timeout=180):
    with (OUT / logfile).open('a', encoding='utf-8') as log:
        return subprocess.run(['cmd.exe', '/d', '/c', str(STRATA), *args], cwd=ROOT,
                              stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                              creationflags=manage.HIDDEN)

def snapshot():
    data = {'at': datetime.now(timezone.utc).isoformat(), 'resources': manage.resources()}
    try:
        rows = httpx.get('http://127.0.0.1:1234/api/v0/models', timeout=3, trust_env=False).json()['data']
        data['other_loaded_models'] = [r.get('id') for r in rows if r.get('state') == 'loaded']
    except Exception:
        data['lm_studio_api'] = 'unavailable'
    proc = subprocess.run(['nvidia-smi'], capture_output=True, text=True,
                          creationflags=manage.HIDDEN, timeout=10)
    data['nvidia_smi'] = proc.stdout
    return data

def stop_backend():
    running = manage.strata_status().get('running', [])
    if not running:
        return
    if len(running) != 1:
        raise RuntimeError('Refusing to stop multiple runtime instances')
    row = running[0]
    cfg = json.loads(Path(row['config']).read_text())
    serialized = json.dumps(cfg).lower().replace('\\\\', '/').replace('\\', '/')
    if row.get('name', '').lower() not in NAMES or row.get('port') != 8081 or str(ROOT / 'models').lower().replace('\\', '/') not in serialized:
        raise RuntimeError('Backend ownership could not be established')
    result = cli(['launch', '--preset', row['name'], '--stop'], 'service-control.log', 90)
    if result.returncode:
        raise RuntimeError('Owned backend stop failed')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            httpx.get(BASE + '/health', timeout=1, trust_env=False)
        except httpx.RequestError:
            return
        time.sleep(1)
    raise RuntimeError('Backend still responds after stop')

def start_model(key, family, model, preset):
    stop_backend()
    free = snapshot()
    (OUT / f'{key}-unloaded.json').write_text(json.dumps(free, indent=2))
    if free.get('other_loaded_models'):
        raise RuntimeError('Another LM Studio model is loaded: ' + str(free['other_loaded_models']))
    args = ['launch', '--family', family, '--model', model, '--data-dir', str(ROOT / 'models'),
            '--context', '64k', '--parallel', '1', '--vision', 'off', '--drafting', 'mtp', '--mtp', 'on',
            '--set', 'vram-reserve-mib=2000', '--set', 'STRATA_VRAM_FREE=device',
            '--set', 'conversation-cache-mib=0', '--set', 'STRATA_ROUTE_TAIL_SKIP=0',
            '--set', 'STRATA_HC_Q8=1', '--tune', 'skip', '--access', 'local', '--port', '8081',
            '--save-preset', preset, '--yes', '--no-tui']
    if family in ('qwen', 'unsloth'):
        args += ['--kv', 'int8', '--set', 'spec=4', '--set', 'spec-min-p=0.5', '--set', 'kv-resident=off',
                 '--set', 'resident-budget-gib=off', '--set', 'mmap-experts=off', '--set', 'resident-experts=off']
    else:
        # Q36 ignores --vram-reserve-mib; its expert budget must leave space for
        # 1.10 GiB staging, the Windows desktop, and the engine's other buffers.
        args += ['--set', 'expert-vram=3.0']
    print(f'Loading {key}, context 65536, MTP on ...', flush=True)
    log = (OUT / f'{key}-launch.log').open('w', encoding='utf-8')
    p = subprocess.Popen(['cmd.exe', '/d', '/c', str(STRATA), *args], cwd=ROOT, stdout=log,
                         stderr=subprocess.STDOUT, creationflags=manage.HIDDEN)
    deadline = time.monotonic() + 1800
    try:
        while time.monotonic() < deadline:
            try:
                s = httpx.get(BASE + '/v1/status', timeout=4, trust_env=False).json()
                if s.get('loaded'):
                    if key == 'iq3s' and 'iq3_s' not in s.get('model', ''):
                        raise RuntimeError('Wrong model loaded')
                    if key == 'qwen36' and 'qwen3.6' not in s.get('model', ''):
                        raise RuntimeError('Wrong model loaded')
                    if s.get('cache_max_tokens') != 65536:
                        raise RuntimeError('Unexpected configured context: ' + str(s))
                    print(f'{key} ready: {s["model"]}', flush=True)
                    return s
            except (httpx.RequestError, ValueError):
                pass
            if p.poll() not in (None, 0):
                raise RuntimeError(f'Launcher exited {p.returncode}: see {log.name}')
            time.sleep(3)
        raise TimeoutError('Loading exceeded 30 minutes')
    finally:
        log.close()

def measured(messages, limit=512):
    raw = chat(BASE, '', messages, limit, timeout=900)
    d = digest(raw)
    d['raw'] = raw
    with (OUT / 'requests.jsonl').open('a', encoding='utf-8') as f:
        f.write(json.dumps(d) + '\n')
    return d

def aggregate(rows):
    good = [r for r in rows if r['ok']]
    sustained = [r for r in good if r['generated_tokens'] >= 256 and r.get('decode_tps_source') == 'engine']
    fresh = [r for r in good if r.get('reused_tokens') is not None and r['reused_tokens'] <= 64
             and r.get('prompt_tps_source') == 'engine']
    return {'prefill_tps': stat(r['prompt_tps'] for r in fresh),
            'decode_tps': stat(r['decode_tps'] for r in sustained),
            'ttft_s': stat(r['ttft_s'] for r in good),
            'wall_s': stat(r['total_s'] for r in good),
            'prompt_tokens': stat(r['prompt_tokens'] for r in good),
            'generated_tokens': stat(r['generated_tokens'] for r in good),
            'reused_tokens': stat(r['reused_tokens'] for r in good),
            'drafts_offered': sum(r.get('drafts_offered') or 0 for r in good),
            'drafts_accepted': sum(r.get('drafts_accepted') or 0 for r in good),
            'requests': rows}

def run_model(key, family, model, preset):
    global ACTIVE_MODEL
    ACTIVE_MODEL = key
    existing = None
    if key == 'qwen36':
        try:
            candidate = httpx.get(BASE + '/v1/status', timeout=5, trust_env=False).json()
            cfg = json.loads((Path(os.environ['LOCALAPPDATA']) / 'Strata/launch/benchqwen3664k.json').read_text())
            args = cfg['args']
            if candidate.get('loaded') and 'qwen3.6' in candidate.get('model', '') and candidate.get('cache_max_tokens') == 65536 and args[args.index('--expert-vram') + 1] == '3.0':
                existing = candidate
        except (httpx.HTTPError, ValueError, KeyError):
            pass
    loaded = existing or start_model(key, family, model, preset)
    if existing:
        print('Reusing verified Qwen3.6 backend with explicit expert budget; no reload.', flush=True)
    result = {'status': loaded, 'before': snapshot(), 'matrix': {}}
    result['request_sampling'] = {'temperature': 0, 'presence_penalty': 0 if key == 'qwen36' else 'model default (1.5)',
                                  'repetition_penalty': 1, 'frequency_penalty': 0}
    REPORT['models'][key] = result
    save()
    if result['before'].get('other_loaded_models'):
        raise RuntimeError('Other model loaded during benchmark')
    suite = Suite(BASE, '', 65536, 3, False, log=lambda s: print(key + ': ' + s, flush=True))
    suite.warm_up()
    suite.run_chat()
    result['short_chat'] = suite.out['chat']
    REPORT['failures'].extend({'model': key, **failure} for failure in suite.out['failures'])
    for size in (4096, 16384, 32768, 60000):
        rows = []
        for run in range(3):
            messages = suite.prompt_for(size - 90, run)
            messages[0]['content'] = '[unique ' + uuid.uuid4().hex + ']\n' + messages[0]['content'] + TASK
            d = measured(messages)
            d.update(target_tokens=size, run=run + 1)
            rows.append(d)
            print(f'{key} ~{size} run {run+1}: input={d["prompt_tokens"]}, cached={d["reused_tokens"]}, '
                  f'prefill={d["prompt_tps"]}, decode={d["decode_tps"]}, out={d["generated_tokens"]}, '
                  f'TTFT={d["ttft_s"]}, error={d["err"]}', flush=True)
            result['matrix'][str(size)] = aggregate(rows)
            save()
            if not d['ok']:
                REPORT['failures'].append({'model': key, 'target': size, 'error': d['err']})
                save()
                if 'memory' in str(d['err']).lower() or d['err'] is None:
                    break
    cached_messages = suite.prompt_for(16384 - 90, 'cache')
    cached_messages[0]['content'] = '[unique ' + uuid.uuid4().hex + ']\n' + cached_messages[0]['content'] + TASK
    seed = measured(cached_messages, 256)
    cached = [measured(cached_messages, 256) for _ in range(3)]
    result['prefix_reuse'] = {'seed': seed, 'summary': aggregate(cached)}
    result['after'] = snapshot()
    for row in manage.strata_status().get('running', []):
        for source in (Path(row['config']), Path(row['config']).with_suffix('.engine.log')):
            if source.exists():
                shutil.copy2(source, OUT / (key + '-' + source.name))
    save()

def restart_gateway():
    if manage.tracked('gateway'):
        return
    p = manage.spawn([str(manage.PYTHON), '-m', 'uvicorn', 'gateway:app', '--host', '127.0.0.1',
                      '--port', '8086', '--app-dir', str(ROOT)], 'gateway.log')
    manage.pidfile('gateway', p.pid)

def main():
    print('Evidence directory:', OUT, flush=True)
    REPORT['before'] = snapshot()
    save()
    gateway = manage.tracked('gateway')
    if gateway:
        subprocess.run(['taskkill.exe', '/PID', str(gateway['pid']), '/T', '/F'], check=True,
                       capture_output=True, creationflags=manage.HIDDEN)
    try:
        if 'prefix_reuse' not in REPORT.get('models', {}).get('iq3s', {}):
            run_model('iq3s', 'qwen', 'IQ3_S', 'BenchIQ3S64K')
        run_model('qwen36', 'qwen36', 'UD-Q4_K_XL', 'BenchQwen3664K')
    except Exception as exc:
        REPORT['failures'].append({'fatal': repr(exc)})
        print('BENCHMARK ERROR:', repr(exc), flush=True)
    finally:
        print('Restoring original FlashNext32K backend and public gateway ...', flush=True)
        try:
            stop_backend()
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(ROOT / 'launch-engine.ps1')], cwd=ROOT,
                                    creationflags=manage.HIDDEN, timeout=1800)
            if result.returncode:
                raise RuntimeError('Original preset launch failed')
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                try:
                    if httpx.get(BASE + '/v1/status', timeout=4, trust_env=False).json().get('loaded'):
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(3)
            else:
                raise RuntimeError('Original model did not become ready')
            restart_gateway()
            REPORT['restored'] = True
        except Exception as exc:
            REPORT['restore_error'] = repr(exc)
            print('RESTORE ERROR:', repr(exc), flush=True)
        REPORT['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save()
    print('Completed. Results:', OUT / 'results.json', flush=True)

if __name__ == '__main__':
    main()
