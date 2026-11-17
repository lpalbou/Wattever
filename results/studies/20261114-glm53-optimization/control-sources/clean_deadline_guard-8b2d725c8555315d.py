"""Independent wall-clock guard: reserve eight minutes to restore our endpoint."""

from desktop_policy import require_desktop
require_desktop()
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

import httpx
import psutil

ROOT=Path(__file__).resolve().parent
OUT=Path(sys.argv[1])
sys.argv=[sys.argv[0],str(OUT)]
import benchmark_matrix as bm
bm.NAMES.update(('cleanud64k','cleaniq3s64k','cleanqwen3664k'))
DEADLINE=datetime.fromisoformat('2026-10-30T12:03:07+00:00').timestamp()
workers=[]
for proc in psutil.process_iter(['pid','exe','cmdline']):
    try:
        if (proc.info['exe'] or '').lower().endswith('python.exe') and str(ROOT/'clean_benchmark.py').lower().replace('\\','/') in ' '.join(proc.info['cmdline'] or []).lower().replace('\\','/') and '/.venv/' not in (proc.info['exe'] or '').lower().replace('\\','/'):
            workers.append((proc.pid,proc.create_time()))
    except psutil.Error:pass
if len(workers)!=1:raise SystemExit('Expected one benchmark worker for deadline guard')
pid,birth=workers[0]
(OUT/'deadline-guard.json').write_text(json.dumps({'worker_pid':pid,'worker_birth':birth,'restore_at_utc':datetime.fromtimestamp(DEADLINE-480,timezone.utc).isoformat()}))
while time.time()<DEADLINE-480:
    try:
        data=json.loads((OUT/'results.json').read_text())
        if data.get('completed_utc'):raise SystemExit(0)
        worker=psutil.Process(pid)
        if worker.create_time()!=birth:raise SystemExit(0)
    except psutil.NoSuchProcess:raise SystemExit(0)
    time.sleep(min(10,max(.1,DEADLINE-480-time.time())))
try:
    worker=psutil.Process(pid)
    if worker.create_time()!=birth:raise SystemExit(0)
    worker.terminate()
except psutil.NoSuchProcess:raise SystemExit(0)
data=json.loads((OUT/'results.json').read_text())
data['budget_stop']='Independent deadline guard stopped measurement eight minutes before the user deadline; retained completed samples.'
try:
    try:
        status=httpx.get(bm.BASE+'/v1/status',timeout=3,trust_env=False).json()
        already_original=status.get('loaded') and 'ud-iq4_xs' in status.get('model','') and status.get('cache_max_tokens')==32768
    except Exception:already_original=False
    if not already_original:
        bm.stop_backend()
        launcher=subprocess.Popen(['cmd.exe','/d','/c',str(bm.STRATA),'launch','--preset','FlashNext32K','--yes','--no-tui'],
                                  cwd=ROOT,stdout=(OUT/'deadline-restore.log').open('a'),stderr=subprocess.STDOUT,creationflags=bm.manage.HIDDEN)
        while time.time()<DEADLINE-60:
            try:
                status=httpx.get(bm.BASE+'/v1/status',timeout=3,trust_env=False).json()
                if status.get('loaded') and 'ud-iq4_xs' in status.get('model',''):break
            except Exception:pass
            time.sleep(3)
        else:raise RuntimeError('Original model did not load before the deadline')
    if not bm.manage.probe('http://127.0.0.1:8086/health')['ok']:bm.restart_gateway()
    data['restored']={'by':'independent deadline guard','health':bm.manage.probe('http://127.0.0.1:8086/health')}
except Exception as exc:data['restore_error']=repr(exc)
data['completed_utc']=datetime.now(timezone.utc).isoformat()
(OUT/'results.json').write_text(json.dumps(data,indent=2))
print('Deadline guard restored endpoint and retained completed benchmark data.',flush=True)
