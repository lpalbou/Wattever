"""Small Tk visual helpers for Strata Control."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import math
import re


class Tooltip:
    """Small keyboard-accessible hints that do not steal focus."""
    def __init__(self, widget, text, delay=500):
        self.widget, self.text, self.delay = widget, text, delay
        self.job = self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<FocusIn>', self.schedule, add='+')
        for event in ('<Leave>', '<FocusOut>', '<ButtonPress>', '<Destroy>'):
            widget.bind(event, self.hide, add='+')

    def schedule(self, event=None):
        if self.widget is None:
            return
        self.hide()
        self.job = self.widget.after(self.delay, self.show)

    def show(self):
        self.job = None
        if self.widget is None or not self.widget.winfo_exists() or not self.text:
            return
        self.window = tk.Toplevel(self.widget)
        self.window.overrideredirect(True)
        self.window.attributes('-topmost', True)
        tk.Label(self.window, text=self.text, bg='#23364a', fg='#f2f7fc',
                 font=('Segoe UI', 9), padx=10, pady=7, justify='left',
                 wraplength=330, relief='solid', borderwidth=1).pack()
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx(), self.widget.winfo_screenwidth() - self.window.winfo_reqwidth() - 12)
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        if y + self.window.winfo_reqheight() > self.widget.winfo_screenheight() - 16:
            y = self.widget.winfo_rooty() - self.window.winfo_reqheight() - 6
        self.window.geometry(f'+{max(8, x)}+{max(8, y)}')

    def hide(self, event=None):
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None


class ActionButton(ttk.Button):
    """Consistent short labels, accessible hints and visible pointer feedback."""
    def __init__(self, parent, text, *, icon='', tip='', **kwargs):
        self.label, self.icon = text, icon
        self._action = kwargs.pop('command', None)
        self._ack_job = None
        super().__init__(parent, text=(icon + '  ' + text).strip(), takefocus=True, cursor='hand2',
                         command=self._invoke_action, **kwargs)
        self.tooltip = Tooltip(self, tip or text)
        self.bind('<Enter>', self._pointer, add='+')
        self.bind('<Destroy>', self._clear_ack, add='+')

    def _invoke_action(self):
        if self.instate(['disabled']):
            return
        self.tooltip.hide()
        self._clear_ack()
        self.state(['selected'])
        self._ack_job = self.after(200, self._clear_ack)
        self.update_idletasks()
        try:
            return self._action() if self._action else None
        except Exception:
            self._clear_ack()
            raise

    def _clear_ack(self, event=None):
        if self._ack_job is not None:
            try:
                self.after_cancel(self._ack_job)
            except tk.TclError:
                pass
            self._ack_job = None
        try:
            if self.winfo_exists():
                self.state(['!selected'])
        except tk.TclError:
            pass

    def _pointer(self, event=None):
        self.configure(cursor='arrow' if self.instate(['disabled']) else 'hand2')

    def set_label(self, text, icon=None):
        self.label = text
        if icon is not None:
            self.icon = icon
        self.configure(text=(self.icon + '  ' + text).strip())


def ram_percentage(value):
    """Return None for Auto, otherwise an explicit one-decimal percentage."""
    text = str(value).strip()
    if text.lower() == 'auto':
        return None
    if not re.fullmatch(r'\d{1,2}(?:\.\d)?', text):
        raise ValueError('RAM use must be Auto or 0–99.9%, with at most one decimal place.')
    number = float(text)
    if not math.isfinite(number) or not 0 <= number <= 99.9:
        raise ValueError('RAM use must be Auto or 0–99.9%, with at most one decimal place.')
    return number


def valid_ram_edit(text):
    """Permit partial edits while preventing out-of-range or excess-precision input."""
    return text.lower() in ('', 'a', 'au', 'aut', 'auto') or bool(re.fullmatch(r'\d{1,2}(?:\.\d?)?', text))


def context_tokens(value, maximum):
    """Parse exact token counts and K=1024 shorthand without rescaling plain numbers."""
    text = str(value).strip()
    if text.lower() in ('auto', 'max'):
        return text.capitalize()
    match = re.fullmatch(r'(\d+)([kK]?)', text)
    if not match:
        raise ValueError('Context: use Auto, Max, a token count such as 40960, or 40K (K = 1,024).')
    tokens = int(match.group(1)) * (1024 if match.group(2) else 1)
    if not 1024 <= tokens <= maximum:
        raise ValueError(f'Context must be between 1,024 and {maximum:,} tokens, or Auto.')
    return tokens


def release_variables(variables):
    """Release owned Tcl variables on the UI thread, even if Python references outlive it."""
    for variable in variables:
        if not isinstance(variable, tk.Variable) or variable._tk is None:
            continue
        try:
            # Tkinter exposes no public dispose method. Its destructor performs
            # the Tcl-side release; Tkinter's internal sentinel makes a
            # later Python finalizer a no-op on an arbitrary collecting thread.
            tk.Variable.__del__(variable)
        except tk.TclError:
            pass
        finally:
            variable._tk = None


class Meter:
    """One labeled pressure bar with a trailing value."""

    def __init__(self, parent, title, colors, layout='pack'):
        self.colors = colors
        self.frame = tk.Frame(parent, bg=colors['card'])
        if layout == 'pack':
            self.frame.pack(fill='x', pady=(0, 4))
        top = tk.Frame(self.frame, bg=colors['card'])
        top.pack(fill='x')
        self.title = tk.Label(top, text=title, bg=colors['card'], fg=colors['ink'],
                              font=('Segoe UI Semibold', 9), anchor='w')
        self.title.pack(side='left', fill='x', expand=True)
        self.value = tk.Label(top, text='–', bg=colors['card'], fg=colors['muted'],
                              font=('Segoe UI', 9), anchor='e')
        self.value.pack(side='right')
        self.meta = tk.Label(self.frame, text='', bg=colors['card'], fg=colors['muted'],
                             font=('Segoe UI', 8), anchor='w')
        self.meta.pack(fill='x', pady=(0, 2))
        self.canvas = tk.Canvas(self.frame, height=6, bg=colors['card'], highlightthickness=0, bd=0)
        self.canvas.pack(fill='x')
        self.fraction = 0.0
        self.canvas.bind('<Configure>', lambda event: self.draw())

    def set(self, title, value, meta='', fraction=0.0, hot=False):
        self.title.configure(text=title)
        self.value.configure(text=value, fg=self.colors['warn'] if hot else self.colors['ink'])
        self.meta.configure(text=meta)
        self.fraction = 0.0 if fraction is None else max(0.0, min(1.0, float(fraction)))
        self.draw()

    def draw(self):
        self.canvas.delete('all')
        width = max(self.canvas.winfo_width(), 40)
        height = 6
        trough = self.colors['trough']
        fill = self.colors['warn'] if self.fraction >= 0.9 else (
            self.colors['caution'] if self.fraction >= 0.75 else self.colors['accent'])
        self.canvas.create_rectangle(0, 0, width, height, fill=trough, outline='')
        self.canvas.create_rectangle(0, 0, max(2, int(width * self.fraction)), height, fill=fill, outline='')


def hardware_rows(snapshot):
    """Normalize a hardware snapshot into meter rows for the UI."""
    if not snapshot:
        return [{'title': 'System', 'value': 'waiting…', 'meta': '', 'fraction': 0.0, 'hot': False}]
    rows = []
    ram = snapshot.get('ram') or {}
    total = ram.get('total_bytes') or 0
    used = ram.get('used_bytes') or 0
    pct = (ram.get('percent') or 0) / 100.0
    rows.append({
        'title': 'System RAM',
        'value': f"{used / (1024 ** 3):.1f} / {total / (1024 ** 3):.1f} GiB",
        'meta': f"{ram.get('percent', 0):.0f}% used",
        'fraction': pct,
        'hot': pct >= 0.9,
    })
    if snapshot.get('error') and not snapshot.get('gpus'):
        rows.append({
            'title': 'GPUs',
            'value': 'unavailable',
            'meta': snapshot['error'],
            'fraction': 0.0,
            'hot': False,
        })
        return rows
    for gpu in snapshot.get('gpus') or []:
        total = gpu.get('memory_total_mib') or 0
        used = gpu.get('memory_used_mib') or 0
        frac = (used / total) if total else 0.0
        util = gpu.get('gpu_util_pct')
        temp = gpu.get('temperature_c')
        power = gpu.get('power_w')
        util_text = '–' if util is None else f'{util:.0f}%'
        temp_text = '–' if temp is None else f'{temp:.0f}°C'
        power_text = '–' if power is None else f'{power:.0f} W'
        rows.append({
            'title': f"GPU {gpu['index']} · {gpu['name']}",
            'value': f"{(used or 0) / 1024:.1f} / {(total or 0) / 1024:.1f} GiB",
            'meta': f"util {util_text} · {temp_text} · {power_text}",
            'fraction': frac,
            'hot': (temp or 0) >= 80 or frac >= 0.92,
        })
    return rows
