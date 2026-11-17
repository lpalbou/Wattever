"""UI-only handoff for this machine's pre-Maya native Control window.

No process or model lifecycle operations. A private operator receipt identifies
one exact old window; stale/reused identities never authorize a window change.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as w
import datetime
import json
import os
import time
from pathlib import Path
import psutil

ROOT=Path(__file__).resolve().parent
RECEIPT=ROOT/'experimental-maya/legacy-native-window.json'
EVENTS=ROOT/'experimental-maya/legacy-window-events.jsonl'
PENDING=ROOT/'experimental-maya/legacy-native-window-pending.json'


def snapshot(expected):
    user=ctypes.windll.user32
    user.GetWindowThreadProcessId.argtypes=[w.HWND,ctypes.POINTER(w.DWORD)]
    user.IsWindow.argtypes=[w.HWND]
    hwnd=int(expected['hwnd'])
    if not user.IsWindow(hwnd):return None
    pid=w.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if pid.value!=expected['pid']:return None
    try:
        proc=psutil.Process(pid.value)
        if proc.create_time()!=expected['birth'] or Path(proc.exe()).resolve()!=Path(expected['exe']).resolve():return None
        args=proc.cmdline()
        if '--experimental-maya' in args or str(ROOT/'strata_app.py') not in args:return None
    except psutil.NoSuchProcess:return None
    except psutil.AccessDenied as error:raise RuntimeError('Cannot verify the old native UI owner.') from error
    user.IsWindowEnabled.argtypes=[w.HWND]
    user.IsWindowVisible.argtypes=[w.HWND]
    return dict(expected,enabled=bool(user.IsWindowEnabled(hwnd)),visible=bool(user.IsWindowVisible(hwnd)))


def change_window(state, *, restore=False):
    if snapshot(state) is None:return
    user=ctypes.windll.user32
    user.EnableWindow.argtypes=[w.HWND,w.BOOL]
    user.ShowWindow.argtypes=[w.HWND,ctypes.c_int]
    hwnd=int(state['hwnd'])
    user.EnableWindow(hwnd,bool(state['enabled']) if restore else False)
    # Restore without stealing focus; a previously hidden window remains hidden.
    user.ShowWindow(hwnd,4 if restore and state['visible'] else 0)
    after=snapshot(state)
    if after is not None:
        desired_enabled=bool(state['enabled']) if restore else False
        desired_visible=bool(state['visible']) if restore else False
        if after['enabled']!=desired_enabled or after['visible']!=desired_visible:
            raise RuntimeError('Legacy UI enabled/visible transition was not confirmed.')


class LegacyWindowGuard:
    def __init__(self):
        self.saved=None
        if PENDING.exists():
            pending=json.loads(PENDING.read_text(encoding='utf-8'))
            if pending.get('schema')!='strata-legacy-native-window-pending-v1':raise RuntimeError('Invalid pending legacy UI state.')
            self.saved=pending['window']

    @property
    def active(self):return self.saved is not None

    def event(self, kind, state):
        EVENTS.parent.mkdir(parents=True,exist_ok=True)
        with EVENTS.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                event=kind,window=state,scope='Window enabled/visible state only; no process or model lifecycle'))+'\n')

    def acquire(self):
        if self.active:
            if not PENDING.exists() or json.loads(PENDING.read_text(encoding='utf-8')).get('window')!=self.saved:
                raise RuntimeError('Pending original legacy UI state changed; refusing a new handoff.')
            current=snapshot(self.saved)
            if current is not None and (current['enabled'] or current['visible']):
                self.event('reassert-disable-hide-preserving-original-state',self.saved)
                change_window(self.saved)
            return
        if not RECEIPT.exists():return
        expected=json.loads(RECEIPT.read_text(encoding='utf-8'))
        if expected.get('schema')!='strata-legacy-native-window-v1':raise RuntimeError('Invalid legacy UI receipt.')
        state=snapshot(expected)
        if state is None:return
        self.event('before-disable-hide',state)
        PENDING.parent.mkdir(parents=True,exist_ok=True)
        temp=PENDING.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(schema='strata-legacy-native-window-pending-v1',window=state)),encoding='utf-8')
        for attempt in range(8):
            try:os.replace(temp,PENDING);break
            except PermissionError:
                if attempt==7:raise
                time.sleep(.025*(attempt+1))
        self.saved=state
        change_window(state)

    def restore(self, *, verified_unloaded):
        if not self.active or not verified_unloaded:return False
        state=self.saved
        current=snapshot(state)
        if current is not None:
            change_window(state,restore=True)
            self.event('restored-after-verified-unload',state)
        else:
            self.event('stale-window-ignored-after-verified-unload',state)
        pending=json.loads(PENDING.read_text(encoding='utf-8'))
        if pending.get('window')!=state:raise RuntimeError('Pending legacy UI ownership changed; refusing to clear it.')
        PENDING.unlink()
        self.saved=None
        return True
