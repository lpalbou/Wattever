"""Experimental GLM controls embedded in the Strata Control desktop process."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import threading
import time
import ctypes

from maya_control import API, MODEL, MayaController, PREPARED, probe, PORT, owned_records, prepared_profile, model_identity, model_label, MODEL_PROFILES
from legacy_window import LegacyWindowGuard
from manage import lock, AlreadyRunning


def open_panel(parent):
    try:
        return MayaPanel(parent)
    except AlreadyRunning:
        user = ctypes.windll.user32
        window = user.FindWindowW(None, 'Strata Control · GLM experimental')
        if window:
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
        return None


class MayaPanel:
    def __init__(self, parent):
        self.instance_lock = lock('desktop-maya-control')
        self.window = tk.Toplevel(parent)
        self.window.title('Strata Control · GLM experimental')
        self.window.geometry('640x680')
        self.window.minsize(610, 660)
        self.window.configure(bg='#0d141e')
        self.controller = MayaController()
        try:
            profile, _ = prepared_profile()
            self.model_id = model_identity(profile)
            title = 'GLM-5.3-Flash · ' + MODEL_PROFILES[self.model_id]['label']
        except (OSError, ValueError, KeyError, RuntimeError):
            self.model_id, title = MODEL, 'GLM-5.3 Flash · prepared Maya profile'
        self.legacy_guard = LegacyWindowGuard()
        self.guard_error = None
        self.closing = False
        self.stop_requested = False
        self.snapshot = None
        self.destroyed = threading.Event()
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        outer = tk.Frame(self.window, bg='#0d141e', padx=24, pady=22)
        outer.pack(fill='both', expand=True)
        tk.Label(outer, text=title, bg='#0d141e', fg='#f0f5fc',
                 font=('Segoe UI Semibold', 17)).pack(anchor='w')
        tk.Label(outer, text='Experimental Project Maya backend · 321B / 18B active',
                 bg='#0d141e', fg='#9aacc2').pack(anchor='w', pady=(5, 12))
        self.status = tk.Label(outer, text='Preparation pending', bg='#172231', fg='#55e2c4',
                               anchor='w', padx=12, pady=12, wraplength=480, justify='left')
        self.status.pack(fill='x')
        grid = tk.Frame(outer, bg='#0d141e')
        grid.pack(fill='x', pady=16)
        self.context = tk.StringVar(value='32768')
        self.gpus = tk.StringVar(value='auto')
        self.parallel = tk.StringVar(value='1')
        self.kv = tk.StringVar(value='int8')
        self.reasoning = tk.StringVar(value='low')
        self.split = tk.StringVar(value='auto')
        self.kv_placement = tk.StringVar(value='RAM + VRAM')
        self.ram_tier = tk.StringVar(value='auto')
        self.mtp = tk.BooleanVar(value=True)
        specs = [('Context', self.context, ('32768', '65536', '98304', '131072'), 0, 0),
                 ('GPUs', self.gpus, ('auto', '0,1', '1,0', '0', '1'), 0, 1),
                 ('Request slots', self.parallel, ('1', '2'), 0, 2),
                 ('KV cache', self.kv, ('int8', 'fp16'), 2, 0),
                 ('Reasoning', self.reasoning, ('none', 'low', 'high', 'max'), 2, 1),
                 ('Layer boundary', self.split, ('auto', '21', '22', '23', '24', '25', '26'), 2, 2),
                 ('KV placement', self.kv_placement, ('RAM + VRAM', 'VRAM only'), 4, 0),
                 ('Expert RAM (GiB / %)', self.ram_tier, ('auto', '0%', '25%', '50%', '75%', '90%', '99.9%'), 4, 1)]
        self.inputs = []
        self.editable_inputs = []
        for label, variable, values, row, col in specs:
            tk.Label(grid, text=label, bg='#0d141e', fg='#9aacc2').grid(row=row, column=col, sticky='w')
            editable = variable in (self.context, self.gpus, self.ram_tier, self.split)
            widget = ttk.Combobox(grid, textvariable=variable, values=values, state='normal' if editable else 'readonly', width=14)
            widget.grid(row=row + 1, column=col, sticky='ew', padx=(0, 10), pady=(5, 12))
            self.inputs.append(widget)
            if editable:
                self.editable_inputs.append(widget)
            grid.columnconfigure(col, weight=1)
        self.mtp_box = ttk.Checkbutton(outer, text='Load MTP head (effective decode policy is recorded)', variable=self.mtp)
        self.mtp_box.pack(anchor='w')
        tk.Label(outer, text='Plain RAM numbers are GiB; % uses fresh room after 12 GiB reserve. 0% keeps buffers/file cache. Low is recommended for answer quality; allow tokens for thinking and the answer.',
                 bg='#0d141e', fg='#9aacc2', wraplength=580, justify='left').pack(anchor='w', pady=(6, 0))
        buttons = tk.Frame(outer, bg='#0d141e')
        buttons.pack(fill='x', pady=16)
        self.load_button = ttk.Button(buttons, text='Load GLM', style='Primary.TButton', command=self.load)
        self.load_button.pack(side='left', expand=True, fill='x', padx=(0, 10))
        self.unload_button = ttk.Button(buttons, text='Unload GLM', command=self.unload)
        self.unload_button.pack(side='left', expand=True, fill='x')
        tk.Label(outer, text=API, bg='#0d141e', fg='#55e2c4', font=('Consolas', 11)).pack(anchor='w')
        ttk.Button(outer, text='Copy endpoint', command=self.copy).pack(anchor='w', pady=(8, 0))
        threading.Thread(target=self.monitor, daemon=True).start()
        self.window.after(200, self.tick)

    def monitor(self):
        while not self.destroyed.wait(2):
            proc = self.controller.model_process
            if proc is None or proc.poll() is not None:
                self.snapshot = None
                continue
            try:
                key = (getattr(self.controller, '_config', {}) or {}).get('api_key')
                identity = (getattr(self.controller, '_config', {}) or {}).get('model_name', self.model_id)
                self.snapshot = (time.monotonic(), probe(PORT, '/v1/status', api_key=key, expected_model=identity))
            except Exception:
                self.snapshot = None

    def copy(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(API)

    def load(self):
        try:
            args=(int(self.context.get()), self.mtp.get(), self.gpus.get(),int(self.parallel.get()),
                  self.kv.get(), self.reasoning.get(), self.split.get(),
                  {'RAM + VRAM': 'on', 'VRAM only': 'off'}[self.kv_placement.get()],self.ram_tier.get())
            self.guard_error = None
            self.legacy_guard.acquire()
            self.controller.submit('load', *args)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            self.guard_error = str(error)
            self.status.configure(text=str(error))

    def unload(self):
        self.stop_requested = True
        self.controller.cancel.set()

    def close(self):
        self.closing = True
        self.unload()

    def tick(self):
        busy = self.controller.operation
        alive = self.controller.model_process is not None and self.controller.model_process.poll() is None
        if self.legacy_guard.active and not busy and not alive and not self.controller.job:
            # Normal Unload clears children only after waiting for the owned
            # tree. A pre-spawn failure has no children at all. Unknown/failed
            # cleanup remains protected; no model or API is restored here.
            cleaned = not self.controller.error or not getattr(self.controller,'children',[])
            try:
                if cleaned and not owned_records():
                    self.legacy_guard.restore(verified_unloaded=True)
            except (ValueError, RuntimeError, OSError) as error:
                self.guard_error = str(error)
        if self.stop_requested and not busy:
            if self.controller.submit('unload'):
                self.stop_requested = False
                busy = 'unload'
        if self.closing and not busy:
            if alive or self.controller.job:
                self.controller.submit('unload')
            elif not self.controller.error and not self.legacy_guard.active:
                self.destroyed.set()
                self.instance_lock.close()
                self.window.destroy()
                return
        if self.controller.error:
            text = self.controller.error
            self.closing = False
        elif self.guard_error:
            text = self.guard_error
        elif busy:
            text = 'Loading GLM…' if busy == 'load' else 'Unloading owned GLM processes…'
        elif alive:
            stamp, checked = self.snapshot or (0, {})
            data = checked.get('data') or {}
            identity = (getattr(self.controller, '_config', {}) or {}).get('model_name', self.model_id)
            confirmed = (time.monotonic() - stamp < 6 and checked.get('state') == 'up'
                         and data.get('loaded') is True and data.get('model') == identity)
            if confirmed:
                cfg = getattr(self.controller, '_config', {}) or {}
                actual_order = (data.get('recorded_condition') or {}).get('gpu_order') or cfg.get('gpu', self.gpus.get())
                applied_ram = (data.get('engine_info') or {}).get('ram_gb')
                text = f'{identity} · GPUs {actual_order} · Context {self.context.get()}'
                if applied_ram is not None:
                    text += f'\nExpert RAM {applied_ram} GiB applied · request {self.ram_tier.get()}'
            else:
                text = 'Experimental server running · Engine readiness not confirmed'
        else:
            text = 'Prepared; load from this window' if PREPARED.exists() else 'Preparation pending · Model download/build not complete'
        self.status.configure(text=text)
        self.load_button.configure(state='normal' if not busy and not alive and PREPARED.exists() else 'disabled')
        self.unload_button.configure(state='normal' if alive or self.controller.job or busy else 'disabled')
        for widget in self.inputs:
            widget.configure(state=('normal' if widget in self.editable_inputs else 'readonly') if not busy and not alive else 'disabled')
        self.mtp_box.configure(state='normal' if not busy and not alive else 'disabled')
        self.window.after(400, self.tick)
