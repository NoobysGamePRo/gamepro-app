"""
Script settings dialog.

A script can declare an optional SETTINGS list on its class; the app then shows this dialog
when Run is pressed and passes the chosen values to the script as `script.settings` (a dict).
Values are remembered per script between runs.

SETTINGS entries (dicts):
    {'type': 'section', 'label': 'Heading text'}
    {'key': 'k', 'type': 'choice', 'label': 'Mode', 'choices': ['a', 'b'], 'default': 'a'}
    {'key': 'k', 'type': 'bool',   'label': 'Keep shinies', 'default': True}
    {'key': 'k', 'type': 'int',    'label': 'Min HP IV', 'min': 0, 'max': 31, 'default': 0}
    {'key': 'k', 'type': 'multi',  'label': 'Natures', 'choices': [...], 'default': []}
    {'key': 'k', 'type': 'text',   'label': 'Note', 'default': ''}
Any entry may add 'help': 'tooltip-style hint shown under the control' and
'show_if': ('other_key', ['value', ...]) to only show it for some choices of another setting.

Optionally the script class can define a static/class method `estimate(values) -> str`;
its text is shown at the bottom of the dialog and refreshed whenever a value changes.
"""

import json
import threading
import os
import tkinter as tk
from tkinter import ttk

BG, FG, ACCENT = '#1a2d6b', '#ffffff', '#cc0000'


def _store_path():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'GamePRo')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'script_settings.json')


def load_saved(script_name):
    try:
        with open(_store_path(), 'r', encoding='utf-8') as f:
            return json.load(f).get(script_name, {})
    except Exception:
        return {}


def save_values(script_name, values):
    try:
        try:
            with open(_store_path(), 'r', encoding='utf-8') as f:
                allv = json.load(f)
        except Exception:
            allv = {}
        allv[script_name] = values
        with open(_store_path(), 'w', encoding='utf-8') as f:
            json.dump(allv, f, indent=1)
    except Exception:
        pass


def defaults(schema):
    return {e['key']: e.get('default') for e in schema if 'key' in e}


