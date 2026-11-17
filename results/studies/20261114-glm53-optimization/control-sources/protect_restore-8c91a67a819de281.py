"""Disable only our redundant watchdog once the live coordinator is restoring."""
import json
from pathlib import Path
import psutil

root=Path(__file__).resolve().parent
folder=root/'benchmark-clean/20261030T100949Z'
data=json.loads((folder/'results.json').read_text())
events=data.get('events',[])
if not events or not events[-1]['message'].startswith('Restoring original FlashNext32K') or data.get('completed_utc'):
    raise SystemExit('Coordinator has not entered restoration; watchdog unchanged.')
workers=[];guards=[]
for proc in psutil.process_iter(['pid','exe','cmdline']):
    exe=(proc.info['exe'] or '').lower().replace('\\','/')
    command=' '.join(proc.info['cmdline'] or []).lower().replace('\\','/')
    if not exe.endswith('python.exe') or '/.venv/' in exe:continue
    if str(root/'clean_benchmark.py').lower().replace('\\','/') in command:workers.append(proc)
    if str(root/'clean_deadline_guard.py').lower().replace('\\','/') in command and str(folder).lower().replace('\\','/') in command:guards.append(proc)
if len(workers)!=1:raise SystemExit('No single live restoring coordinator; watchdog unchanged.')
for proc in guards:
    proc.terminate()
    print('Stopped redundant owned deadline guard',proc.pid,'while coordinator restores the original model.')
