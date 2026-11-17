"""At the sweep boundary, retain all data and budget a balanced second sweep."""

from desktop_policy import require_desktop
require_desktop()
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime,timezone

import httpx
import psutil

ROOT=Path(__file__).resolve().parent
OUT=Path(sys.argv[1])
deadline=datetime.fromisoformat('2026-10-30T12:03:07+00:00').timestamp()
while time.time()<deadline-1500:
    data=json.loads((OUT/'results.json').read_text())
    if data.get('completed_utc'):raise SystemExit('Runner completed before rebalance was needed')
    qwen=data.get('models',{}).get('qwen36',{})
    if 1 in data.get('completed_sweeps',[]) and any(load['sweep']==2 for load in qwen.get('loads',[])):
        if any(r['sweep']==2 and r['phase']=='matrix' and r['accepted'] for r in qwen['requests']):
            raise SystemExit('Second sweep already has matrix samples; refusing to change its counts')
        status=httpx.get('http://127.0.0.1:8081/v1/status',timeout=4,trust_env=False).json()
        if status.get('loaded') and 'qwen3.6' in status.get('model','') and status.get('cache_max_tokens')==65536:
            break
    time.sleep(3)
else:raise SystemExit('No safe handoff window within time budget')
workers=[]
for proc in psutil.process_iter(['pid','exe','cmdline']):
    exe=(proc.info['exe'] or '').lower().replace('\\','/')
    command=' '.join(proc.info['cmdline'] or []).lower().replace('\\','/')
    if exe.endswith('python.exe') and '/.venv/' not in exe and str(ROOT/'clean_benchmark.py').lower().replace('\\','/') in command:
        workers.append(proc)
if len(workers)!=1:raise SystemExit('Benchmark worker identity ambiguous; no changes made')
print('Sweep1 complete; handing off at the freshly loaded sweep2 Qwen3.6 backend.',flush=True)
workers[0].terminate()
workers[0].wait(timeout=15)
env=dict(os.environ)
env['CLEAN_RESUME_REASON']='Budget-balanced second sweep: retain two first-sweep samples and collect one independent second-sweep sample per cell (three overall). Reused the already freshly loaded second-sweep backend.'
(OUT/'rebalance.json').write_text(json.dumps({'at':datetime.now(timezone.utc).isoformat(),'old_worker_pid':workers[0].pid,'reason':env['CLEAN_RESUME_REASON']},indent=2))
worker=subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'clean_benchmark.py'),str(OUT)],
                        cwd=ROOT,env=env,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(3)
with (OUT/'deadline-guard-rebalanced.log').open('a') as log:
    subprocess.Popen([str(ROOT/'.venv/Scripts/python.exe'),str(ROOT/'clean_deadline_guard.py'),str(OUT)],
                     cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW|subprocess.DETACHED_PROCESS)
raise SystemExit(worker.wait())