class SettingsDialog(tk.Toplevel):
    """Modal dialog. After wait_window(), `.result` is the values dict, or None if cancelled."""

    def __init__(self, parent, script_cls):
        super().__init__(parent)
        self.title(f'{script_cls.NAME} — settings')
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, True)
        self.result = None
        self._cls = script_cls
        self._schema = list(getattr(script_cls, 'SETTINGS', []) or [])
        saved = load_saved(script_cls.NAME)
        self._values = defaults(self._schema)
        for k, v in saved.items():
            if k in self._values:
                self._values[k] = v
        self._vars, self._rows = {}, []

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill='both', expand=True, padx=10, pady=8)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0, width=460, height=520)
        sb = ttk.Scrollbar(outer, orient='vertical', command=canvas.yview)
        self._body = tk.Frame(canvas, bg=BG)
        self._body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self._body, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')
        canvas.bind_all('<MouseWheel>', lambda e: canvas.yview_scroll(int(-e.delta / 120), 'units'))
        self._canvas = canvas

        self._build()

        self._est = tk.Label(self, text='', bg=BG, fg='#ffd54a', justify='left', anchor='w',
                             wraplength=460, font=('Segoe UI', 9))
        self._est.pack(fill='x', padx=12, pady=(0, 4))
        btns = tk.Frame(self, bg=BG)
        btns.pack(fill='x', padx=10, pady=(0, 10))
        tk.Button(btns, text='Reset to defaults', command=self._reset).pack(side='left')
        tk.Button(btns, text='Cancel', command=self._cancel).pack(side='right', padx=(6, 0))
        tk.Button(btns, text='Run', bg=ACCENT, fg=FG, width=10, command=self._ok).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self._cancel)
        self._refresh()
        self.grab_set()
        self.focus_set()

    # ── Building ────────────────────────────────────────────────────────────
    def _build(self):
        for w in self._body.winfo_children():
            w.destroy()
        self._vars, self._rows = {}, []
        for e in self._schema:
            row = tk.Frame(self._body, bg=BG)
            self._rows.append((e, row))
            t = e.get('type')
            if t == 'section':
                tk.Label(row, text=e.get('label', ''), bg=BG, fg='#ff6b6b',
                         font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(8, 2))
                continue
            key, val = e['key'], self._values.get(e['key'], e.get('default'))
            tk.Label(row, text=e.get('label', key), bg=BG, fg=FG, width=24, anchor='w',
                     justify='left', wraplength=180).pack(side='left', anchor='n')
            if t == 'choice':
                v = tk.StringVar(value=str(val))
                cb = ttk.Combobox(row, textvariable=v, values=list(e['choices']), state='readonly',
                                  width=32)
                cb.pack(side='left')
                v.trace_add('write', lambda *_: self._refresh())
            elif t == 'bool':
                v = tk.BooleanVar(value=bool(val))
                tk.Checkbutton(row, variable=v, bg=BG, activebackground=BG, selectcolor=BG,
                               fg=FG, command=self._refresh).pack(side='left')
            elif t == 'int':
                v = tk.StringVar(value=str(val))
                tk.Spinbox(row, from_=e.get('min', 0), to=e.get('max', 999), textvariable=v,
                           width=8).pack(side='left')
                v.trace_add('write', lambda *_: self._refresh())
            elif t == 'multi':
                lb = tk.Listbox(row, selectmode='multiple', height=min(8, len(e['choices'])),
                                exportselection=False, width=26)
                for i, c in enumerate(e['choices']):
                    lb.insert('end', c)
                    if c in (val or []):
                        lb.selection_set(i)
                lb.pack(side='left')
                lb.bind('<<ListboxSelect>>', lambda *_: self._refresh())
                v = lb
            else:
                v = tk.StringVar(value=str(val or ''))
                tk.Entry(row, textvariable=v, width=28).pack(side='left')
                v.trace_add('write', lambda *_: self._refresh())
            self._vars[key] = (e, v)
            if e.get('help'):
                h = tk.Frame(self._body, bg=BG)
                tk.Label(h, text=e['help'], bg=BG, fg='#b8c4ff', font=('Segoe UI', 8),
                         justify='left', wraplength=440).pack(anchor='w', padx=(4, 0))
                self._rows.append((e, h))

    def _read(self):
        out = dict(self._values)
        for key, (e, v) in self._vars.items():
            t = e.get('type')
            try:
                if t == 'multi':
                    out[key] = [e['choices'][i] for i in v.curselection()]
                elif t == 'int':
                    x = int(v.get())
                    out[key] = max(e.get('min', x), min(e.get('max', x), x))
                elif t == 'bool':
                    out[key] = bool(v.get())
                else:
                    out[key] = v.get()
            except (ValueError, tk.TclError):
                pass
        return out

    def _refresh(self):
        vals = self._read()
        for e, row in self._rows:
            si = e.get('show_if')
            show = si is None or vals.get(si[0]) in si[1]
            if show and not row.winfo_ismapped():
                row.pack(fill='x', pady=1)
            elif not show and row.winfo_ismapped():
                row.pack_forget()
        # keep the original order when rows reappear
        for e, row in self._rows:
            if row.winfo_ismapped():
                row.pack_forget()
                row.pack(fill='x', pady=1)
        est = getattr(self._cls, 'estimate', None)
        if callable(est):
            # estimates can take a second or two: debounce, then compute off the UI thread
            if getattr(self, '_est_job', None):
                self.after_cancel(self._est_job)
            self._est_seq = getattr(self, '_est_seq', 0) + 1
            seq = self._est_seq

            def work():
                try:
                    txt = est(vals) or ''
                except Exception as ex:
                    txt = f'(estimate unavailable: {ex})'
                try:
                    self.after(0, lambda: seq == self._est_seq and self.winfo_exists()
                               and self._est.config(text=txt))
                except Exception:
                    pass

            def start():
                self._est_job = None
                self._est.config(text='Estimating...')
                threading.Thread(target=work, daemon=True).start()
            self._est_job = self.after(400, start)

    # ── Buttons ─────────────────────────────────────────────────────────────
    def _reset(self):
        self._values = defaults(self._schema)
        self._build()
        self._refresh()

    def _ok(self):
        self.result = self._read()
        save_values(self._cls.NAME, self.result)
        self._canvas.unbind_all('<MouseWheel>')
        self.destroy()

    def _cancel(self):
        self.result = None
        self._canvas.unbind_all('<MouseWheel>')
        self.destroy()
