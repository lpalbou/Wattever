"""Strata Control: the single desktop entrypoint for this workspace."""
from __future__ import annotations

import ctypes
import json
import math
import subprocess
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

from manage import lock, AlreadyRunning
from strata_desktop import (
    Controller, ROOT, SETTINGS, REASONING, API, model_choices, observe,
    save_settings, load_settings, endpoint_urls,
)
import advanced_params
import control_widgets
import gpu_selection
import live_monitor
import model_downloads
import model_recommendations
import storage_volumes

BG, CARD, INK, MUTED, ACCENT = '#0b121c', '#141e2b', '#eef4fb', '#8fa3bb', '#4fe0c2'
TERM, LINE, WARN, CAUTION = '#080d14', '#243246', '#ff9b8a', '#ffd27a'
COLORS = {
    'card': CARD, 'ink': INK, 'muted': MUTED, 'accent': ACCENT,
    'trough': '#0c1520', 'warn': WARN, 'caution': CAUTION,
}
MAYA_REASONING = {'Off': 'none', 'Low': 'low', 'High': 'high', 'Max': 'max'}


class App:
    def __init__(self, root, controller=None, observer=observe, catalog=model_choices, monitor=True,
                 hardware=live_monitor.hardware_snapshot, metrics=live_monitor.fetch_metrics,
                 maya_controller=None):
        self.root, self.controller, self.observer = root, controller or Controller(), observer
        self.maya = maya_controller
        self.hardware_fn, self.metrics_fn = hardware, metrics
        self.snapshot, self.models, self.monitor_error = None, [], None
        self.hardware, self.metrics = None, None
        self.maya_snapshot = None
        self.catalog_done, self.catalog_error = False, None
        self._recommend_after_hardware = False
        self._hardware_seen = False
        self.closing, self.stop_requested, self.closed = False, False, threading.Event()
        self.shown_error = None
        self.advanced_vars = {}
        self.advanced_widgets = []
        self._advanced_traces = []
        self._tick_job = None
        self._disposed = False
        self._ui_thread = threading.get_ident()
        self.advanced_open = tk.BooleanVar(value=False)
        root.title('Strata Control')
        width = min(1260, root.winfo_screenwidth() - 80)
        height = min(900, root.winfo_screenheight() - 100)
        root.geometry(f'{width}x{height}')
        root.minsize(920, 700)
        root.configure(bg=BG)
        try:
            root.iconbitmap(str(ROOT / 'strata-control.ico'))
        except tk.TclError:
            pass
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind('<Destroy>', self._root_destroyed, add='+')
        self.style()
        outer = tk.Frame(root, bg=BG, padx=18, pady=14)
        outer.pack(fill='both', expand=True)
        self.build_header(outer)
        workspace = tk.Frame(outer, bg=BG)
        workspace.pack(fill='both', expand=True)
        workspace.columnconfigure(0, weight=1)
        workspace.rowconfigure(0, weight=1)
        main_shell = tk.Frame(workspace, bg=BG)
        main_shell.grid(row=0, column=0, sticky='nsew')
        self.main_canvas = tk.Canvas(main_shell, bg=BG, highlightthickness=0, bd=0)
        self.dashboard_scroll = ttk.Scrollbar(main_shell, orient='vertical', command=self.main_canvas.yview)
        self.dashboard_scroll.pack(side='right', fill='y')
        self.main_canvas.pack(side='left', fill='both', expand=True)
        self.main_canvas.configure(yscrollcommand=self.dashboard_scroll.set)
        self.main_column = tk.Frame(self.main_canvas, bg=BG)
        self._dashboard_window = self.main_canvas.create_window((0, 0), window=self.main_column, anchor='nw')
        self.main_canvas.bind('<Configure>', self.layout_dashboard)
        self.main_column.bind('<Configure>', self.layout_dashboard)
        root.bind('<MouseWheel>', self.scroll_dashboard, add='+')
        self.advanced_panel = tk.Frame(workspace, bg=LINE, width=360, padx=1, pady=1)
        # Keep the panel constructed but hidden until Advanced is opened.
        self.advanced_inner = tk.Frame(self.advanced_panel, bg='#101925', padx=14, pady=12)
        self.advanced_inner.pack(fill='both', expand=True)
        top = tk.Frame(self.main_column, bg=BG)
        self.top_cards = top
        top.pack(fill='x', pady=(0, 8))
        top.columnconfigure(0, weight=2)
        top.columnconfigure(1, weight=3)
        self.build_server(top)
        self.build_model(top)
        bottom = tk.Frame(self.main_column, bg=BG)
        bottom.pack(fill='both', expand=True, pady=(2, 0))
        bottom.columnconfigure(0, weight=1)
        bottom.rowconfigure(1, weight=1, minsize=150)
        self.build_hardware(bottom)
        self.build_requests(bottom)
        self.library_note = tk.Label(self.main_column, text='', bg=BG, fg=MUTED, font=('Segoe UI', 8),
                                     justify='left', wraplength=760, anchor='w')
        self.library_note.pack(fill='x', pady=(8, 0))
        self.status_strip = tk.Frame(self.main_column, bg='#101925', padx=12, pady=8)
        self.status_strip.pack(fill='x', pady=(0, 10), before=top)
        self.error_label = tk.Label(self.status_strip, text='', bg='#101925', fg=WARN, font=('Segoe UI', 9),
                                    justify='left', wraplength=900)
        self.error_label.pack(fill='x')
        self.progress = ttk.Progressbar(self.status_strip, mode='indeterminate',
                                        style='Accent.Horizontal.TProgressbar')
        self.progress_note = tk.Label(self.status_strip, text='', bg='#101925', fg=ACCENT,
                                      font=('Segoe UI Semibold', 9), justify='left', wraplength=900, anchor='w')
        footer = tk.Frame(self.main_column, bg=BG)
        footer.pack(fill='x', side='bottom', pady=(8, 0), before=bottom)
        self.button(footer, 'GLM lab', icon='◇', tip='Open the optional experimental GLM controls.',
                    command=self.open_maya, style='Quiet.TButton').pack(side='right')
        tk.Label(footer, text='Closing Control stops its server and model.', bg=BG, fg=MUTED, font=('Segoe UI', 8)).pack(side='left')
        self.storage = tk.StringVar(value='')
        self.nvme_retry_at = 0
        self.gpu_stamp = None
        self.gpu_retry_at = 0
        self.meters = []
        self._decode_bar_job = None
        self._decode_bar_t0 = None
        self._cache_mib_when_enabled = '1024'
        if self.maya is None:
            try:
                from maya_control import MayaController
                self.maya = MayaController()
            except Exception:
                self.maya = None
        try:
            saved = load_settings()
            self.reasoning.set(next(label for label, value in REASONING.items() if value == saved.get('reasoning_effort')))
            self.lan_access.set(bool(saved.get('lan_access')))
            self.vision.set(bool(saved.get('vision', False)))
            advanced_saved = saved.get('advanced') if isinstance(saved.get('advanced'), dict) else {}
            mib = str(advanced_saved.get('conversation_cache_mib', '0'))
            if mib and mib != '0':
                self._cache_mib_when_enabled = mib
            cache_on = saved.get('conversation_cache')
            if cache_on is None:
                cache_on = mib != '0'
            self.conversation_cache.set(bool(cache_on))
            self.restore_storage_choice(saved.get('model_storage'))
            if isinstance(saved.get('advanced'), dict):
                for key, value in saved['advanced'].items():
                    if key in self.advanced_vars:
                        self.advanced_vars[key].set(str(value))
        except (OSError, ValueError, StopIteration):
            pass
        self.refresh_gpu_choice()
        self.refresh_storage_options(force=True)
        self.refresh_api_label()
        self.rebuild_advanced()
        self.sync_conversation_cache_advanced()
        self.refresh_library_note()
        if monitor:
            threading.Thread(target=self.monitor, args=(catalog,), daemon=True).start()
        self._tick_job = root.after(100, self.tick)

    def build_header(self, outer):
        header = tk.Frame(outer, bg=BG)
        header.pack(fill='x', pady=(0, 12))
        mark = tk.Canvas(header, width=34, height=34, bg=BG, highlightthickness=0)
        mark.pack(side='left', padx=(0, 10))
        for y, color in [(21, '#1f6f78'), (14, '#34b5ad'), (8, ACCENT)]:
            mark.create_polygon(3, y, 17, y - 6, 31, y, 17, y + 6, fill=color, outline='')
        titles = tk.Frame(header, bg=BG)
        titles.pack(side='left')
        tk.Label(titles, text='Strata Control', bg=BG, fg=INK, font=('Segoe UI Semibold', 17)).pack(anchor='w')
        tk.Label(titles, text='Local AI, ready when you are', bg=BG, fg=MUTED,
                 font=('Segoe UI', 9)).pack(anchor='w')
        self.updated = tk.Label(header, text='Checking…', bg=BG, fg=MUTED, font=('Segoe UI', 9))
        self.updated.pack(side='right', anchor='s', pady=2)

    def button(self, parent, text, *, icon='', tip='', **kwargs):
        return control_widgets.ActionButton(parent, text, icon=icon, tip=tip, **kwargs)

    def layout_dashboard(self, event=None):
        width = max(1, self.main_canvas.winfo_width())
        height = max(self.main_column.winfo_reqheight(), self.main_canvas.winfo_height())
        self.main_canvas.itemconfigure(self._dashboard_window, width=width, height=height)
        self.main_canvas.configure(scrollregion=(0, 0, width, height))
        for name in ('error_label', 'progress_note', 'requests_note'):
            label = getattr(self, name, None)
            if label is not None:
                label.configure(wraplength=max(240, width - 40))

    def scroll_dashboard(self, event):
        widget = event.widget
        if widget.winfo_class() in ('Treeview', 'TCombobox', 'Text', 'TScrollbar'):
            return
        if str(widget).startswith(str(self.advanced_panel)):
            return
        if self.main_canvas.yview() != (0.0, 1.0):
            self.main_canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
            return 'break'

    def card(self, parent, title, row=0, column=0, padx=(0, 0), pady=(0, 0), expand=False):
        shell = tk.Frame(parent, bg=LINE, padx=1, pady=1)
        if expand:
            shell.grid(row=row, column=column, sticky='nsew', padx=padx, pady=pady)
        else:
            shell.grid(row=row, column=column, sticky='nsew', padx=padx, pady=pady)
        frame = tk.Frame(shell, bg=CARD, padx=14, pady=11)
        frame.pack(fill='both', expand=True)
        tk.Label(frame, text=title, bg=CARD, fg=MUTED, font=('Segoe UI Semibold', 8)).pack(anchor='w')
        body = tk.Frame(frame, bg=CARD)
        body.pack(fill='both', expand=True, pady=(8, 0))
        return body

    def build_server(self, parent):
        body = self.card(parent, 'SERVER', 0, 0, padx=(0, 6))
        status_row = tk.Frame(body, bg=CARD)
        status_row.pack(fill='x')
        self.status_pill = tk.Label(status_row, text='  Checking  ', bg='#243246', fg=MUTED,
                                    font=('Segoe UI Semibold', 9))
        self.status_pill.pack(side='left')
        self.status = tk.Label(status_row, text='Checking live status…', bg=CARD, fg=INK,
                               font=('Segoe UI Semibold', 13))
        self.status.pack(side='left', padx=(10, 0))
        self.detail = tk.Label(body, text='Inspecting the server and running processes.', bg=CARD, fg=MUTED,
                               font=('Segoe UI', 9), wraplength=380, justify='left')
        self.detail.pack(anchor='w', pady=(6, 10))
        row = tk.Frame(body, bg=CARD)
        row.pack(fill='x')
        self.start = self.button(row, 'Start', icon='▶', tip='Start the API server. A model is loaded separately.', style='Primary.TButton', command=lambda: self.run('start'))
        self.start.pack(side='left')
        self.stop = self.button(row, 'Stop', icon='■', tip='Stop the server and release the loaded model. Cancels a running load.', style='Stop.TButton', command=self.request_stop)
        self.stop.pack(side='left', padx=(8, 14))
        self.lan_access = tk.BooleanVar(value=False)
        self.lan_check = ttk.Checkbutton(row, text='LAN access', variable=self.lan_access,
                                         command=self.save_lan_access, style='Card.TCheckbutton')
        self.lan_check.pack(side='left')
        control_widgets.Tooltip(self.lan_check, 'Allow other devices on your network to use the API. Set before starting the server.')
        api_row = tk.Frame(body, bg='#101925', padx=10, pady=8)
        api_row.pack(fill='x', pady=(14, 0))
        tk.Label(api_row, text='API', bg='#101925', fg=MUTED, font=('Segoe UI Semibold', 8)).pack(anchor='w')
        self.api_urls = {'local': API, 'lan': None, 'advertised': API}
        self.local_url_var = tk.StringVar(value=API)
        self.lan_url_var = tk.StringVar(value='LAN off')
        local_line = tk.Frame(api_row, bg='#101925')
        local_line.pack(fill='x', pady=(4, 0))
        tk.Label(local_line, text='Local', bg='#101925', fg=MUTED, font=('Segoe UI Semibold', 8),
                 width=6, anchor='w').pack(side='left')
        self.api = tk.Label(local_line, textvariable=self.local_url_var, bg='#101925', fg=ACCENT,
                            font=('Consolas', 10), anchor='w')
        self.api.pack(side='left', fill='x', expand=True, padx=(4, 8))
        self.copy_local = self.button(local_line, 'Copy', icon='⧉', tip='Copy the API URL for apps on this computer.', width=7,
                                     command=lambda: self.copy_url('local'))
        self.copy_local.pack(side='right')
        lan_line = tk.Frame(api_row, bg='#101925')
        lan_line.pack(fill='x', pady=(4, 0))
        tk.Label(lan_line, text='LAN', bg='#101925', fg=MUTED, font=('Segoe UI Semibold', 8),
                 width=6, anchor='w').pack(side='left')
        self.lan_api = tk.Label(lan_line, textvariable=self.lan_url_var, bg='#101925', fg=ACCENT,
                                font=('Consolas', 10), anchor='w')
        self.lan_api.pack(side='left', fill='x', expand=True, padx=(4, 8))
        self.copy_lan = self.button(lan_line, 'Copy', icon='⧉', tip='Copy the API URL for other devices on your network.', width=7,
                                   command=lambda: self.copy_url('lan'))
        self.copy_lan.pack(side='right')
        self.lan_note = tk.Label(body, text='1  Start the server     2  Load a model     3  Connect your app',
                                 bg=CARD, fg=MUTED, font=('Segoe UI', 8), justify='left', wraplength=420)
        self.lan_note.pack(anchor='w', pady=(8, 0))

    def build_model(self, parent):
        body = self.card(parent, 'MODEL', 0, 1, padx=(6, 0))
        self.current = tk.Label(body, text='Loaded: none', bg=CARD, fg=INK, font=('Segoe UI', 9),
                                wraplength=420, justify='left')
        self.current.pack(anchor='w')
        grid = tk.Frame(body, bg=CARD)
        grid.pack(fill='x', pady=(10, 0))
        for col in range(3):
            grid.columnconfigure(col, weight=1)
        self.field_label(grid, 'MODEL', 0, 0)
        self.model = tk.StringVar()
        self.model_select = ttk.Combobox(grid, textvariable=self.model, state='readonly')
        self.model_select.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(4, 8))
        self.model_select.bind('<<ComboboxSelected>>', self.choose_model)
        self.field_label(grid, 'CONTEXT', 2, 0)
        self.field_label(grid, 'REASONING', 2, 1)
        self.field_label(grid, 'GPU', 2, 2)
        self.context = tk.StringVar(value='Auto')
        self.reasoning = tk.StringVar(value='Model default')
        self.mtp = tk.BooleanVar(value=True)
        self.conversation_cache = tk.BooleanVar(value=False)
        self.context_select = ttk.Combobox(grid, textvariable=self.context, state='normal')
        self.context_select.grid(row=3, column=0, sticky='ew', padx=(0, 6), pady=(4, 0))
        control_widgets.Tooltip(self.context_select,
            'Auto chooses the largest estimated context that fits the connected hardware, up to the model limit. '
            'Or type exact tokens such as 40960, or 40K (K = 1,024). A shorter context leaves more VRAM for experts.')
        self.reason_select = ttk.Combobox(grid, textvariable=self.reasoning, values=list(REASONING), state='readonly')
        self.reason_select.grid(row=3, column=1, sticky='ew', padx=(0, 6), pady=(4, 0))
        self.reason_select.bind('<<ComboboxSelected>>', self.save_reasoning)
        self.gpu_choice = tk.StringVar(value='Auto · all GPUs')
        self.gpu_options = {'Auto · all GPUs': {'mode': 'auto'}}
        self.gpu_select = ttk.Combobox(grid, textvariable=self.gpu_choice, state='readonly')
        self.gpu_select.grid(row=3, column=2, sticky='ew', pady=(4, 0))
        self.gpu_select.bind('<<ComboboxSelected>>', self.save_gpu_choice)
        control_widgets.Tooltip(self.gpu_select, 'Automatic uses supported connected GPUs. The hardware plan is rebuilt at each model load.')
        opts = tk.Frame(body, bg=CARD)
        opts.pack(fill='x', pady=(8, 0))
        self.mtp_check = ttk.Checkbutton(opts, text='Fast drafting', variable=self.mtp, style='Card.TCheckbutton')
        self.mtp_check.pack(side='left')
        control_widgets.Tooltip(self.mtp_check, 'MTP drafts several answer tokens ahead. Recommended on when the model supports it.')
        self.cache_check = ttk.Checkbutton(
            opts, text='Prefix cache', variable=self.conversation_cache, style='Card.TCheckbutton',
            command=self.save_conversation_cache,
        )
        self.cache_check.pack(side='left', padx=(14, 0))
        control_widgets.Tooltip(self.cache_check, 'Keep prompt prefixes for follow-up chats. Uses additional system RAM; off by default.')
        self.cache_note = tk.Label(opts, text='', bg=CARD, fg=MUTED, font=('Segoe UI', 8))
        self.cache_note.pack(side='left', padx=(8, 0))
        # Images need the model family's encoder file; the button fetches what is missing.
        self.vision = tk.BooleanVar(value=False)
        self.vision_check = ttk.Checkbutton(opts, text='Images', variable=self.vision, style='Card.TCheckbutton',
                                            command=self.save_vision)
        self.vision_check.pack(side='left', padx=(14, 0))
        control_widgets.Tooltip(self.vision_check, 'Enable image inputs when this model’s image encoder is installed. The encoder runs on the CPU.')
        self.vision_download = self.button(opts, 'Get images', icon='↓', tip='Download the image encoders. Downloads do not start a model.', command=self.download_assets)
        self.vision_note = tk.Label(opts, text='', bg=CARD, fg=MUTED, font=('Segoe UI', 8))
        self.vision_note.pack(side='left', padx=(8, 0))
        actions = tk.Frame(body, bg=CARD)
        actions.pack(fill='x', pady=(12, 0))
        self.load = self.button(actions, 'Load', icon='↑', tip='Configure the selected model for your hardware and load it.', style='Primary.TButton', command=self.load_model)
        self.load.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.unload = self.button(actions, 'Eject', icon='⏏', tip='Unload the model and free its memory. Keep the API server running.', command=self.eject_model)
        self.unload.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.advanced_button = self.button(actions, 'Tune', icon='⚙', tip='Open advanced memory and speed settings. Auto is recommended to start.', command=self.toggle_advanced)
        self.advanced_button.pack(side='left')
        self.field_label(body, 'STORAGE / NVMe', 0, 0, pack=True)
        storage_row = tk.Frame(body, bg=CARD)
        storage_row.pack(fill='x', pady=(4, 0))
        self.storage_choice = tk.StringVar(value='Source models folder (default)')
        self.storage_options = {'Source models folder (default)': {'mode': 'default'}}
        self.storage_select = ttk.Combobox(storage_row, textvariable=self.storage_choice, state='readonly')
        self.storage_select.pack(side='left', fill='x', expand=True, padx=(0, 8))
        self.storage_select.bind('<<ComboboxSelected>>', self.save_storage_choice)
        self.storage_browse = self.button(storage_row, 'Browse', icon='…', tip='Choose a folder containing the verified model package.', command=self.choose_storage)
        self.storage_browse.pack(side='right')
        self.storage_note = tk.Label(body, text='Detecting NVMe…')
        self.limit_note = tk.Label(body, text='Discovering prepared models…')
        self.recommend_note = tk.Label(body, text='')
        self.model_meta = tk.Label(body, text='Detecting NVMe…', bg=CARD, fg=MUTED,
                                   font=('Segoe UI', 8), justify='left', wraplength=480, anchor='w')
        self.model_meta.pack(anchor='w', pady=(6, 0))
        self.model_rec = tk.Label(body, text='', bg=CARD, fg=ACCENT,
                                  font=('Segoe UI', 8), justify='left', wraplength=480, anchor='w')
        self.model_rec.pack(anchor='w', pady=(2, 0))

    def build_hardware(self, parent):
        body = self.card(parent, 'MEMORY & DEVICES', 0, 0, pady=(0, 8), expand=True)
        self.hardware_host = tk.Frame(body, bg=CARD)
        self.hardware_host.pack(fill='both', expand=True)
        self.hardware_view = self.hardware_host  # tests can still find the panel host
        self.meters = []

    def build_requests(self, parent):
        body = self.card(parent, 'REQUEST HISTORY', 1, 0, expand=True)
        bar = tk.Frame(body, bg=CARD)
        bar.pack(fill='x', pady=(0, 6))
        for label, color in [('TOKENS', MUTED), ('PREFILL  ·  reading the prompt', '#83c5fc'),
                             ('DECODE  ·  writing the answer', ACCENT)]:
            tk.Label(bar, text=label, bg=CARD, fg=color, font=('Segoe UI Semibold', 8)).pack(side='left', padx=(0, 18))
        self.button(bar, 'Copy', icon='⧉', tip='Copy the complete request table as tab-separated values for a spreadsheet.',
                    command=self.copy_requests, style='Quiet.TButton').pack(side='right')
        table = tk.Frame(body, bg=TERM)
        table.pack(fill='both', expand=True)
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = [str(index) for index in range(len(live_monitor.REQUEST_COLUMNS))]
        self.requests_view = ttk.Treeview(table, columns=columns, show='headings', selectmode='browse',
                                         height=5, style='Requests.Treeview')
        self.requests_view.grid(row=0, column=0, sticky='nsew')
        self.request_columns = tuple(live_monitor.REQUEST_COLUMNS)
        widths = [82, 175, 95, 95, 95, 110, 110, 110, 110, 105]
        for index, label in enumerate(self.request_columns):
            title = {'End': 'Condition', 'Prefill s': 'Prefill time (s)', 'Decode s': 'Decode time (s)',
                     'Total s': 'Wall time (s)', 'Wall s': 'Wall time (s)'}.get(label, label)
            self.requests_view.heading(str(index), text=title, anchor='w' if index < 2 else 'e')
            self.requests_view.column(str(index), width=widths[index], minwidth=175 if index == 1 else 70,
                                      anchor='w' if index < 2 else 'e', stretch=False)
        self.requests_scroll_x = ttk.Scrollbar(table, orient='horizontal', command=self.requests_view.xview)
        self.requests_scroll_x.grid(row=1, column=0, sticky='ew')
        self.requests_scroll_y = ttk.Scrollbar(table, orient='vertical', command=self.requests_view.yview)
        self.requests_scroll_y.grid(row=0, column=1, sticky='ns')
        self.requests_view.configure(xscrollcommand=self.requests_scroll_x.set, yscrollcommand=self.requests_scroll_y.set)
        self.requests_view.tag_configure('even', background=TERM)
        self.requests_view.tag_configure('odd', background='#101b29')
        self.requests_note = tk.Label(body, text='Load a model to see its completed requests.',
                                     bg=CARD, fg=MUTED, font=('Segoe UI', 8), anchor='w', justify='left')
        self.requests_note.pack(fill='x', pady=(6, 0))
        self.requests_view.tooltip = control_widgets.Tooltip(self.requests_view,
            'Prompt: total input. Cached: reused prefix. Prefill: processing newly read tokens.\n'
            'Decode: generating tokens. Condition states the wall timing scope. Drag headings to resize; scroll sideways for every metric.')
        self._requests_stamp = None

    def field_label(self, parent, text, row, col, pack=False):
        label = tk.Label(parent, text=text, bg=CARD, fg=MUTED, font=('Segoe UI Semibold', 8))
        if pack:
            label.pack(anchor='w', pady=(10, 0))
        else:
            label.grid(row=row, column=col, sticky='w')

    def refresh_model_meta(self):
        """One compact footer instead of three stacked note labels."""
        storage = (self.storage_note.cget('text') or '').strip()
        limits = (self.limit_note.cget('text') or '').strip()
        recommend = (self.recommend_note.cget('text') or '').strip()
        lines = []
        if storage and storage != 'Detecting NVMe…':
            lines.append(storage)
        if limits and limits != 'Discovering prepared models…':
            lines.append(limits)
        full = ' · '.join(lines) if lines else limits or storage or '…'
        choice = self.selected_choice() or {}
        if choice.get('engine') != 'maya' and choice.get('max_context'):
            full = (f"Context limit {choice['max_context']:,} · "
                    + ('Multi-GPU' if choice.get('multi_gpu') else 'GPU selected at load')
                    + (' · NVMe available' if any(value.get('mode') == 'nvme' for value in getattr(self, 'storage_options', {}).values()) else ''))
        self.model_meta.configure(text=full)
        self.model_rec.configure(text='Auto adjusts context and memory to your hardware.'
                                 if recommend and choice.get('engine') != 'maya' else recommend)

    def sync_progress_strip(self, busy, note, phase=None):
        """Single progress line; hide the strip when idle."""
        if busy or self.error_label.cget('text'):
            if not self.status_strip.winfo_manager():
                self.status_strip.pack(fill='x', pady=(0, 10), before=self.top_cards)
        else:
            self.status_strip.pack_forget()
        if busy and note:
            if not self.progress.winfo_manager():
                self.progress.pack(fill='x', pady=(4, 0), after=self.error_label)
            if not self.progress_note.winfo_manager():
                self.progress_note.pack(fill='x', pady=(4, 0))
            headline = f'{phase} — ' if phase else ''
            self.progress_note.configure(text=headline + note)
        else:
            self.progress.pack_forget()
            self.progress_note.pack_forget()
            self.progress_note.configure(text='')

    def live_engine_banner(self):
        if (self.metrics or {}).get('state') != 'up':
            return None, None, None
        data = (self.metrics or {}).get('data') or {}
        live = data.get('live') or {}
        phase, detail = live_monitor.format_live_request_banner(live, data.get('engine'))
        return phase, detail, live

    def stop_decode_bar_animation(self):
        if self._decode_bar_job is not None:
            try:
                self.root.after_cancel(self._decode_bar_job)
            except tk.TclError:
                pass
            self._decode_bar_job = None
        self._decode_bar_t0 = None

    def _step_decode_bar_animation(self):
        self._decode_bar_job = None
        if self._decode_bar_t0 is None:
            return
        elapsed = time.monotonic() - self._decode_bar_t0
        # Slow sine sweep: readable motion without the harsh ttk indeterminate strobe.
        period = 3.4
        wave = 0.5 + 0.5 * math.sin(2 * math.pi * elapsed / period)
        value = int(1000 * (0.14 + 0.68 * wave))
        self.progress.configure(mode='determinate', maximum=1000, value=value)
        self._decode_bar_job = self.root.after(45, self._step_decode_bar_animation)

    def start_decode_bar_animation(self):
        if self._decode_bar_t0 is None:
            self._decode_bar_t0 = time.monotonic()
        if self._decode_bar_job is None:
            self._step_decode_bar_animation()

    def refresh_progress_bar(self, *, operation_busy, progress, live=None):
        """Determinate for bytes/prefill/capped decode; soft sine bar for open-ended decode."""
        self.progress.stop()
        self.stop_decode_bar_animation()
        total = progress.get('bytes_total') or 0
        done = progress.get('bytes_done') or 0
        if operation_busy and total > 0 and progress.get('phase') in ('staging', 'downloading'):
            self.progress.configure(mode='determinate', maximum=max(total, 1), value=min(done, total))
            return
        live = live or {}
        state = live.get('state')
        frac = live_monitor.live_request_fraction(live) if live else None
        if frac is not None:
            self.progress.configure(mode='determinate', maximum=1000, value=max(0, min(1000, int(round(frac * 1000)))))
            return
        if state == 'generating':
            self.start_decode_bar_animation()
            return
        if operation_busy:
            if str(self.progress.cget('mode')) != 'indeterminate':
                self.progress.configure(mode='indeterminate')
            self.progress.start(8)
            return
        if str(self.progress.cget('mode')) != 'indeterminate':
            self.progress.configure(mode='indeterminate')

    def style(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background=BG, foreground=INK)
        style.configure('TButton', padding=(11, 7), background='#26364b', borderwidth=1, foreground=INK,
                        bordercolor='#34475e', lightcolor='#34475e', darkcolor='#34475e',
                        focuscolor=ACCENT, focusthickness=1, relief='flat')
        style.map('TButton', background=[('disabled', '#172331'), ('pressed', '#52799a'), ('selected', '#417b83'), ('active', '#3a5673')],
                  foreground=[('disabled', '#65768c')], bordercolor=[('focus', ACCENT), ('active', '#658bab')],
                  lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)],
                  relief=[('pressed', 'sunken'), ('!pressed', 'flat')])
        style.configure('Primary.TButton', background=ACCENT, foreground='#092323', font=('Segoe UI Semibold', 10))
        style.map('Primary.TButton', background=[('disabled', '#263d43'), ('pressed', '#22b99d'), ('selected', '#c1ffea'), ('active', '#91f5dd')],
                  foreground=[('disabled', '#7b9b9e')], bordercolor=[('focus', '#d8fff5'), ('active', '#baffee')])
        style.configure('Stop.TButton', foreground='#ffc2b8', background='#422f38', bordercolor='#60424c')
        style.map('Stop.TButton', background=[('disabled', '#1b2737'), ('pressed', '#99505b'), ('selected', '#bb6471'), ('active', '#71424f')],
                  foreground=[('disabled', '#65768c')])
        style.configure('Quiet.TButton', padding=(8, 5), background='#1b2a3c', foreground=MUTED)
        style.map('Quiet.TButton', background=[('disabled', '#172331'), ('pressed', '#52799a'), ('selected', '#417b83'), ('active', '#344c66')],
                  foreground=[('disabled', '#65768c'), ('selected', INK), ('active', INK)])
        style.configure('TCombobox', fieldbackground=CARD, background='#26364b', foreground=INK,
                        arrowcolor=MUTED, bordercolor='#33465d', lightcolor='#33465d', darkcolor='#33465d', padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', '#121c29'), ('disabled', '#111c29')],
                  foreground=[('readonly', INK), ('disabled', '#65768c')],
                  selectbackground=[('readonly', '#121c29')], bordercolor=[('focus', ACCENT), ('active', '#658bab')],
                  lightcolor=[('focus', ACCENT)], darkcolor=[('focus', ACCENT)])
        self.root.option_add('*TCombobox*Listbox.background', CARD)
        self.root.option_add('*TCombobox*Listbox.foreground', INK)
        self.root.option_add('*TCombobox*Listbox.selectBackground', '#284e55')
        style.configure('TCheckbutton', background=BG, foreground=INK, padding=4)
        style.map('TCheckbutton', background=[('pressed', '#244d55'), ('active', '#1c3044')],
                  foreground=[('disabled', '#65768c'), ('active', '#ffffff')],
                  indicatorbackground=[('selected', ACCENT), ('active', '#477693')])
        style.configure('Card.TCheckbutton', background=CARD, foreground=INK, padding=4)
        style.map('Card.TCheckbutton', background=[('pressed', '#244d55'), ('active', '#23384e')],
                  foreground=[('disabled', '#65768c'), ('active', '#ffffff')],
                  indicatorbackground=[('selected', ACCENT), ('active', '#477693')])
        style.configure('Requests.Treeview', background=TERM, fieldbackground=TERM, foreground=INK,
                        borderwidth=0, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                        rowheight=29, font=('Consolas', 9))
        style.map('Requests.Treeview', background=[('selected', '#24525a')], foreground=[('selected', '#effffb')])
        style.configure('Requests.Treeview.Heading', font=('Segoe UI Semibold', 8), background='#213247',
                        foreground='#bdd0e4', padding=(8, 9), relief='flat')
        style.map('Requests.Treeview.Heading', background=[('pressed', '#496e8a'), ('active', '#304c67')])
        style.configure('TEntry', fieldbackground='#121c29', foreground=INK, insertcolor=INK, padding=6)
        style.configure('TScrollbar', background='#324a61', troughcolor=TERM, arrowcolor=MUTED, borderwidth=0)
        style.map('TScrollbar', background=[('pressed', '#6fa7c2'), ('active', '#4f7794')])
        style.configure('Accent.Horizontal.TProgressbar', background=ACCENT, troughcolor='#0a1018',
                        borderwidth=0, thickness=4)

    def monitor(self, catalog):
        try:
            self.models = catalog()
        except Exception as exc:
            self.catalog_error = str(exc)
        self.catalog_done = True
        while not self.closed.is_set():
            try:
                self.snapshot = self.observer()
                self.monitor_error = None
            except Exception as exc:
                self.monitor_error = str(exc)
            try:
                self.hardware = self.hardware_fn()
            except Exception as exc:
                self.hardware = {'ram': {}, 'gpus': [], 'error': str(exc),
                                 'checked': time.time(), 'monotonic': time.monotonic()}
            backend = (self.snapshot or {}).get('backend') or {}
            maya_alive = (self.maya is not None and self.maya.model_process is not None
                          and self.maya.model_process.poll() is None)
            if maya_alive:
                try:
                    from maya_control import probe as maya_probe
                    key = (getattr(self.maya, '_config', {}) or {}).get('api_key')
                    self.maya_snapshot = maya_probe(api_key=key)
                except Exception as exc:
                    self.maya_snapshot = {'state': 'unknown', 'error': str(exc)}
                try:
                    self.metrics = live_monitor.fetch_metrics(port=8097)
                except Exception as exc:
                    self.metrics = {'state': 'unknown', 'data': None, 'error': str(exc),
                                    'checked': time.time(), 'monotonic': time.monotonic()}
            elif backend.get('state') == 'up':
                self.maya_snapshot = None
                try:
                    self.metrics = self.metrics_fn()
                except Exception as exc:
                    self.metrics = {'state': 'unknown', 'data': None, 'error': str(exc),
                                    'checked': time.time(), 'monotonic': time.monotonic()}
            else:
                self.maya_snapshot = None
                self.metrics = {'state': 'absent', 'data': None, 'checked': time.time(),
                                'monotonic': time.monotonic()}
            self.closed.wait(1)

    def selected_choice(self):
        return next((m for m in self.models if m['label'] == self.model.get()), None)

    def choose_model(self, event=None):
        choice = self.selected_choice()
        if not choice:
            return
        maximum = choice['max_context']
        if choice.get('engine') != 'maya':
            saved = load_settings()
            self.reason_select.configure(values=list(REASONING))
            self.reasoning.set(next((label for label, value in REASONING.items()
                                     if value == saved.get('reasoning_effort')), 'Model default'))
        else:
            self.reason_select.configure(values=list(MAYA_REASONING))
        if choice.get('engine') == 'maya':
            values = ['Auto', 'Max'] + [str(size) for size in (32768, 65536, 98304, 131072, 262144, 524288, 1048576) if size <= maximum]
            self.context_select.configure(values=values)
            note = 'GLM uses the Project Maya backend · unload native Strata first'
            if choice.get('released') is False:
                note += ' · release gate not enabled'
            self.storage_note.configure(text='GLM uses its prepared pack path (NVMe staging is for native Qwen packs)')
            self.refresh_model_meta()
        else:
            values = ['Auto', 'Max'] + [f'{size // 1024}K' for size in (8192, 16384, 32768, 65536, 98304, 131072, 163840, 204800, 262144)
                                if size <= maximum]
            self.context_select.configure(values=values)
            gpu_note = 'Multi-GPU capable' if choice.get('multi_gpu') else 'Single-GPU engine'
            note = f'Max context {maximum:,} · {gpu_note}'
        self.limit_note.configure(text=note)
        self.refresh_model_meta()
        self.rebuild_advanced()
        self.apply_recommendations(preserve_settings=True)
        # First hardware sample can refine context/RAM-tier after model change.
        self._recommend_after_hardware = True
        self.refresh_api_label()
        self.refresh_library_note()

    def apply_recommendations(self, preserve_settings=False):
        choice = self.selected_choice()
        if not choice:
            return
        rec = model_recommendations.recommend(choice, self.hardware)
        try:
            saved = load_settings()
        except (OSError, ValueError):
            saved = {}
        preferences = ((saved.get('model_preferences') or {}).get(choice['id']) or {}) if preserve_settings else {}
        values = list(self.context_select.cget('values') or ())
        context = preferences.get('context', rec['context'])
        if isinstance(context, int):
            context = f'{context // 1024}K' if context % 1024 == 0 else str(context)
        if context in values:
            self.context.set(context)
        elif values:
            self.context.set(values[-1])
        self.mtp.set(bool(preferences.get('mtp', rec.get('mtp', True))))
        if choice.get('engine') != 'maya' and rec.get('reasoning_label') in REASONING and not (preserve_settings and 'reasoning_effort' in saved):
            self.reasoning.set(rec['reasoning_label'])
        for key, value in (rec.get('advanced') or {}).items():
            if key in self.advanced_vars and not preserve_settings:
                self.advanced_vars[key].set(str(value))
        if choice.get('engine') == 'maya':
            self.sync_maya_reasoning()
        if not preserve_settings and choice.get('engine') != 'maya':
            self.conversation_cache.set(False)
        if not self.conversation_cache.get():
            if 'conversation_cache_mib' in self.advanced_vars:
                self._cache_mib_when_enabled = self.advanced_vars['conversation_cache_mib'].get()
                self.advanced_vars['conversation_cache_mib'].set('0')
        else:
            self.sync_conversation_cache_advanced()
        self.recommend_note.configure(text=rec.get('summary') or '')
        self.refresh_model_meta()

    def refresh_library_note(self):
        try:
            rows = model_recommendations.library_status()
        except Exception as exc:
            self.library_note.configure(text='Library status unavailable: ' + str(exc))
            return
        bits = []
        for row in rows:
            if row['state'] == 'not found' and row['id'] == 'deepseek-v4.1-flash':
                bits.append('DeepSeek V4.1 Flash: not downloaded (no active transfer)')
            elif row['state'] == 'incomplete download':
                bits.append(f"{row['title']}: downloading/incomplete")
            elif row['state'] == 'stalled download':
                bits.append(f"{row['title']}: stalled incomplete download")
            elif row['state'] == 'present' and 'Coder' in row['title']:
                bits.append(f"{row['title']}: on disk, not packed for Control yet")
        self.library_note.configure(text=f'Library · {len(bits)} other model packages · hover for details' if bits else '')
        if not hasattr(self.library_note, 'tooltip'):
            self.library_note.tooltip = control_widgets.Tooltip(self.library_note, '')
        self.library_note.tooltip.text = '\n'.join(bits)

    def toggle_advanced(self):
        self.advanced_open.set(not self.advanced_open.get())
        if self.advanced_open.get():
            self.rebuild_advanced()
            self.advanced_panel.grid(row=0, column=1, sticky='nsew', padx=(12, 0))
            self.advanced_panel.grid_propagate(False)
            self.advanced_panel.configure(width=330)
            self.advanced_button.set_label('Tune', '×')
            self.advanced_button.tooltip.text = 'Close advanced settings. Your selections stay available.'
            self._closed_drawer_geometry = self.root.geometry()
            width = min(self.root.winfo_screenwidth() - 70, max(920, self.root.winfo_width()) + 342)
            self.root.geometry(f'{width}x{max(700, self.root.winfo_height())}')
        else:
            self.advanced_panel.grid_forget()
            self.advanced_button.set_label('Tune', '⚙')
            self.advanced_button.tooltip.text = 'Open advanced memory and speed settings. Auto is recommended to start.'
            if getattr(self, '_closed_drawer_geometry', None):
                self.root.geometry(self._closed_drawer_geometry)

    def rebuild_advanced(self):
        for child in self.advanced_inner.winfo_children():
            child.destroy()
        choice = self.selected_choice() or {}
        engine = choice.get('engine') or 'strata'
        defs = advanced_params.definitions_for('maya' if engine == 'maya' else choice.get('family', 'strata'))
        previous = {key: var.get() for key, var in self.advanced_vars.items()}
        self.clear_advanced_traces()
        control_widgets.release_variables(self.advanced_vars.values())
        try:
            settings = load_settings()
            saved = settings.get('advanced') or {}
        except (OSError, ValueError):
            settings = {}
            saved = {}
        self.advanced_vars = {}
        self.advanced_widgets = []
        head = tk.Frame(self.advanced_inner, bg='#101925')
        head.pack(fill='x')
        tk.Label(head, text='Advanced', bg='#101925', fg=INK,
                 font=('Segoe UI Semibold', 12)).pack(side='left')
        self.button(head, 'Close', icon='×', tip='Close advanced settings. Your selections stay available.', command=self.toggle_advanced,
                    style='Quiet.TButton').pack(side='right')
        tk.Label(self.advanced_inner,
                 text=choice.get('label') or 'Select a model',
                 bg='#101925', fg=ACCENT, font=('Segoe UI', 9), wraplength=300, justify='left').pack(anchor='w', pady=(8, 4))
        tk.Label(self.advanced_inner,
                 text='Applies at the next model load. Start with Auto; adjust one setting at a time.',
                 bg='#101925', fg=MUTED, font=('Segoe UI', 8), wraplength=300, justify='left').pack(anchor='w', pady=(0, 8))
        # Scrollable body so the side panel never hides the main dashboard.
        body_shell = tk.Frame(self.advanced_inner, bg='#101925')
        body_shell.pack(fill='both', expand=True)
        canvas = tk.Canvas(body_shell, bg='#101925', highlightthickness=0, bd=0)
        scroll = ttk.Scrollbar(body_shell, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        body = tk.Frame(canvas, bg='#101925')
        window_id = canvas.create_window((0, 0), window=body, anchor='nw')

        def _sync_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox('all'))
            canvas.itemconfigure(window_id, width=canvas.winfo_width())

        body.bind('<Configure>', _sync_scroll)
        canvas.bind('<Configure>', _sync_scroll)

        def _wheel(event):
            delta = -1 if event.delta > 0 else 1
            if event.delta == 0 and getattr(event, 'num', None) in (4, 5):
                delta = -1 if event.num == 4 else 1
            canvas.yview_scroll(delta, 'units')

        canvas.bind('<MouseWheel>', _wheel)
        self.ram_preview = None
        for item in defs:
            if engine == 'maya' and item['key'] == 'reasoning':
                value = previous.get('reasoning', settings.get('maya_reasoning_effort', saved.get('reasoning', 'low')))
                self.advanced_vars['reasoning'] = tk.StringVar(value=value if value in MAYA_REASONING.values() else 'low')
                continue
            cell = tk.Frame(body, bg='#101925')
            cell.pack(fill='x', pady=(0, 10))
            tk.Label(cell, text=item['label'], bg='#101925', fg=MUTED,
                     font=('Segoe UI Semibold', 8)).pack(anchor='w')
            variable = tk.StringVar(value=previous.get(item['key'], str(saved.get(item['key'], item['default']))))
            self.advanced_vars[item['key']] = variable
            editable = bool(item.get('editable'))
            widget = ttk.Combobox(cell, textvariable=variable, values=item['values'], state='normal' if editable else 'readonly')
            widget._editable = editable
            if item['key'] == 'ram_use_percent':
                widget.configure(validate='key', validatecommand=(self.root.register(control_widgets.valid_ram_edit), '%P'))
            widget.pack(fill='x', pady=(4, 3))
            self.advanced_widgets.append(widget)
            tk.Label(cell, text=item['effect'], bg='#101925', fg='#7f93ab', font=('Segoe UI', 8),
                     wraplength=300, justify='left').pack(anchor='w')
            control_widgets.Tooltip(widget, item['effect'])
            widget.bind('<MouseWheel>', _wheel, add='+')
            if item['key'] == 'ram_use_percent':
                self.ram_preview = tk.Label(cell, text='', bg='#162b35', fg=ACCENT, font=('Segoe UI', 8),
                                            wraplength=278, justify='left', padx=8, pady=7, anchor='w')
                self.ram_preview.pack(fill='x', pady=(6, 0))
                trace = variable.trace_add('write', lambda *_: self.refresh_ram_preview())
                self._advanced_traces.append((variable, 'write', trace))
        self.sync_conversation_cache_advanced()
        if engine == 'maya':
            self.sync_maya_reasoning()
        self.button(self.advanced_inner, 'Reset to Auto', icon='↺', tip='Restore the recommended settings for this model.',
                    command=self.apply_recommendations).pack(fill='x', pady=(8, 0))
        self.refresh_ram_preview()

    def sync_maya_reasoning(self):
        value = self.advanced_vars['reasoning'].get()
        self.reasoning.set(next(label for label, level in MAYA_REASONING.items() if level == value))

    def save_reasoning(self, event=None):
        try:
            if (self.selected_choice() or {}).get('engine') == 'maya':
                value = MAYA_REASONING[self.reasoning.get()]
                save_settings({'maya_reasoning_effort': value})
                self.advanced_vars['reasoning'].set(value)
            else:
                save_settings({'reasoning_effort': REASONING[self.reasoning.get()]})
            self.error_label.configure(text='')
            return True
        except (OSError, ValueError, KeyError) as exc:
            self.error_label.configure(text='Could not save reasoning: ' + str(exc))
            return False

    def refresh_api_label(self):
        choice = self.selected_choice() or {}
        settings = {'lan_access': bool(self.lan_access.get())}
        port = 8097 if choice.get('engine') == 'maya' else 8086
        urls = endpoint_urls(settings, port=port)
        self.api_urls = urls
        self.local_url_var.set(urls['local'])
        if urls['lan']:
            self.lan_url_var.set(urls['lan'])
            self.copy_lan.configure(state='normal')
        else:
            self.lan_url_var.set('LAN off')
            self.copy_lan.configure(state='disabled')
        if choice.get('engine') == 'maya':
            if settings['lan_access']:
                self.lan_note.configure(
                    text='GLM binds 0.0.0.0:8097 when LAN is on · reload the model after changing LAN.')
            else:
                self.lan_note.configure(text='GLM endpoint is localhost:8097 · enable LAN before Load for remote access.')
            return
        if settings['lan_access']:
            self.lan_note.configure(text='LAN on · Windows Firewall may prompt on first Start/Load.')
        else:
            self.lan_note.configure(text='1  Start the API     2  Load a model     3  Copy the URL')

    def save_lan_access(self):
        try:
            save_settings({'lan_access': bool(self.lan_access.get())})
            self.refresh_api_label()
            self.error_label.configure(text='')
            return True
        except (OSError, ValueError) as exc:
            self.error_label.configure(text='Could not save network setting: ' + str(exc))
            return False

    def restore_storage_choice(self, storage):
        if not isinstance(storage, dict):
            self.storage_choice.set('Source models folder (default)')
            return
        mode = storage.get('mode')
        if mode == 'nvme' and storage.get('letter'):
            letter = str(storage['letter']).upper()
            label = next((name for name, value in self.storage_options.items()
                          if value.get('mode') == 'nvme' and value.get('letter') == letter), None)
            if label:
                self.storage_choice.set(label)
                return
            pending = f'NVMe {letter}: (waiting for detection)'
            self.storage_options[pending] = {'mode': 'nvme', 'letter': letter}
            self.storage_choice.set(pending)
            return
        if mode == 'custom' and storage.get('path'):
            path = str(storage['path'])
            label = f'Custom: {path}'
            self.storage_options[label] = {'mode': 'custom', 'path': path}
            self.storage_choice.set(label)
            self.storage.set(path)
            return
        self.storage_choice.set('Source models folder (default)')

    def refresh_storage_options(self, force=False):
        if not force and time.monotonic() < self.nvme_retry_at:
            return
        current = self.storage_options.get(self.storage_choice.get(), {'mode': 'default'})
        options = {'Source models folder (default)': {'mode': 'default'}}
        try:
            for volume in storage_volumes.nvme_volumes():
                options[storage_volumes.volume_label(volume)] = {
                    'mode': 'nvme', 'letter': volume['letter'],
                    'path': str(storage_volumes.stage_root_for(volume)),
                }
            if current.get('mode') == 'custom' and current.get('path'):
                label = f"Custom: {current['path']}"
                options[label] = current
            self.storage_options = options
            self.storage_select.configure(values=list(options))
            match = next((name for name, value in options.items()
                          if value.get('mode') == current.get('mode')
                          and value.get('letter') == current.get('letter')
                          and (value.get('mode') != 'custom' or value.get('path') == current.get('path'))), None)
            if match:
                self.storage_choice.set(match)
            elif current.get('mode') == 'nvme':
                self.storage_choice.set('Source models folder (default)')
            nvme_count = sum(1 for value in options.values() if value.get('mode') == 'nvme')
            if nvme_count:
                self.storage_note.configure(
                    text=f'{nvme_count} NVMe ready · Load copies into {storage_volumes.STAGE_FOLDER} if needed')
            else:
                self.storage_note.configure(text='No NVMe found · Browse can still use a verified copy')
            self.refresh_model_meta()
            self.nvme_retry_at = time.monotonic() + 30
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            self.nvme_retry_at = time.monotonic() + 10
            self.storage_note.configure(text='NVMe discovery unavailable: ' + str(exc))
            self.refresh_model_meta()

    def save_storage_choice(self, event=None):
        value = self.storage_options.get(self.storage_choice.get(), {'mode': 'default'})
        try:
            save_settings({'model_storage': None if value.get('mode') == 'default' else value})
            self.storage.set((value.get('path') or '') if value.get('mode') == 'custom' else '')
            self.error_label.configure(text='')
            return True
        except (OSError, ValueError) as exc:
            self.error_label.configure(text='Could not save storage setting: ' + str(exc))
            return False

    def copy_url(self, which='advertised'):
        urls = self.api_urls or {}
        if which == 'local':
            value = urls.get('local') or self.local_url_var.get()
        elif which == 'lan':
            value = urls.get('lan')
            if not value:
                self.error_label.configure(text='LAN URL is unavailable until Expose on LAN is enabled.')
                return
        else:
            value = urls.get('advertised') or urls.get('local') or self.local_url_var.get()
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.error_label.configure(text='')

    def open_maya(self):
        from maya_panel import open_panel
        panel = getattr(self, 'maya_panel', None)
        if panel is not None and panel.window.winfo_exists():
            panel.window.lift()
            return
        self.maya_panel = open_panel(self.root)

    def refresh_gpu_choice(self):
        if time.monotonic() < self.gpu_retry_at:
            return
        try:
            stamp = gpu_selection.SETTINGS.stat().st_mtime_ns if gpu_selection.SETTINGS.exists() else 0
            inventory = gpu_selection.cards()
            self.gpu_options = {'Auto · all GPUs': {'mode': 'auto'}}
            self.gpu_options.update({f'GPU {card["index"]} · {card["name"].replace("NVIDIA GeForce ", "")}':
                                     dict(mode='single', gpu_index=card['index'], **({'gpu_uuid': card['uuid']} if card.get('uuid') else {})) for card in inventory})
            self.gpu_select.configure(values=list(self.gpu_options))
            saved = gpu_selection.read_choice()
            self.gpu_choice.set(next((label for label, value in self.gpu_options.items()
                                      if gpu_selection.validate(value) == saved), 'Select a connected GPU'))
            self.gpu_stamp = stamp
            self.gpu_retry_at = time.monotonic() + 10
        except (OSError, ValueError, subprocess.SubprocessError):
            self.gpu_retry_at = time.monotonic() + 10
            self.gpu_select.configure(values=list(self.gpu_options))
            self.gpu_choice.set('GPU discovery unavailable')

    def save_gpu_choice(self, event=None):
        value = self.gpu_options.get(self.gpu_choice.get())
        if value is None:
            return False
        try:
            gpu_selection.plan(value)
            gpu_selection.save_choice(value)
            self.gpu_stamp = gpu_selection.SETTINGS.stat().st_mtime_ns
            return True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.error_label.configure(text=str(exc))
            return False

    def run(self, operation, *args):
        self.controller.submit(operation, *args)

    def advanced_values(self):
        values = {key: var.get() for key, var in self.advanced_vars.items()}
        if not self.conversation_cache.get():
            values = dict(values)
            values['conversation_cache_mib'] = '0'
        return values

    def save_advanced(self):
        try:
            if 'ram_use_percent' in self.advanced_vars:
                percentage = control_widgets.ram_percentage(self.advanced_vars['ram_use_percent'].get())
                choice = self.selected_choice() or {}
                if percentage == 0 and choice.get('family') == 'qwen36':
                    raise ValueError('This model needs an expert RAM cache. Choose Auto or a value above 0%.')
            save_settings({'advanced': self.advanced_values(), 'conversation_cache': bool(self.conversation_cache.get())})
            return True
        except (OSError, ValueError) as exc:
            self.error_label.configure(text='Could not save advanced settings: ' + str(exc))
            return False

    def load_model(self):
        choice = self.selected_choice()
        if not choice:
            return
        try:
            context = control_widgets.context_tokens(self.context.get(), choice['max_context'])
        except ValueError as exc:
            self.error_label.configure(text=str(exc))
            return
        if not self.save_reasoning() or not self.save_storage_choice() or not self.save_lan_access():
            return
        self.sync_conversation_cache_advanced()
        if not self.save_advanced():
            return
        advanced = self.advanced_values()
        if choice.get('engine') == 'maya':
            if self.maya is None:
                self.error_label.configure(text='GLM/Maya controller is unavailable.')
                return
            try:
                from legacy_window import LegacyWindowGuard
                if not hasattr(self, 'legacy_guard') or self.legacy_guard is None:
                    self.legacy_guard = LegacyWindowGuard()
                self.legacy_guard.acquire()
                context = choice.get('auto_context', choice['max_context']) if context == 'Auto' else (choice['max_context'] if context == 'Max' else context)
                kv_streaming = {'RAM + VRAM': 'on', 'VRAM only': 'off'}[advanced.get('kv_placement', 'RAM + VRAM')]
                self.maya.submit(
                    'load', context, self.mtp.get(), advanced.get('gpus', 'auto'),
                    int(advanced.get('parallel', '1')), advanced.get('kv', 'int8'),
                    advanced.get('reasoning', 'none'), advanced.get('split', 'auto'),
                    kv_streaming, advanced.get('ram_tier', 'auto'), advanced.get('vram_reserve_mib', 'auto'),
                    {key: advanced.get(key, 'auto') for key in ('prefill', 'read_chunks', 'cpu_workers', 'pcie_fraction')},
                )
            except (ValueError, RuntimeError, OSError, KeyError) as exc:
                self.error_label.configure(text=str(exc))
            return
        storage = self.storage_options.get(self.storage_choice.get(), {'mode': 'default'})
        args = [choice['id'], context, self.mtp.get()]
        if storage.get('mode') not in (None, 'default'):
            args.append(storage)
        else:
            args.append(None)
        args.append(advanced)
        # Images only when asked for and the family's encoder is verified on disk.
        args.append(bool(self.vision.get()) and self.vision_ready())
        self.run('load', *args)

    def eject_model(self):
        choice = self.selected_choice() or {}
        maya_busy = self.maya and (self.maya.operation or (
            self.maya.model_process is not None and self.maya.model_process.poll() is None))
        if choice.get('engine') == 'maya' or maya_busy:
            if self.maya:
                self.maya.cancel.set()
                self.maya.submit('unload')
            return
        self.run('unload')

    def choose_storage(self):
        folder = filedialog.askdirectory(parent=self.root, title='Select a verified copy of the models folder')
        if not folder:
            return
        label = f'Custom: {folder}'
        self.storage_options[label] = {'mode': 'custom', 'path': folder}
        self.storage_select.configure(values=list(self.storage_options))
        self.storage_choice.set(label)
        self.storage.set(folder)
        self.save_storage_choice()

    def request_stop(self):
        self.stop_requested = True
        self.controller.cancel.set()

    def close(self):
        panel = getattr(self, 'maya_panel', None)
        if panel is not None and panel.window.winfo_exists():
            panel.close()
        self.closing = True
        self.request_stop()

    def clear_advanced_traces(self):
        for variable, mode, token in self._advanced_traces:
            try:
                variable.trace_remove(mode, token)
            except (tk.TclError, AttributeError):
                pass
        self._advanced_traces.clear()

    def _root_destroyed(self, event):
        if event.widget is self.root:
            self.dispose()

    def dispose(self):
        """Release UI callbacks and variables on their owning thread; no model lifecycle work."""
        if self._disposed:
            return
        if threading.get_ident() != self._ui_thread:
            raise RuntimeError('Strata UI cleanup must run on its owning thread.')
        self._disposed = True
        self.closed.set()
        if hasattr(self, '_decode_bar_job'):
            self.stop_decode_bar_animation()
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except tk.TclError:
                pass
            self._tick_job = None
        self.clear_advanced_traces()
        variables = list(self.advanced_vars.values())
        self.advanced_vars.clear()
        for name, value in list(vars(self).items()):
            if isinstance(value, tk.Variable):
                variables.append(value)
                setattr(self, name, None)
        control_widgets.release_variables(variables)
        def clear_callbacks(widget):
            tooltip = getattr(widget, 'tooltip', None)
            if tooltip is not None:
                tooltip.hide()
                tooltip.widget = None
                widget.tooltip = None
            if isinstance(widget, control_widgets.ActionButton):
                widget._clear_ack()
                widget._action = None
            try:
                children = widget.winfo_children()
            except tk.TclError:
                return
            for child in children:
                clear_callbacks(child)
        clear_callbacks(self.root)

    def render_hardware(self):
        rows = control_widgets.hardware_rows(self.hardware)
        while len(self.meters) < len(rows):
            self.meters.append(control_widgets.Meter(self.hardware_host, '…', COLORS, layout=None))
        while len(self.meters) > len(rows):
            meter = self.meters.pop()
            meter.frame.destroy()
        columns = min(3, len(rows))
        for column in range(columns):
            self.hardware_host.columnconfigure(column, weight=1, uniform='hardware')
        for index, (meter, row) in enumerate(zip(self.meters, rows)):
            meter.frame.pack_forget()
            meter.frame.grid(row=index // columns, column=index % columns, sticky='ew',
                             padx=(0 if index % columns == 0 else 14, 0), pady=(0, 4))
            row['title'] = row['title'].replace('NVIDIA GeForce ', '')
            meter.set(row['title'], row['value'], row.get('meta', ''), row.get('fraction', 0.0), row.get('hot', False))

    def refresh_ram_preview(self):
        label = getattr(self, 'ram_preview', None)
        variable = self.advanced_vars.get('ram_use_percent')
        if label is None or variable is None or not label.winfo_exists():
            return
        try:
            percentage = control_widgets.ram_percentage(variable.get())
        except ValueError:
            label.configure(text='Enter Auto or 0–99.9%, with one decimal place.', fg=CAUTION)
            return
        if percentage == 0:
            q36 = (self.selected_choice() or {}).get('family') == 'qwen36'
            label.configure(text=('This model needs an expert RAM cache. Choose Auto or a value above 0%.' if q36 else
                                  'No explicit expert RAM copy.\nWindows / OS file caching may still use most free RAM. '
                                  'This is not a total RAM limit.'), fg=CAUTION)
            return
        loaded = (self.snapshot or {}).get('backend', {}).get('state') == 'up'
        if loaded or self.controller.operation == 'load':
            label.configure(text='Budget is recalculated after Eject, before the next load. A running model already occupies RAM.',
                            fg=MUTED)
            return
        ram = (self.hardware or {}).get('ram') or {}
        available, total = ram.get('available_bytes'), ram.get('total_bytes')
        if type(available) not in (int, float) or type(total) not in (int, float):
            label.configure(text='Waiting for available RAM. The expert cache budget is calculated at load.', fg=MUTED)
            return
        try:
            import hardware_planner
            cache = self.advanced_vars.get('conversation_cache_mib')
            choice = self.selected_choice() or {}
            preview = hardware_planner.ram_budget_preview(total, available,
                max(1, len((self.hardware or {}).get('gpus') or [])),
                ram_use_percent=variable.get(), conversation_cache_mib=cache.get() if cache else 0,
                packed=choice.get('family') != 'qwen36')
            budget = preview['ram_budget_bytes'] / 1024**3
            eligible = preview['ram_cache_available_bytes'] / 1024**3
            prefix = 'Auto' if percentage is None else f'{percentage:g}%'
            text = f'{prefix} · estimated maximum {budget:.1f} GiB expert RAM\n{eligible:.1f} GiB available after runtime buffers.'
            if percentage is not None and percentage >= 90:
                text += '\nHigh pressure: other apps may page to disk.'
            else:
                text += '\nActual allocation may be lower for this model.'
            label.configure(text=text, fg=CAUTION if percentage is not None and percentage >= 90 else ACCENT)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            label.configure(text=str(exc), fg=CAUTION)

    def render_requests(self):
        payload = (self.metrics or {}).get('data')
        rows = live_monitor.request_table_rows(payload) if (self.metrics or {}).get('state') == 'up' and payload else []
        stamp = tuple(tuple(row) for row in rows)
        if stamp != self._requests_stamp:
            yview = self.requests_view.yview()
            selected = [tuple(self.requests_view.item(item, 'values')) for item in self.requests_view.selection()]
            for item in self.requests_view.get_children():
                self.requests_view.delete(item)
            for index, row in enumerate(rows):
                item = self.requests_view.insert('', 'end', values=row, tags=('even' if index % 2 == 0 else 'odd',))
                if tuple(row) in selected:
                    self.requests_view.selection_add(item)
            if yview and yview[0] > .01:
                self.requests_view.yview_moveto(yview[0])
            self._requests_stamp = stamp
        self.requests_note.configure(text=(
            'Cached tokens are reused · Condition identifies wall timing scope · Scroll sideways for every metric'
            if rows else 'No completed requests yet. Load a model and send a prompt from your app.'))
        self.requests_view.tooltip.text = live_monitor.REQUEST_TIMING_NOTE

    def vision_encoder(self):
        """The image encoder for the selected model, or None (GLM and unknown families)."""
        choice = self.selected_choice() or {}
        if choice.get('engine') == 'maya':
            return None
        return model_downloads.vision_encoder_for(choice.get('family'))

    def vision_ready(self):
        encoder = self.vision_encoder()
        return bool(encoder and model_downloads.is_ready(encoder, ROOT / 'models'))

    def save_vision(self):
        try:
            save_settings({'vision': bool(self.vision.get())})
            return True
        except (OSError, ValueError) as exc:
            self.error_label.configure(text='Could not save the Images setting: ' + str(exc))
            return False

    def sync_conversation_cache_advanced(self):
        var = self.advanced_vars.get('conversation_cache_mib')
        if var is None:
            return
        if self.conversation_cache.get():
            if var.get() == '0':
                var.set(self._cache_mib_when_enabled)
            else:
                self._cache_mib_when_enabled = var.get()
        else:
            if var.get() != '0':
                self._cache_mib_when_enabled = var.get()
            var.set('0')
        mib = var.get() if self.conversation_cache.get() else '0'
        self.cache_note.configure(
            text=(f'{mib} MiB' if self.conversation_cache.get() else ''))

    def save_conversation_cache(self):
        try:
            self.sync_conversation_cache_advanced()
            save_settings({
                'conversation_cache': bool(self.conversation_cache.get()),
                'advanced': self.advanced_values(),
            })
            return True
        except (OSError, ValueError) as exc:
            self.error_label.configure(text='Could not save prefix cache: ' + str(exc))
            return False

    def download_assets(self):
        """Fetch every missing image encoder, the selected model's first."""
        selected = self.vision_encoder()
        wanted = model_downloads.missing(ROOT / 'models')
        wanted.sort(key=lambda item: item is not selected)
        if wanted:
            self.run('download', [item.id for item in wanted])

    def render_vision(self, idle, busy):
        """Images checkbox when the encoder is on disk; a download button when it is not."""
        encoder = self.vision_encoder()
        ready = self.vision_ready()
        downloading = self.controller.operation == 'download'
        if encoder is None:
            self.vision_download.pack_forget()
            self.vision_check.configure(state='disabled')
            self.vision_note.configure(text='no image encoder for this model')
            return
        self.vision_check.configure(state='normal' if ready and idle else 'disabled')
        if ready:
            self.vision_download.pack_forget()
            self.vision_note.configure(text='read on the CPU' if self.vision.get() else '')
        else:
            if not self.vision_download.winfo_manager():
                self.vision_download.pack(side='left', padx=(8, 0), before=self.vision_note)
            total = sum(item.size_gb for item in model_downloads.missing(ROOT / 'models'))
            # A download starts no engine, so it is allowed while a model is loaded.
            self.vision_download.configure(state='disabled' if busy else 'normal',
                                           text=f'Download image encoders · {total:.1f} GB')
            self.vision_note.configure(text='downloading…' if downloading else 'needed for images')

    def copy_requests(self):
        """Put the finished-request table on the clipboard as tab-separated text."""
        payload = (self.metrics or {}).get('data')
        self.root.clipboard_clear()
        self.root.clipboard_append(live_monitor.requests_tsv(payload))

    def paint_status(self, label, color, pill_bg, pill_fg=None):
        self.status.configure(text=label, fg=color)
        self.status_pill.configure(text=' ● ', bg=pill_bg, fg=pill_fg or color)

    def tick(self):
        if self._tick_job is not None:
            try:
                self.root.after_cancel(self._tick_job)
            except tk.TclError:
                pass
        self._tick_job = None
        if self._disposed:
            return
        self.refresh_gpu_choice()
        self.refresh_storage_options()
        self.refresh_api_label()
        self.render_hardware()
        self.refresh_ram_preview()
        if (self._recommend_after_hardware and isinstance(self.hardware, dict)
                and self.hardware.get('gpus') is not None):
            self.apply_recommendations(preserve_settings=True)
            self._recommend_after_hardware = False
            self._hardware_seen = True
        self.render_requests()
        state = self.snapshot
        fresh = state and not self.monitor_error and time.monotonic() - state['monotonic'] < 5
        maya_alive = (self.maya is not None and self.maya.model_process is not None
                      and self.maya.model_process.poll() is None)
        busy = bool(self.controller.operation or (self.maya and self.maya.operation))
        progress = getattr(self.controller, 'progress', None) or {}
        if self.models and not self.model_select.cget('values'):
            self.model_select.configure(values=[m['label'] for m in self.models])
            selected = next((m for m in self.models if state and m['id'] == state.get('model')), None)
            if selected is None and maya_alive:
                selected = next((m for m in self.models if m.get('engine') == 'maya'), self.models[0])
            self.model.set((selected or self.models[0])['label'])
            self.choose_model()
        elif self.catalog_done and not self.models:
            self.limit_note.configure(text=self.catalog_error or 'No fully prepared local models were found.')
            self.refresh_model_meta()
        if self.stop_requested and not busy:
            if maya_alive and self.maya and self.maya.submit('unload'):
                self.stop_requested = False
                busy = True
            elif self.controller.submit('stop'):
                self.stop_requested = False
                busy = True
        active_error = self.controller.error or (self.maya.error if self.maya else None)
        if active_error and active_error != self.shown_error and not self.stop_requested:
            self.shown_error = active_error
            self.closing = False
            self.error_label.configure(text='Last action: ' + active_error)
        elif busy and not active_error:
            self.shown_error = None
            self.error_label.configure(text='')
        if self.closing and not busy and fresh and state['state'] == 'Stopped' and not maya_alive:
            self.dispose()
            self.root.destroy()
            return
        total = progress.get('bytes_total') or 0
        done = progress.get('bytes_done') or 0
        fallback = {'start': 'Starting the API…', 'load': 'Loading model…', 'unload': 'Ejecting model…',
                    'stop': 'Stopping Strata…', 'download': 'Starting download…'}.get(
            self.controller.operation or (self.maya.operation if self.maya else None), '')
        note = progress.get('message') or fallback
        live_phase, live_detail, live = self.live_engine_banner()
        strip_busy = False
        strip_note = ''
        strip_phase = None
        if busy and note:
            strip_busy, strip_note, strip_phase = True, note, None
            self.detail.configure(text='')
        elif live_phase and live_detail:
            frac = live_monitor.live_request_fraction(live)
            detail = live_detail
            if frac is not None:
                detail = f'{live_detail} · {frac * 100:.0f}%'
            strip_busy, strip_note, strip_phase = True, detail, live_phase
        self.sync_progress_strip(strip_busy, strip_note, phase=strip_phase)
        self.refresh_progress_bar(operation_busy=busy, progress=progress, live=live if live_phase else None)
        maya_ready = bool(maya_alive and (self.maya_snapshot or {}).get('state') == 'up'
                          and ((self.maya_snapshot or {}).get('data') or {}).get('loaded') is True)
        choice = self.selected_choice() or {}
        selecting_maya = choice.get('engine') == 'maya'
        if maya_ready:
            self.paint_status('Model ready', ACCENT, '#16352f')
            data = (self.maya_snapshot or {}).get('data') or {}
            context = (data.get('context') or {}).get('native')
            self.detail.configure(text='GLM experimental model is loaded on Maya.')
            self.current.configure(
                text=f"Loaded: {data.get('model') or choice.get('id')}"
                + (f' · context {context:,}' if type(context) is int else ''))
            self.updated.configure(text='Live · ' + time.strftime('%H:%M:%S'))
        elif fresh:
            working = self.controller.operation or (self.maya.operation if self.maya else None)
            if live_phase and live_detail and not working:
                self.paint_status(live_phase, CAUTION, '#3a2f18')
            elif working:
                # Show the running action, not the last settled state ("Server ready").
                label = {'start': 'Starting…', 'load': 'Loading…', 'unload': 'Ejecting…', 'stop': 'Stopping…',
                         'download': 'Downloading…'}.get(working, 'Working…')
                if working == 'load' and progress.get('phase') == 'staging':
                    label = f'Copying to NVMe {100 * done // total}%' if total else 'Copying to NVMe…'
                elif working == 'download' and progress.get('phase') == 'downloading' and total:
                    label = f'Downloading {100 * done // total}%'
                self.paint_status(label, CAUTION, '#3a2f18')
            elif state['state'] in ('Model ready', 'Server ready'):
                self.paint_status(state['state'], ACCENT, '#16352f')
            elif state['state'] == 'Stopped':
                self.paint_status('Stopped', MUTED, '#243246')
            else:
                self.paint_status(state['state'], CAUTION, '#3a2f18')
            detail = {
                'start': 'Starting the API…',
                'load': progress.get('message') or 'Loading model weights. This can take several minutes.',
                'unload': 'Ejecting the model and verifying its processes have exited.',
                'stop': 'Stopping Strata and verifying its processes have exited.',
            }.get(self.controller.operation or ((self.maya.operation if self.maya else None)), state['detail'])
            if (self.maya and self.maya.operation) == 'load':
                detail = 'Loading GLM weights. This can take several minutes.'
            elif (self.maya and self.maya.operation) == 'unload':
                detail = 'Ejecting GLM and verifying owned processes have exited.'
            self.detail.configure(text=detail)
            name = state.get('model') or (
                'none' if state['state'] in ('Stopped', 'Server ready') else 'not confirmed')
            context = (state.get('context') or {}).get('native')
            pids = ', '.join(str(p['pid']) for p in state['processes'] if 'engine' in p['role'])
            self.current.configure(
                text=f'Loaded: {name}'
                + (f' · context {context:,}' if type(context) is int else '')
                + (f' · engine {pids}' if pids else ''))
            self.updated.configure(text='Live · ' + time.strftime('%H:%M:%S', time.localtime(state['checked'])))
        else:
            label = 'Checking…' if state is None and not self.monitor_error else 'Unknown'
            self.paint_status(label, CAUTION, '#3a2f18')
            self.detail.configure(text='Waiting for live process and API checks.' if state is None else
                                  'Live status is unavailable. Strata may still be running.')
            self.current.configure(text='Loaded: checking' if state is None else 'Loaded: not confirmed')
            self.updated.configure(text='Status unavailable' if state else 'Checking…')
        server_ready = fresh and state['state'] == 'Server ready'
        model_ready = fresh and state['backend']['state'] == 'up'
        stopped = fresh and state['state'] == 'Stopped'
        gateway_up = fresh and state['gateway']['state'] == 'up'
        idle_for_select = fresh and not busy and not model_ready and not maya_alive and state['state'] in (
            'Stopped', 'Server ready')
        self.model_select.configure(state='readonly' if idle_for_select else 'disabled')
        self.context_select.configure(state=('readonly' if selecting_maya else 'normal') if idle_for_select else 'disabled')
        storage_enabled = idle_for_select and not selecting_maya and server_ready
        self.storage_select.configure(state='readonly' if storage_enabled else 'disabled')
        self.storage_browse.configure(state='normal' if storage_enabled else 'disabled')
        self.gpu_select.configure(state='readonly' if idle_for_select and not selecting_maya else 'disabled')
        reasoning_enabled = idle_for_select if selecting_maya else gateway_up and not busy
        self.reason_select.configure(state='readonly' if reasoning_enabled else 'disabled')
        self.mtp_check.configure(state='normal' if idle_for_select else 'disabled')
        self.cache_check.configure(state='normal' if idle_for_select and not selecting_maya else 'disabled')
        self.render_vision(idle_for_select, busy)
        for widget in self.advanced_widgets:
            widget.configure(state=('normal' if getattr(widget, '_editable', False) else 'readonly')
                             if idle_for_select else 'disabled')
        self.lan_check.configure(state='normal' if stopped and not busy and not maya_alive else 'disabled')
        self.start.configure(state='normal' if fresh and not busy and state['state'] == 'Stopped' and not maya_alive else 'disabled')
        self.stop.configure(state='normal' if not fresh or busy or state['state'] != 'Stopped' or maya_alive else 'disabled')
        can_load_native = server_ready and self.models and not busy and not maya_alive and not selecting_maya
        can_load_maya = selecting_maya and idle_for_select and bool(choice.get('id')) and self.maya is not None
        self.load.configure(state='normal' if can_load_native or can_load_maya else 'disabled')
        self.unload.configure(state='normal' if not busy and (
            model_ready or maya_alive or any(p['role'] != 'gateway' for p in (state or {}).get('processes', []))) else 'disabled')
        self._tick_job = self.root.after(200, self.tick)


def main():
    if '--experimental-maya' in sys.argv:
        from maya_panel import open_panel
        from types import SimpleNamespace
        root = tk.Tk()
        root.withdraw()
        App.style(SimpleNamespace(root=root))
        panel = open_panel(root)
        if panel is None:
            root.destroy()
            return

        def finish_panel():
            if not panel.window.winfo_exists():
                root.destroy()
            else:
                root.after(200, finish_panel)

        root.after(200, finish_panel)
        root.mainloop()
        return
    try:
        instance_lock = lock('desktop-control')
    except AlreadyRunning:
        user = ctypes.windll.user32
        window = user.FindWindowW(None, 'Strata Control')
        if window:
            user.ShowWindow(window, 9)
            user.SetForegroundWindow(window)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = tk.Tk()
    app = App(root)
    if '--start-server' in sys.argv:
        root.after(300, lambda: app.run('start'))
    root.mainloop()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        (ROOT / 'desktop-error.log').write_text(str(exc), encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None, str(exc), 'Strata Control could not open', 0x10)
        raise
