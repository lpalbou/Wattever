import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import psutil

root=Path(__file__).resolve().parent
targets={}
for p in psutil.process_iter(['pid','name','exe','cmdline','ppid']):
    if (p.info['name'] or '').lower()=='codex.exe':
        args=p.info['cmdline'] or []
        role=next((s for s in args if s.startswith('--type=')),'main/native')
        targets[p.pid]=p.info
        print(json.dumps({'pid':p.pid,'role':role,'exe':p.info['exe'],'parent':p.info['ppid']}))
user32=ctypes.windll.user32
windows=[]
callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
@callback_type
def visit(hwnd,param):
    process_id=wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd,ctypes.byref(process_id))
    if process_id.value in targets and user32.IsWindowVisible(hwnd):
        windows.append({'hwnd':int(hwnd),'pid':process_id.value,'was_minimized':bool(user32.IsIconic(hwnd))})
    return True
user32.EnumWindows(visit,0)
print('visible Codex windows:',json.dumps(windows))
state=root/'benchmark-clean/codex-window-state.json'
if '--minimize' in sys.argv:
    state.write_text(json.dumps(windows,indent=2))
    for window in windows:
        if not window['was_minimized']:user32.ShowWindowAsync(wintypes.HWND(window['hwnd']),6)
    print('Minimized Codex windows; original state saved.')
elif '--restore' in sys.argv and state.exists():
    for window in json.loads(state.read_text()):
        if not window['was_minimized'] and user32.IsWindow(wintypes.HWND(window['hwnd'])):
            pid=wintypes.DWORD();user32.GetWindowThreadProcessId(wintypes.HWND(window['hwnd']),ctypes.byref(pid))
            if pid.value==window['pid']:user32.ShowWindowAsync(wintypes.HWND(window['hwnd']),9)
    print('Restored prior Codex window visibility.')
