"""
GamePRo Script Builder
======================
Record a script spec by clicking buttons in sync with your game.
The tool captures each button press and the timing between them automatically.
Add detection points by drawing regions on the live video feed.
Export the spec to clipboard and paste it to Claude for script generation.

Opening: click the [Script Builder] button in the Scripts panel header.
"""

import json
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

# Brand colours (duplicated from app.py to avoid circular import)
BG       = '#1a2d6b'
BG2      = '#0f1d4a'
BG3      = '#243580'
ACCENT   = '#cc0000'
ACCENT_H = '#aa0000'
FG       = '#ffffff'
FG2      = '#aabbdd'

# ── Button definitions ─────────────────────────────────────────────────────────
# (label shown in UI, controller method name)
BTN_DEFS = [
    ('A',    'press_a'),     ('B',    'press_b'),
    ('X',    'press_x'),     ('Y',    'press_y'),
    ('↑',    'press_up'),    ('↓',    'press_down'),
    ('←',    'press_left'),  ('→',    'press_right'),
    ('H↑',   'hold_up'),     ('H↓',   'hold_down'),
    ('H←',   'hold_left'),   ('H→',   'hold_right'),
    ('Home', 'soft_reset'),  ('SR2',  'soft_reset_z'),
    ('+(W)', 'wonder_trade'),('0',    'release_all'),
]

CONSOLES = ['3DS', 'Switch / Switch 2']

# Video canvas size (frames are assumed 640×480; this is displayed at 75%)
CANVAS_W = 480
CANVAS_H = 360
SCALE    = 640 / 480   # canvas→frame coordinate multiplier (~1.333)


class ScriptBuilderDialog(tk.Toplevel):
    """
    Script spec builder — records button sequences with timing from real
    game playthrough, then exports a structured spec for Claude.
    """

    def __init__(self, parent: tk.Tk, frame_grabber, controller=None, scripts_dir=None):
        super().__init__(parent)
        self.title('GamePRo Script Builder')
        self.configure(bg=BG)
        self.resizable(True, True)
        self.transient(parent)

        self._grabber     = frame_grabber
        self._controller  = controller
        self._scripts_dir = scripts_dir
        self._steps       = []          # list of step dicts
        self._sel_idx   = None        # currently selected step index

        self._recording        = False
        self._rec_start        = 0.0
        self._last_click_time  = 0.0
        self._timer_id         = None

        self._test_running  = False
        self._test_stop     = threading.Event()

        self._canvas_mode  = None    # None | 'detect'
        self._drag_start   = None
        self._drag_rect_id = None
        self._use_corners_var = tk.BooleanVar(value=True)

        self._build()
        self._update_video()

        # Centre over parent
        self.update_idletasks()
        pw = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        ph = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f'+{pw}+{ph}')
        self.protocol('WM_DELETE_WINDOW', self._on_builder_close)

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build(self):
        # ── Metadata row ──────────────────────────────────────────────────────
        meta = tk.Frame(self, bg=BG2, padx=8, pady=6)
        meta.pack(fill='x')

        tk.Label(meta, text='Console:', bg=BG2, fg=FG2,
                 font=('Arial', 9)).pack(side='left')
        self._console_var = tk.StringVar(value=CONSOLES[0])
        ttk.Combobox(meta, textvariable=self._console_var, values=CONSOLES,
                     width=10, state='readonly').pack(side='left', padx=(2, 10))

        # ── Main area: left steps panel + right video ──────────────────────
        main = tk.Frame(self, bg=BG)
        main.pack(fill='both', expand=True)

        # Left: step list + editor (300px)
        left = tk.Frame(main, bg=BG, width=300)
        left.pack(side='left', fill='y')
        left.pack_propagate(False)

        # Step list header
        lhdr = tk.Frame(left, bg=BG, padx=6, pady=4)
        lhdr.pack(fill='x')
        self._step_count_lbl = tk.Label(lhdr, text='Steps (0)',
                                         bg=BG, fg=FG2, font=('Arial', 9, 'bold'))
        self._step_count_lbl.pack(side='left')

        # Step listbox
        lf = tk.Frame(left, bg=BG2, highlightthickness=1,
                      highlightbackground=BG3)
        lf.pack(fill='both', expand=True, padx=6)

        vsb = ttk.Scrollbar(lf, orient='vertical')
        self._step_list = tk.Listbox(
            lf, yscrollcommand=vsb.set, selectmode='single',
            bg=BG2, fg=FG, font=('Courier', 8), relief='flat', bd=0,
            activestyle='none', selectbackground=BG3,
        )
        vsb.config(command=self._step_list.yview)
        self._step_list.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self._step_list.bind('<<ListboxSelect>>', self._on_step_select)

        # Step control buttons
        ctrl = tk.Frame(left, bg=BG, padx=6, pady=3)
        ctrl.pack(fill='x')
        bs = dict(bg=BG3, fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2)
        tk.Button(ctrl, text='↑', command=self._move_up,    **bs).pack(side='left')
        tk.Button(ctrl, text='↓', command=self._move_down,  **bs).pack(side='left', padx=2)
        tk.Button(ctrl, text='Del', command=self._delete_step,
                  bg=ACCENT, fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)
        tk.Button(ctrl, text='+ Wait', command=self._add_wait, **bs).pack(side='left', padx=2)
        tk.Button(ctrl, text='+ Rand', command=self._add_random_wait,
                  bg='#4a3a0a', fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)
        tk.Button(ctrl, text='+ LDR', command=self._add_ldr,
                  bg='#3a1a5c', fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)
        tk.Button(ctrl, text='+ Block', command=self._add_block,
                  bg='#335577', fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)
        tk.Button(ctrl, text='+ Detect', command=self._start_draw,
                  bg='#1a5c1a', fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)
        tk.Button(ctrl, text='⧖ Adjust All', command=self._adjust_timings_dialog,
                  bg='#44337a', fg=FG, relief='flat', font=('Arial', 8),
                  cursor='hand2', padx=4, pady=2).pack(side='left', padx=2)

        # Step editor
        ed = tk.Frame(left, bg=BG2, padx=8, pady=6)
        ed.pack(fill='x', padx=6, pady=(0, 4))

        tk.Label(ed, text='Edit selected step', bg=BG2, fg=FG2,
                 font=('Arial', 8, 'bold')).pack(anchor='w', pady=(0, 4))

        # Block name row — enabled only when a block header is selected
        bn_row = tk.Frame(ed, bg=BG2)
        bn_row.pack(fill='x', pady=1)
        tk.Label(bn_row, text='Block Name:', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        self._block_name_var = tk.StringVar()
        self._block_name_entry = tk.Entry(
            bn_row, textvariable=self._block_name_var,
            bg=BG, fg=FG, relief='flat', font=('Arial', 8), width=20,
            insertbackground=FG, state='disabled',
            disabledbackground=BG2, disabledforeground=FG2)
        self._block_name_entry.pack(side='left')

        def _field(parent, label, var, lo, hi, inc, width=7):
            row = tk.Frame(parent, bg=BG2)
            row.pack(fill='x', pady=1)
            tk.Label(row, text=label, bg=BG2, fg=FG,
                     font=('Arial', 8), width=12, anchor='w').pack(side='left')
            tk.Spinbox(row, from_=lo, to=hi, increment=inc, textvariable=var,
                       width=width, bg=BG, fg=FG, relief='flat',
                       font=('Courier', 9), buttonbackground=BG3,
                       insertbackground=FG).pack(side='left')

        self._delay_var = tk.DoubleVar(value=1.0)
        _field(ed, 'Delay (s):', self._delay_var, 0, 300, 0.25)

        # Random wait min/max — hidden unless a random_wait step is selected
        self._rw_min_var = tk.DoubleVar(value=0.5)
        self._rw_max_var = tk.DoubleVar(value=2.0)
        self._rw_row = tk.Frame(ed, bg=BG2)
        self._rw_row.pack(fill='x', pady=1)
        tk.Label(self._rw_row, text='Min (s):', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        tk.Spinbox(self._rw_row, from_=0, to=300, increment=0.25,
                   textvariable=self._rw_min_var, width=7,
                   bg=BG, fg=FG, relief='flat', font=('Courier', 9),
                   buttonbackground=BG3, insertbackground=FG).pack(side='left', padx=(0, 8))
        tk.Label(self._rw_row, text='Max (s):', bg=BG2, fg=FG,
                 font=('Arial', 8), anchor='w').pack(side='left')
        tk.Spinbox(self._rw_row, from_=0, to=300, increment=0.25,
                   textvariable=self._rw_max_var, width=7,
                   bg=BG, fg=FG, relief='flat', font=('Courier', 9),
                   buttonbackground=BG3, insertbackground=FG).pack(side='left')
        self._rw_row.pack_forget()

        # LDR fields — hidden unless an ldr step is selected
        self._ldr_mode_var      = tk.StringVar(value='Below')
        self._ldr_threshold_var = tk.IntVar(value=500)
        self._ldr_row = tk.Frame(ed, bg=BG2)
        self._ldr_row.pack(fill='x', pady=1)
        tk.Label(self._ldr_row, text='Mode:', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        ttk.Combobox(self._ldr_row, textvariable=self._ldr_mode_var,
                     values=['Below', 'Above'], width=7,
                     state='readonly').pack(side='left', padx=(0, 8))
        tk.Label(self._ldr_row, text='Threshold:', bg=BG2, fg=FG,
                 font=('Arial', 8), anchor='w').pack(side='left')
        tk.Spinbox(self._ldr_row, from_=0, to=1020, increment=1,
                   textvariable=self._ldr_threshold_var, width=5,
                   bg=BG, fg=FG, relief='flat', font=('Courier', 9),
                   buttonbackground=BG3, insertbackground=FG).pack(side='left', padx=(4, 0))
        tk.Label(self._ldr_row, text=' /1020', bg=BG2, fg=FG2,
                 font=('Arial', 7)).pack(side='left')
        self._ldr_row.pack_forget()

        self._ldr_test_btn = tk.Button(
            ed, text='📊 Test LDR…', command=self._open_ldr_test,
            bg='#2a1a4a', fg=FG, relief='flat', font=('Arial', 8),
            cursor='hand2', padx=6, pady=2)
        # hidden until ldr step selected
        self._ldr_test_btn.pack(pady=(2, 0), anchor='w')
        self._ldr_test_btn.pack_forget()

        self._note_row = tk.Frame(ed, bg=BG2)
        note_row = self._note_row
        note_row.pack(fill='x', pady=1)
        tk.Label(note_row, text='Note:', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        self._note_var = tk.StringVar()
        tk.Entry(note_row, textvariable=self._note_var, bg=BG, fg=FG,
                 relief='flat', font=('Arial', 8), width=20,
                 insertbackground=FG).pack(side='left')

        # Detect-only fields (always visible; only relevant for detect steps)
        self._window_var = tk.DoubleVar(value=6.0)
        _field(ed, 'Window (s):', self._window_var, 0.5, 60, 0.5)

        self._tol_var = tk.IntVar(value=25)
        _field(ed, 'Tolerance:', self._tol_var, 1, 255, 1)

        # Detection method selector
        meth_row = tk.Frame(ed, bg=BG2)
        meth_row.pack(fill='x', pady=1)
        tk.Label(meth_row, text='Method:', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        self._method_var = tk.StringVar(value='Avg RGB')
        self._method_combo = ttk.Combobox(
            meth_row, textvariable=self._method_var,
            values=['Avg RGB', 'Pixel Count', 'Target Color'],
            width=12, state='readonly')
        self._method_combo.pack(side='left')
        self._method_combo.bind('<<ComboboxSelected>>', self._on_method_change)

        # Pixel count threshold (only relevant for Pixel Count method)
        self._px_threshold_var = tk.IntVar(value=10)
        self._px_threshold_row = tk.Frame(ed, bg=BG2)
        self._px_threshold_row.pack(fill='x', pady=1)
        tk.Label(self._px_threshold_row, text='Px Threshold:', bg=BG2, fg=FG,
                 font=('Arial', 8), width=12, anchor='w').pack(side='left')
        self._px_threshold_sb = tk.Spinbox(
            self._px_threshold_row, from_=1, to=100000,
            increment=1, textvariable=self._px_threshold_var,
            width=7, bg=BG, fg=FG, relief='flat',
            font=('Courier', 9), buttonbackground=BG3,
            insertbackground=FG)
        self._px_threshold_sb.pack(side='left')
        tk.Label(self._px_threshold_row, text=' pixels', bg=BG2, fg=FG2,
                 font=('Arial', 7)).pack(side='left')
        # Hidden initially — shown only for Pixel Count method
        self._px_threshold_row.pack_forget()

        # Target colour row — shown only for Target Color method
        self._target_color_row = tk.Frame(ed, bg=BG2)
        self._target_color_row.pack(fill='x', pady=1)
        self._target_swatch = tk.Label(
            self._target_color_row, text='   ', bg='#888888',
            relief='solid', width=3)
        self._target_swatch.pack(side='left', padx=(0, 4))
        self._target_rgb_lbl = tk.Label(
            self._target_color_row,
            text='Target: not set — pick via Compare Shiny Image',
            bg=BG2, fg=FG2, font=('Courier', 7))
        self._target_rgb_lbl.pack(side='left')
        self._target_color_row.pack_forget()

        self._region_lbl = tk.Label(ed, text='Region: —',
                                     bg=BG2, fg=FG2, font=('Courier', 7))
        self._region_lbl.pack(anchor='w', pady=(2, 0))
        self._baseline_lbl = tk.Label(ed, text='Baseline: —',
                                       bg=BG2, fg=FG2, font=('Courier', 7))
        self._baseline_lbl.pack(anchor='w')

        self._fixed_var = tk.BooleanVar(value=False)
        self._fixed_cb = tk.Checkbutton(
            ed, text='Fixed region (use builder settings — no user setup)',
            variable=self._fixed_var,
            bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2,
            activeforeground=FG, font=('Arial', 8))
        self._fixed_cb.pack(anchor='w', pady=(2, 0))

        self._compare_btn = tk.Button(
            ed, text='Compare Shiny Image…', command=self._open_compare,
            bg='#1a4455', fg=FG, relief='flat', font=('Arial', 8),
            cursor='hand2', padx=6, pady=2, state='disabled')
        self._compare_btn.pack(pady=(3, 0), anchor='w')

        tk.Button(ed, text='Apply Changes', command=self._apply_edit,
                  bg=ACCENT, fg=FG, relief='flat', font=('Arial', 8, 'bold'),
                  cursor='hand2', padx=8, pady=3).pack(pady=(6, 0))

        # Setup note
        sn = tk.Frame(left, bg=BG, padx=6, pady=2)
        sn.pack(fill='x')
        tk.Label(sn, text='Setup note:', bg=BG, fg=FG2,
                 font=('Arial', 8)).pack(anchor='w')
        self._setup_var = tk.StringVar()
        tk.Entry(sn, textvariable=self._setup_var, bg=BG2, fg=FG,
                 relief='flat', font=('Arial', 8), insertbackground=FG,
                 width=38).pack(fill='x')

        # ── Right panel: video + recording controls ────────────────────────
        right = tk.Frame(main, bg=BG)
        right.pack(side='left', fill='both', expand=True, padx=(4, 6))

        # Recording controls row
        rec = tk.Frame(right, bg=BG, pady=4)
        rec.pack(fill='x')

        self._rec_btn = tk.Button(
            rec, text='▶  Start Recording',
            command=self._toggle_recording,
            bg='#1a5500', fg=FG, relief='flat',
            font=('Arial', 9, 'bold'), cursor='hand2', padx=10, pady=4,
        )
        self._rec_btn.pack(side='left', padx=(0, 8))

        self._timer_lbl = tk.Label(rec, text='00:00.0', bg=BG, fg=FG2,
                                    font=('Courier', 10, 'bold'), width=7)
        self._timer_lbl.pack(side='left', padx=(0, 8))

        self._status_lbl = tk.Label(rec, text='Click ▶ Start Recording then click buttons below.',
                                     bg=BG, fg=FG2, font=('Arial', 8))
        self._status_lbl.pack(side='left')

        # Test buttons — right-aligned in the same row
        self._test_stop_btn = tk.Button(
            rec, text='■  Stop Test',
            command=self._stop_test,
            bg='#444444', fg=FG, relief='flat',
            font=('Arial', 9, 'bold'), cursor='hand2', padx=8, pady=4,
            state='disabled',
        )
        self._test_stop_btn.pack(side='right', padx=(4, 0))

        self._test_btn = tk.Button(
            rec, text='▶  Test Steps',
            command=self._start_test,
            bg='#1a4455', fg=FG, relief='flat',
            font=('Arial', 9, 'bold'), cursor='hand2', padx=8, pady=4,
        )
        self._test_btn.pack(side='right', padx=(0, 4))

        # Button grid for recording (2 rows × 8 columns)
        btn_grid = tk.Frame(right, bg=BG)
        btn_grid.pack(fill='x', pady=(0, 4))

        self._rec_btns = {}
        COLS = 8
        for i, (label, cmd) in enumerate(BTN_DEFS):
            r, c = divmod(i, COLS)
            b = tk.Button(
                btn_grid, text=label, width=5,
                command=lambda l=label, c_=cmd: self._record_button(l, c_),
                bg=BG3, fg=FG, relief='flat',
                font=('Arial', 9, 'bold'), cursor='hand2',
                padx=2, pady=5, state='disabled',
            )
            b.grid(row=r, column=c, padx=1, pady=1, sticky='ew')
            self._rec_btns[cmd] = b

        # Screen calibration option row (above video canvas)
        cal_opt_row = tk.Frame(right, bg=BG, pady=2)
        cal_opt_row.pack(fill='x')

        tk.Checkbutton(
            cal_opt_row, text='4-corner screen calibration (ask user to click corners every run)',
            variable=self._use_corners_var,
            bg=BG, fg=FG, selectcolor=BG3, activebackground=BG, activeforeground=FG,
            font=('Arial', 8), cursor='hand2',
        ).pack(side='left', padx=(0, 4))

        # Video canvas
        self._canvas = tk.Canvas(
            right, width=CANVAS_W, height=CANVAS_H,
            bg='#000000', highlightthickness=1,
            highlightbackground=BG3, cursor='crosshair',
        )
        self._canvas.pack()
        self._canvas.bind('<ButtonPress-1>',   self._on_drag_start)
        self._canvas.bind('<B1-Motion>',        self._on_drag_move)
        self._canvas.bind('<ButtonRelease-1>', self._on_drag_end)
        self._canvas.bind('<Button-3>',         self._on_drag_cancel)

        self._draw_hint_lbl = tk.Label(right, text='',
                                        bg=BG, fg='#44ee44', font=('Arial', 8))
        self._draw_hint_lbl.pack()

        # ── Bottom export row ──────────────────────────────────────────────
        bot = tk.Frame(self, bg=BG2, padx=8, pady=6)
        bot.pack(fill='x')

        bst = dict(relief='flat', font=('Arial', 9, 'bold'),
                   cursor='hand2', padx=10, pady=4)
        tk.Button(bot, text='Load',   command=self._load, bg=BG3, fg=FG, **bst).pack(side='left', padx=2)
        tk.Button(bot, text='Save',   command=self._save, bg=BG3, fg=FG, **bst).pack(side='left', padx=2)
        tk.Button(bot, text='Build Script', command=self._build_script,
                  bg='#226622', fg=FG, **bst).pack(side='right', padx=2)
        tk.Button(bot, text='Copy AI Prompt', command=self._export_copy,
                  bg=ACCENT, fg=FG, **bst).pack(side='right', padx=2)

    # ── Recording ─────────────────────────────────────────────────────────────

    def _on_method_change(self, _=None):
        """Show/hide method-specific rows based on the selected detect method."""
        mth = self._method_var.get()
        if mth == 'Pixel Count':
            self._px_threshold_row.pack(fill='x', pady=1, before=self._region_lbl)
        else:
            self._px_threshold_row.pack_forget()
        if mth == 'Target Color':
            self._target_color_row.pack(fill='x', pady=1, before=self._region_lbl)
        else:
            self._target_color_row.pack_forget()
            self._fixed_cb.config(state='normal')

    def _toggle_recording(self):
        if not self._recording:
            self._recording       = True
            self._rec_start       = time.time()
            self._last_click_time = self._rec_start
            self._rec_btn.config(text='■  Stop Recording', bg=ACCENT)
            self._status_lbl.config(text='Recording… click the buttons below as you play.')
            for b in self._rec_btns.values():
                b.config(state='normal')
            self._tick_timer()
        else:
            self._recording = False
            if self._timer_id:
                self.after_cancel(self._timer_id)
            self._rec_btn.config(text='▶  Start Recording', bg='#1a5500')
            self._status_lbl.config(
                text=f'Recording stopped.  {len(self._steps)} steps recorded.')
            for b in self._rec_btns.values():
                b.config(state='disabled')

    # ── Test run ───────────────────────────────────────────────────────────────

    def _start_test(self):
        if not self._steps:
            self._status_lbl.config(text='No steps to test — record some first.')
            return
        if not self._controller or not self._controller.is_open():
            self._status_lbl.config(text='Not connected — select a serial port first.')
            return
        if self._recording:
            return   # don't allow test while recording

        self._test_stop.clear()
        self._test_running = True
        self._test_btn.config(state='disabled')
        self._test_stop_btn.config(state='normal')
        self._rec_btn.config(state='disabled')
        self._status_lbl.config(text='Testing…')

        threading.Thread(target=self._test_thread, daemon=True).start()

    def _stop_test(self):
        self._test_stop.set()
        self._status_lbl.config(text='Stopping test…')

    def _test_thread(self):
        steps = list(self._steps)   # snapshot
        skipped = 0

        for idx, s in enumerate(steps):
            if self._test_stop.is_set():
                break

            # Highlight current step in the list
            self.after(0, lambda i=idx: (
                self._step_list.selection_clear(0, 'end'),
                self._step_list.selection_set(i),
                self._step_list.see(i),
            ))

            t = s.get('type')

            if t == 'button':
                cmd = s.get('cmd')
                self.after(0, lambda b=s.get('button', '?'):
                           self._status_lbl.config(text=f'Testing step {idx+1}/{len(steps)}: {b}'))
                try:
                    getattr(self._controller, cmd)()
                except Exception as e:
                    self.after(0, lambda e=e:
                               self._status_lbl.config(text=f'Error: {e}'))
                # Wait the recorded delay after the button press
                delay = s.get('delay', 0.0)
                if delay > 0:
                    end = time.time() + delay
                    while time.time() < end:
                        if self._test_stop.is_set():
                            break
                        time.sleep(0.05)

            elif t == 'wait':
                delay = s.get('delay', 1.0)
                self.after(0, lambda i=idx, d=delay:
                           self._status_lbl.config(
                               text=f'Testing step {i+1}/{len(steps)}: Wait {d:.2f}s'))
                end = time.time() + delay
                while time.time() < end:
                    if self._test_stop.is_set():
                        break
                    time.sleep(0.05)

            elif t == 'random_wait':
                import random
                lo, hi = s.get('min', 0.5), s.get('max', 2.0)
                delay = random.uniform(lo, hi)
                self.after(0, lambda i=idx, d=delay, a=lo, b=hi:
                           self._status_lbl.config(
                               text=f'Testing step {i+1}/{len(steps)}: Random Wait {d:.2f}s  ({a:.2f}–{b:.2f})'))
                end = time.time() + delay
                while time.time() < end:
                    if self._test_stop.is_set():
                        break
                    time.sleep(0.05)

            elif t == 'detect':
                # Detection steps require the full generated script — skip during test
                skipped += 1
                self.after(0, lambda i=idx:
                           self._status_lbl.config(
                               text=f'Testing step {i+1}/{len(steps)}: DETECT — skipped'))
                time.sleep(0.3)

            elif t == 'ldr':
                # LDR steps require hardware — skip during test
                skipped += 1
                thr = s.get('threshold', 500)
                mode_str = '< ' if s.get('mode', 'below') == 'below' else '> '
                self.after(0, lambda i=idx, m=mode_str, v=thr:
                           self._status_lbl.config(
                               text=f'Testing step {i+1}/{len(steps)}: LDR {m}{v} — skipped'))
                time.sleep(0.3)

            elif t == 'block':
                self.after(0, lambda n=s.get('name', 'Block'):
                           self._status_lbl.config(text=f'── {n} ──'))
                time.sleep(0.15)

        def _done():
            self._test_running = False
            self._test_btn.config(state='normal')
            self._test_stop_btn.config(state='disabled')
            self._rec_btn.config(state='normal')
            stopped = self._test_stop.is_set()
            skip_note = f'  ({skipped} detect step(s) skipped)' if skipped else ''
            self._status_lbl.config(
                text=f'Test {"stopped" if stopped else "complete"}.{skip_note}')
            self._step_list.selection_clear(0, 'end')

        self.after(0, _done)

    def _tick_timer(self):
        if self._recording:
            e = time.time() - self._rec_start
            self._timer_lbl.config(text=f'{int(e // 60):02d}:{e % 60:04.1f}')
            self._timer_id = self.after(100, self._tick_timer)

    def _record_button(self, label: str, cmd: str):
        if not self._recording:
            return
        now   = time.time()
        delay = round(now - self._last_click_time, 2)
        self._last_click_time = now

        # The delay is the time AFTER the PREVIOUS step, so update previous step's delay
        if self._steps:
            self._steps[-1]['delay'] = delay

        # Add the new step (delay will be updated by the NEXT click, or left as 0)
        step = {'type': 'button', 'button': label, 'cmd': cmd, 'delay': 0.0, 'note': ''}
        self._steps.append(step)
        self._refresh_list()
        self._step_list.see(tk.END)

        # Fire the Arduino command in a background thread so the UI stays responsive.
        # The controller call blocks until the servo ACKs, so it must not run on the
        # main thread.
        if self._controller and self._controller.is_open():
            def _send(c=cmd):
                try:
                    getattr(self._controller, c)()
                except Exception:
                    pass
            threading.Thread(target=_send, daemon=True).start()

        # Brief colour flash on the clicked button
        btn = self._rec_btns.get(cmd)
        if btn:
            btn.config(bg='#88ee00')
            self.after(150, lambda b=btn: b.config(bg=BG3))

    # ── Step list management ───────────────────────────────────────────────────

    def _refresh_list(self):
        sel = self._step_list.curselection()
        prev = sel[0] if sel else None

        self._step_list.delete(0, tk.END)
        step_num = 0
        for i, s in enumerate(self._steps):
            if s.get('type') == 'block':
                self._step_list.insert(tk.END, f'  {self._fmt_step(s)}')
                self._step_list.itemconfig(i, fg='#88ccff', selectforeground='#ffffff')
            else:
                step_num += 1
                self._step_list.insert(tk.END, f'  {step_num:3d}.  {self._fmt_step(s)}')

        self._step_count_lbl.config(text=f'Steps ({len(self._steps)})')

        if prev is not None and prev < len(self._steps):
            self._step_list.selection_set(prev)

    def _fmt_step(self, s: dict) -> str:
        t    = s.get('type', '?')
        note = f'  # {s["note"]}' if s.get('note') else ''
        if t == 'block':
            bname = s.get('name', 'Block')
            bar   = '═' * max(2, 30 - len(bname))
            return f'══ {bname} {bar}'
        elif t == 'button':
            return f'[{s.get("button", "?"):5s}]  wait={s.get("delay", 0):.2f}s{note}'
        elif t == 'wait':
            return f'[Wait ]  {s.get("delay", 0):.2f}s{note}'
        elif t == 'random_wait':
            return f'[RandW]  {s.get("min", 0):.2f}–{s.get("max", 0):.2f}s{note}'
        elif t == 'ldr':
            arrow = '↓' if s.get('mode', 'below') == 'below' else '↑'
            return f'[LDR{arrow} ]  thr={s.get("threshold", 500)}  win={s.get("window", 10):.1f}s{note}'
        elif t == 'detect':
            r   = s.get('region')
            loc = f'  ({r[0]},{r[1]} {r[2]}×{r[3]})' if r else '  (no region)'
            mth = s.get('method', 'avg_rgb')
            if mth == 'pixel_count':
                mtag = f'px≥{s.get("px_threshold", 10)}'
            elif mth == 'target_color':
                tc = s.get('target_color')
                tcstr = (f'#{int(tc[0]):02x}{int(tc[1]):02x}{int(tc[2]):02x}'
                         if tc else 'no-color')
                mtag = f'tc:{tcstr}≥{s.get("px_threshold", 10)}'
            else:
                mtag = 'avg'
            ftag = '[F]' if s.get('fixed', False) or mth == 'target_color' else '[C]'
            return f'{ftag}[DETCT {mtag}]  win={s.get("window", 6):.1f}s  tol={s.get("tolerance", 25)}{loc}{note}'
        return f'[{t}]{note}'

    def _on_step_select(self, _=None):
        sel = self._step_list.curselection()
        if not sel:
            self._sel_idx = None
            self._compare_btn.config(state='disabled')
            return
        idx = sel[0]
        if idx >= len(self._steps):
            return
        self._sel_idx = idx
        s = self._steps[idx]
        t = s.get('type')

        if t == 'block':
            self._block_name_var.set(s.get('name', ''))
            self._block_name_entry.config(state='normal')
            self._delay_var.set(0.0)
            self._note_var.set('')
            self._region_lbl.config(text='Region: —')
            self._baseline_lbl.config(text='Baseline: —')
            self._compare_btn.config(state='disabled')
        else:
            self._block_name_var.set('')
            self._block_name_entry.config(state='disabled')
            self._delay_var.set(round(s.get('delay', 0.0), 3))
            self._note_var.set(s.get('note', ''))
            self._window_var.set(s.get('window', 6.0))
            self._tol_var.set(s.get('tolerance', 25))
            if t == 'detect':
                self._rw_row.pack_forget()
                self._ldr_row.pack_forget()
                self._ldr_test_btn.pack_forget()
                r  = s.get('region')
                bl = s.get('baseline')
                self._region_lbl.config(
                    text=f'Region: x={r[0]} y={r[1]} w={r[2]} h={r[3]}' if r else 'Region: not set')
                self._baseline_lbl.config(
                    text=f'Baseline: R={bl[0]:.1f}  G={bl[1]:.1f}  B={bl[2]:.1f}' if bl else 'Baseline: not sampled')
                mth = s.get('method', 'avg_rgb')
                if mth == 'pixel_count':
                    self._method_var.set('Pixel Count')
                elif mth == 'target_color':
                    self._method_var.set('Target Color')
                else:
                    self._method_var.set('Avg RGB')
                self._px_threshold_var.set(s.get('px_threshold', 10))
                self._fixed_var.set(s.get('fixed', False))
                # Update target colour swatch
                tc = s.get('target_color')
                if tc:
                    hx = f'#{int(tc[0]):02x}{int(tc[1]):02x}{int(tc[2]):02x}'
                    self._target_swatch.config(bg=hx)
                    self._target_rgb_lbl.config(
                        text=f'Target: R={tc[0]:.0f}  G={tc[1]:.0f}  B={tc[2]:.0f}')
                else:
                    self._target_swatch.config(bg='#888888')
                    self._target_rgb_lbl.config(
                        text='Target: not set — pick via Compare Shiny Image')
                self._on_method_change()
                self._compare_btn.config(state='normal')
            elif t == 'random_wait':
                self._rw_min_var.set(round(s.get('min', 0.5), 3))
                self._rw_max_var.set(round(s.get('max', 2.0), 3))
                self._rw_row.pack(fill='x', pady=1, before=self._note_row)
                self._ldr_row.pack_forget()
                self._ldr_test_btn.pack_forget()
                self._region_lbl.config(text='Region: —')
                self._baseline_lbl.config(text='Baseline: —')
                self._method_var.set('Avg RGB')
                self._fixed_var.set(False)
                self._px_threshold_row.pack_forget()
                self._compare_btn.config(state='disabled')
            elif t == 'ldr':
                self._ldr_mode_var.set('Below' if s.get('mode', 'below') == 'below' else 'Above')
                self._ldr_threshold_var.set(s.get('threshold', 500))
                self._window_var.set(s.get('window', 10.0))
                self._rw_row.pack_forget()
                self._ldr_row.pack(fill='x', pady=1, before=self._note_row)
                self._ldr_test_btn.pack(pady=(2, 0), anchor='w', before=self._note_row)
                self._region_lbl.config(text='Region: —')
                self._baseline_lbl.config(text='Baseline: —')
                self._method_var.set('Avg RGB')
                self._fixed_var.set(False)
                self._px_threshold_row.pack_forget()
                self._compare_btn.config(state='disabled')
            else:
                self._rw_row.pack_forget()
                self._ldr_row.pack_forget()
                self._ldr_test_btn.pack_forget()
                self._region_lbl.config(text='Region: —')
                self._baseline_lbl.config(text='Baseline: —')
                self._method_var.set('Avg RGB')
                self._fixed_var.set(False)
                self._px_threshold_row.pack_forget()
                self._compare_btn.config(state='disabled')

    def _apply_edit(self):
        if self._sel_idx is None or self._sel_idx >= len(self._steps):
            messagebox.showinfo('Select a step', 'Click a step in the list first.',
                                parent=self)
            return
        s = self._steps[self._sel_idx]
        if s.get('type') == 'block':
            s['name'] = self._block_name_var.get().strip() or 'Block'
            self._refresh_list()
            return
        s['note']  = self._note_var.get()
        if s.get('type') == 'random_wait':
            s['min'] = round(self._rw_min_var.get(), 3)
            s['max'] = round(self._rw_max_var.get(), 3)
        elif s.get('type') == 'ldr':
            s['mode']      = 'below' if self._ldr_mode_var.get() == 'Below' else 'above'
            s['threshold'] = self._ldr_threshold_var.get()
            s['window']    = self._window_var.get()
        else:
            s['delay'] = self._delay_var.get()
        if s.get('type') == 'detect':
            s['window']       = self._window_var.get()
            s['tolerance']    = self._tol_var.get()
            mth_str = self._method_var.get()
            s['method'] = ('pixel_count'  if mth_str == 'Pixel Count'  else
                           'target_color' if mth_str == 'Target Color' else
                           'avg_rgb')
            s['px_threshold'] = self._px_threshold_var.get()
            s['fixed']        = self._fixed_var.get()
            if s['method'] != 'target_color':
                s.pop('target_color', None)   # discard stale target if method changed
        self._refresh_list()
        self._on_step_select()

    def _add_block(self):
        n    = sum(1 for s in self._steps if s.get('type') == 'block') + 1
        step = {'type': 'block', 'name': f'Block {n}'}
        idx  = (self._sel_idx + 1) if self._sel_idx is not None else len(self._steps)
        self._steps.insert(idx, step)
        self._sel_idx = idx
        self._refresh_list()
        self._step_list.selection_set(idx)
        self._on_step_select()
        # Put focus in the name field so the user can rename immediately
        self._block_name_entry.focus_set()
        self._block_name_entry.selection_range(0, 'end')

    def _adjust_timings_dialog(self):
        """Open a small dialog to shift or scale all step delays at once."""
        if not self._steps:
            self._status_lbl.config(text='No steps to adjust.')
            return

        dlg = tk.Toplevel(self)
        dlg.title('Adjust All Delays')
        dlg.configure(bg=BG)
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        pad = dict(padx=10, pady=4)

        tk.Label(dlg, text='Shift all delays by a fixed amount and/or scale them.',
                 bg=BG, fg=FG2, font=('Arial', 8)).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=10, pady=(10, 2))

        def _row(r, label, var, lo, hi, inc):
            tk.Label(dlg, text=label, bg=BG, fg=FG,
                     font=('Arial', 9), width=14, anchor='w').grid(
                row=r, column=0, **pad, sticky='w')
            tk.Spinbox(dlg, from_=lo, to=hi, increment=inc, textvariable=var,
                       width=8, bg=BG2, fg=FG, relief='flat',
                       font=('Courier', 9), buttonbackground=BG3,
                       insertbackground=FG).grid(row=r, column=1, **pad)

        offset_var = tk.DoubleVar(value=0.0)
        scale_var  = tk.DoubleVar(value=1.0)
        _row(1, 'Offset (s):', offset_var, -30.0, 30.0, 0.05)
        tk.Label(dlg, text='Added to every delay  (negative = subtract)',
                 bg=BG, fg=FG2, font=('Arial', 7)).grid(
            row=1, column=2, sticky='w', padx=(0, 10))

        _row(2, 'Scale (×):', scale_var, 0.05, 5.0, 0.05)
        tk.Label(dlg, text='Multiplied after offset  (1.0 = no change)',
                 bg=BG, fg=FG2, font=('Arial', 7)).grid(
            row=2, column=2, sticky='w', padx=(0, 10))

        result_lbl = tk.Label(dlg, text='', bg=BG, fg='#88ee88',
                              font=('Arial', 8))
        result_lbl.grid(row=3, column=0, columnspan=3, padx=10, pady=(2, 0))

        def _apply():
            offset = offset_var.get()
            scale  = scale_var.get()
            changed = 0
            for s in self._steps:
                if 'delay' in s:
                    new_val = max(0.0, round((s['delay'] + offset) * scale, 3))
                    if new_val != s['delay']:
                        s['delay'] = new_val
                        changed += 1
            self._refresh_list()
            # Refresh editor panel if a step is selected
            if self._sel_idx is not None:
                self._on_step_select()
            result_lbl.config(text=f'{changed} delay(s) updated.')
            self._status_lbl.config(
                text=f'All delays adjusted: offset={offset:+.2f}s  ×{scale:.2f}')

        def _reset():
            offset_var.set(0.0)
            scale_var.set(1.0)
            result_lbl.config(text='')

        btn_row = tk.Frame(dlg, bg=BG)
        btn_row.grid(row=4, column=0, columnspan=3, pady=(6, 10))
        bst = dict(relief='flat', font=('Arial', 9, 'bold'),
                   cursor='hand2', padx=10, pady=4)
        tk.Button(btn_row, text='Apply', command=_apply,
                  bg=ACCENT, fg=FG, **bst).pack(side='left', padx=4)
        tk.Button(btn_row, text='Reset fields', command=_reset,
                  bg=BG3, fg=FG, **bst).pack(side='left', padx=4)
        tk.Button(btn_row, text='Close', command=dlg.destroy,
                  bg=BG3, fg=FG, **bst).pack(side='left', padx=4)

        # Centre over the builder dialog
        dlg.update_idletasks()
        pw = self.winfo_x() + (self.winfo_width()  - dlg.winfo_width())  // 2
        ph = self.winfo_y() + (self.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f'+{pw}+{ph}')

    def _add_wait(self):
        step = {'type': 'wait', 'delay': 1.0, 'note': ''}
        idx  = (self._sel_idx + 1) if self._sel_idx is not None else len(self._steps)
        self._steps.insert(idx, step)
        self._refresh_list()
        self._step_list.selection_set(idx)
        self._sel_idx = idx

    def _add_ldr(self):
        step = {'type': 'ldr', 'mode': 'below', 'threshold': 500,
                'window': 10.0, 'interval': 0.1, 'note': ''}
        idx  = (self._sel_idx + 1) if self._sel_idx is not None else len(self._steps)
        self._steps.insert(idx, step)
        self._refresh_list()
        self._step_list.selection_set(idx)
        self._sel_idx = idx
        self._on_step_select()

    def _add_random_wait(self):
        step = {'type': 'random_wait', 'min': 0.5, 'max': 2.0, 'note': ''}
        idx  = (self._sel_idx + 1) if self._sel_idx is not None else len(self._steps)
        self._steps.insert(idx, step)
        self._refresh_list()
        self._step_list.selection_set(idx)
        self._sel_idx = idx
        self._on_step_select()

    def _delete_step(self):
        if self._sel_idx is None or not self._steps:
            return
        del self._steps[self._sel_idx]
        self._sel_idx = min(self._sel_idx, len(self._steps) - 1) if self._steps else None
        self._refresh_list()
        if self._sel_idx is not None:
            self._step_list.selection_set(self._sel_idx)

    def _move_up(self):
        if self._sel_idx is None or self._sel_idx == 0:
            return
        i = self._sel_idx
        self._steps[i - 1], self._steps[i] = self._steps[i], self._steps[i - 1]
        self._sel_idx = i - 1
        self._refresh_list()
        self._step_list.selection_set(self._sel_idx)

    def _move_down(self):
        if self._sel_idx is None or self._sel_idx >= len(self._steps) - 1:
            return
        i = self._sel_idx
        self._steps[i + 1], self._steps[i] = self._steps[i], self._steps[i + 1]
        self._sel_idx = i + 1
        self._refresh_list()
        self._step_list.selection_set(self._sel_idx)

    # ── Detection region drawing ───────────────────────────────────────────────

    def _start_draw(self):
        self._canvas_mode = 'detect'
        self._drag_start = None
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None
        self._draw_hint_lbl.config(
            text='Click and drag on the video to define the detection region.  '
                 'Right-click to cancel.')
        self._status_lbl.config(text='Draw detection region on video…')

    def _open_ldr_test(self):
        if self._sel_idx is None or self._sel_idx >= len(self._steps):
            return
        s = self._steps[self._sel_idx]
        if s.get('type') != 'ldr':
            return
        def _apply(threshold):
            s['threshold'] = threshold
            self._ldr_threshold_var.set(threshold)
            self._refresh_list()
        LdrTestDialog(self, self._controller, s.get('threshold', 500), _apply)

    def _on_builder_close(self):
        self.destroy()

    def _on_drag_start(self, event):
        if not self._canvas_mode:
            return
        self._drag_start = (event.x, event.y)

    def _on_drag_move(self, event):
        if not self._canvas_mode or not self._drag_start:
            return
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
        x0, y0 = self._drag_start
        self._drag_rect_id = self._canvas.create_rectangle(
            x0, y0, event.x, event.y, outline='#00ff00', width=2)

    def _on_drag_end(self, event):
        if not self._canvas_mode or not self._drag_start:
            return

        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y

        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)

        if rw < 4 or rh < 4:
            self._on_drag_cancel(None)
            return

        # Convert canvas coords → frame coords (frame assumed 640×480)
        fx = int(rx * SCALE)
        fy = int(ry * SCALE)
        fw = int(rw * SCALE)
        fh = int(rh * SCALE)

        mode = self._canvas_mode
        self._canvas_mode = None
        self._drag_start  = None
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None
        self._draw_hint_lbl.config(text='')

        # mode == 'detect'
        baseline = None
        if self._grabber:
            frame = self._grabber.get_latest_frame()
            if frame is not None:
                region = frame[fy:fy + fh, fx:fx + fw]
                if region.size > 0:
                    mean = region.mean(axis=(0, 1))   # BGR
                    baseline = [round(float(mean[2]), 1),   # R
                                round(float(mean[1]), 1),   # G
                                round(float(mean[0]), 1)]   # B

        step = {
            'type':         'detect',
            'delay':        0.0,
            'note':         '',
            'region':       [fx, fy, fw, fh],
            'baseline':     baseline or [0.0, 0.0, 0.0],
            'tolerance':    self._tol_var.get(),
            'window':       self._window_var.get(),
            'interval':     0.08,
            'method':       ('pixel_count'
                             if self._method_var.get() == 'Pixel Count'
                             else 'avg_rgb'),
            'px_threshold': self._px_threshold_var.get(),
            'fixed':        True,   # default: use captured region/baseline; uncheck to ask user at runtime
        }

        idx = (self._sel_idx + 1) if self._sel_idx is not None else len(self._steps)
        self._steps.insert(idx, step)
        self._sel_idx = idx

        bl_str = (f'R={baseline[0]}  G={baseline[1]}  B={baseline[2]}'
                  if baseline else 'no frame')
        self._status_lbl.config(
            text=f'Detect step added — region ({fx},{fy} {fw}×{fh})  Baseline: {bl_str}')
        self._refresh_list()
        self._step_list.selection_set(self._sel_idx)
        self._on_step_select()

    def _on_drag_cancel(self, _):
        self._canvas_mode = None
        self._drag_start  = None
        if self._drag_rect_id:
            self._canvas.delete(self._drag_rect_id)
            self._drag_rect_id = None
        self._draw_hint_lbl.config(text='')
        self._status_lbl.config(text='Cancelled.')

    # ── Compare dialog ────────────────────────────────────────────────────────

    def _open_compare(self):
        if self._sel_idx is None or self._sel_idx >= len(self._steps):
            return
        s = self._steps[self._sel_idx]
        if s.get('type') != 'detect':
            return

        def on_apply(region, baseline, method, tol, px_threshold, target_color=None):
            s['region']       = region
            s['baseline']     = baseline
            s['method']       = method
            s['tolerance']    = tol
            s['px_threshold'] = px_threshold
            s['fixed']        = (method == 'target_color')
            if method == 'target_color' and target_color:
                s['target_color'] = target_color
            else:
                s.pop('target_color', None)
            self._refresh_list()
            self._on_step_select()
            if method == 'target_color' and target_color:
                tc = target_color
                self._status_lbl.config(
                    text=f'Detect step updated — Target color: R={tc[0]:.0f} G={tc[1]:.0f} B={tc[2]:.0f}  tol={tol}')
            else:
                self._status_lbl.config(
                    text=f'Detect step updated — Baseline: R={baseline[0]} G={baseline[1]} B={baseline[2]}  method={method}  tol={tol}')

        DetectionCompareDialog(self, self._grabber, s, on_apply=on_apply)

    # ── Video feed ────────────────────────────────────────────────────────────

    def _update_video(self):
        if not self.winfo_exists():
            return

        frame = self._grabber.get_latest_frame() if self._grabber else None
        if frame is not None:
            display = cv2.resize(frame, (CANVAS_W, CANVAS_H))
            # Draw detect regions as blue outlines — suppressed during test
            if not self._test_running:
                for s in self._steps:
                    if s.get('type') == 'detect' and s.get('region'):
                        fx, fy, fw, fh = s['region']
                        cx = int(fx / SCALE)
                        cy = int(fy / SCALE)
                        cw = int(fw / SCALE)
                        ch = int(fh / SCALE)
                        cv2.rectangle(display, (cx, cy), (cx + cw, cy + ch),
                                      (255, 128, 0), 1)
            img = Image.fromarray(cv2.cvtColor(display, cv2.COLOR_BGR2RGB))
            self._photo = ImageTk.PhotoImage(img)
            self._canvas.create_image(0, 0, anchor='nw', image=self._photo)
            if self._drag_rect_id:
                self._canvas.tag_raise(self._drag_rect_id)

        self.after(33, self._update_video)

    # ── Save / Load / Export ──────────────────────────────────────────────────

    def _spec_dict(self) -> dict:
        meta = {
            'console': self._console_var.get(),
            'setup':   self._setup_var.get(),
            'corners': self._use_corners_var.get(),
        }
        return {'meta': meta, 'steps': self._steps}

    def _save(self):
        path = filedialog.asksaveasfilename(
            title='Save Script Spec', defaultextension='.json',
            filetypes=[('JSON', '*.json')], parent=self)
        if not path:
            return
        with open(path, 'w') as f:
            json.dump(self._spec_dict(), f, indent=2)
        self._status_lbl.config(text=f'Saved.')

    def _load(self):
        path = filedialog.askopenfilename(
            title='Load Script Spec', filetypes=[('JSON', '*.json')], parent=self)
        if not path:
            return
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            m = data.get('meta', {})
            self._console_var.set(m.get('console', '3DS'))
            self._setup_var.set(m.get('setup', ''))
            self._steps = data.get('steps', [])
            self._sel_idx = None
            self._use_corners_var.set(m.get('corners', True))
            self._refresh_list()
            self._status_lbl.config(text='Loaded.')
        except Exception as e:
            messagebox.showerror('Load Error', str(e), parent=self)

    def _export_copy(self):
        if not self._steps:
            messagebox.showwarning('No steps', 'Record some steps first.', parent=self)
            return
        text = self._build_ai_prompt()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._status_lbl.config(
            text='AI prompt copied to clipboard — paste into Claude, ChatGPT, or any AI!')

    # ── Direct script builder ─────────────────────────────────────────────────

    def _build_script(self):
        """Generate and save a Python script directly from the recorded spec."""
        import os
        if not self._steps:
            messagebox.showwarning('No steps', 'Record some steps first.', parent=self)
            return

        path = filedialog.asksaveasfilename(
            title='Save Generated Script',
            defaultextension='.py',
            filetypes=[('Python files', '*.py')],
            initialdir=self._scripts_dir,
            parent=self,
        )
        if not path:
            return

        # Derive the script name from the chosen filename
        basename = os.path.splitext(os.path.basename(path))[0]
        name = basename.replace('_', ' ').replace('-', ' ').strip()

        code = self._generate_script_code(name=name)

        save_dir = os.path.dirname(path)
        os.makedirs(save_dir, exist_ok=True)
        # Auto-create __init__.py if saving into a subfolder of scripts/
        if self._scripts_dir and os.path.abspath(save_dir).startswith(
                os.path.abspath(self._scripts_dir)):
            init_path = os.path.join(save_dir, '__init__.py')
            if not os.path.exists(init_path):
                open(init_path, 'w').close()

        with open(path, 'w', encoding='utf-8') as f:
            f.write(code)
        self._status_lbl.config(text=f'Script saved: {os.path.basename(path)}')

    def _generate_script_code(self, name: str = None) -> str:
        """Generate a complete, runnable Python script from the current spec."""
        import re

        m          = self._spec_dict()['meta']
        if not name:
            name = 'My Script'
        console    = m.get('console', '3DS')
        setup      = m.get('setup', '')
        class_name = self._to_class_name(name)
        cal_name   = self._to_cal_name(name)
        has_corners     = bool(m.get('corners', True))
        has_target_color = any(s.get('type') == 'detect' and s.get('method') == 'target_color'
                               for s in self._steps)
        has_fixed_detect = any(s.get('type') == 'detect' and s.get('fixed', False)
                               for s in self._steps)
        has_cal_detect   = any(s.get('type') == 'detect' and not s.get('fixed', False)
                               for s in self._steps)
        has_detect      = has_fixed_detect or has_cal_detect
        has_blocks      = any(s.get('type') == 'block'  for s in self._steps)
        has_avg_rgb     = any(s.get('type') == 'detect' and s.get('method', 'avg_rgb') == 'avg_rgb'
                              for s in self._steps)
        has_pixel_count = any(s.get('type') == 'detect' and s.get('method') == 'pixel_count'
                              for s in self._steps)
        has_cal         = False             # no JSON cal file — region asked once per run, stored in memory
        has_random_wait = any(s.get('type') == 'random_wait' for s in self._steps)
        has_ldr         = any(s.get('type') == 'ldr'         for s in self._steps)

        L = []   # output lines

        # ── Docstring ────────────────────────────────────────────────────────
        L += ['"""', f'{name} — {console}']
        if setup:
            L += ['', f'Setup: {setup}']
        L += ['', 'Auto-generated by GamePRo Script Builder.',
              'Adjust the timing constants at the top of the class to tune the script.',
              '"""', '']

        # ── Imports ──────────────────────────────────────────────────────────
        if has_cal:
            L += ['import json', 'import os', 'import sys']
        if has_random_wait:
            L += ['import random']
        L += ['import time', '', 'from scripts.base_script import BaseScript', '', '']

        # ── Class header ─────────────────────────────────────────────────────
        L += [
            f'class {class_name}(BaseScript):',
            f'    NAME = {repr(name)}',
            f'    DESCRIPTION = "Auto-generated script for {console}."',
            '',
            '    # ── Timing constants (seconds) ──────────────────────────────────────',
        ]

        # ── Pass 1: assign constant names to every non-block step ─────────────
        step_infos   = []   # parallel to self._steps
        detect_count = 0
        step_num     = 0    # counts only non-block steps

        for s in self._steps:
            t = s.get('type')
            if t == 'block':
                step_infos.append({'type': 'block'})
                continue
            step_num += 1
            info = {'type': t, 'step': step_num}
            if t == 'button':
                cmd = s.get('cmd', 'btn')
                info['cmd']   = cmd
                info['cname'] = f'{cmd.replace("press_", "").upper()}_{step_num}_DELAY'
            elif t == 'wait':
                info['cname'] = f'WAIT_{step_num}_DELAY'
            elif t == 'random_wait':
                info['cname_min'] = f'RANDOM_{step_num}_MIN'
                info['cname_max'] = f'RANDOM_{step_num}_MAX'
            elif t == 'ldr':
                info['ldr_n']    = f'LDR_{step_num}'
                info['ldr_mode'] = s.get('mode', 'below')
            elif t == 'detect':
                detect_count += 1
                info['detect_n'] = detect_count
                info['px']       = f'DETECT_{detect_count}'
                info['method']   = s.get('method', 'avg_rgb')
            step_infos.append(info)

        # ── Constant lines ────────────────────────────────────────────────────
        const_lines = []
        for s, info in zip(self._steps, step_infos):
            t    = info['type']
            note = f'   # {s["note"]}' if s.get('note') else ''
            if t == 'block':
                const_lines.append(f"    # ── {s.get('name', 'Block')} ──")
            elif t == 'button':
                const_lines.append(f"    {info['cname']} = {s.get('delay', 0.0):.2f}{note}")
            elif t == 'wait':
                const_lines.append(f"    {info['cname']} = {s.get('delay', 1.0):.2f}{note}")
            elif t == 'random_wait':
                const_lines += [
                    f"    {info['cname_min']} = {s.get('min', 0.5):.2f}   # random delay range{note}",
                    f"    {info['cname_max']} = {s.get('max', 2.0):.2f}",
                ]
            elif t == 'ldr':
                px = info['ldr_n']
                direction = 'below' if info['ldr_mode'] == 'below' else 'above'
                const_lines += [
                    f'    {px}_THRESHOLD = {s.get("threshold", 500)}'
                    f'   # LDR trigger: {direction} this value{note}',
                    f'    {px}_WINDOW    = {s.get("window", 10.0):.1f}'
                    f'   # max seconds to wait for trigger',
                    f'    {px}_INTERVAL  = {s.get("interval", 0.1):.2f}',
                ]
            elif t == 'detect':
                px  = info['px']
                mth = info.get('method', 'avg_rgb')
                const_lines += [
                    f'    {px}_WINDOW    = {s.get("window", 6.0):.1f}{note}',
                    f'    {px}_INTERVAL  = {s.get("interval", 0.08):.2f}',
                    f'    {px}_TOLERANCE = {s.get("tolerance", 25)}',
                ]
                if mth == 'pixel_count':
                    const_lines.append(
                        f'    {px}_PX_THRESHOLD = {s.get("px_threshold", 10)}'
                        f'   # min pixels that must change to trigger')
                if mth == 'target_color':
                    tc = s.get('target_color', [0.0, 0.0, 0.0])
                    const_lines += [
                        f'    {px}_TARGET   = {tc}  # R, G, B target colour (right-click reference image)',
                        f'    {px}_PX_THRESHOLD = {s.get("px_threshold", 10)}'
                        f'   # min matching pixels to trigger',
                    ]
                    if s.get('fixed', False):
                        r = s.get('region', [0, 0, 0, 0])
                        const_lines.append(
                            f'    {px}_REGION   = {r}   # x, y, w, h in frame pixels')
                elif s.get('fixed', False):
                    r  = s.get('region',   [0, 0, 0, 0])
                    bl = s.get('baseline', [0.0, 0.0, 0.0])
                    const_lines += [
                        f'    {px}_REGION   = {r}   # x, y, w, h in frame pixels',
                        f'    {px}_BASELINE = {bl}  # R, G, B at time of script build',
                    ]

        if has_corners and has_detect:
            const_lines += [
                '',
                '    # ── Video panel dimensions (must match VideoPanel constants) ─────────',
                '    _PANEL_W = 640',
                '    _PANEL_H = 480',
            ]

        # ── Step-body generator (shared by flat loop and block methods) ───────
        def gen_steps(s_list, info_list, indent, stop_stmt):
            lines = []
            for s, info in zip(s_list, info_list):
                t    = info['type']
                note = f'   # {s["note"]}' if s.get('note') else ''
                step = info.get('step', '?')
                if t == 'button':
                    val = s.get('delay', 0.0)
                    lines += [
                        f'{indent}# Step {step}: [{s.get("button", "?")}]{note}',
                        f"{indent}controller.{info['cmd']}()",
                    ]
                    if val > 0:
                        lines.append(
                            f"{indent}if not self.wait(self.{info['cname']}, stop_event): {stop_stmt}")
                    lines.append('')
                elif t == 'wait':
                    lines += [
                        f'{indent}# Step {step}: Wait{note}',
                        f"{indent}if not self.wait(self.{info['cname']}, stop_event): {stop_stmt}",
                        '',
                    ]
                elif t == 'random_wait':
                    lines += [
                        f'{indent}# Step {step}: Random Wait{note}',
                        f"{indent}if not self.wait(random.uniform(self.{info['cname_min']}, self.{info['cname_max']}), stop_event): {stop_stmt}",
                        '',
                    ]
                elif t == 'ldr':
                    px   = info['ldr_n']
                    mode = repr(info['ldr_mode'])
                    lines += [
                        f'{indent}# Step {step}: LDR — wait until {"below" if info["ldr_mode"] == "below" else "above"} threshold{note}',
                        f'{indent}result = self._poll_ldr(',
                        f'{indent}    controller, stop_event, {mode}, self.{px}_THRESHOLD,',
                        f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                        f'{indent}if stop_event.is_set(): {stop_stmt}',
                        f'{indent}if result is None:',
                        f"{indent}    log('LDR timeout — no trigger within window.')",
                        f'{indent}    {stop_stmt}',
                        f"{indent}log(f'LDR triggered at {{result}}')",
                        '',
                    ]
                elif t == 'detect':
                    px       = info['px']
                    mth      = info.get('method', 'avg_rgb')
                    is_fixed = s.get('fixed', False)
                    lines += [f'{indent}# Step {step}: Detect{note}']

                    # ── Region / colour setup ──────────────────────────────
                    if is_fixed:
                        # Baked-in region from builder
                        lines += [f'{indent}x, y, w, h = self.{px}_REGION']
                        if mth == 'target_color':
                            lines += [f'{indent}tr, tg, tb = self.{px}_TARGET']
                        else:
                            lines += [f'{indent}br, bg_c, bb = self.{px}_BASELINE']
                        lines.append(f'{indent}tol = self.{px}_TOLERANCE')
                    else:
                        # Cal-mode: ask user once, save to cal file
                        _cal_args = ('request_calibration, frame_grabber, stop_event, warp_info'
                                     if has_corners else
                                     'request_calibration, frame_grabber, stop_event')
                        lines += [
                            f'{indent}if self._detect_cal is None:',
                            f'{indent}    self._do_calibrate({_cal_args})',
                            f'{indent}    if stop_event.is_set(): {stop_stmt}',
                            f"{indent}x, y, w, h = self._detect_cal['region']",
                        ]
                        if mth == 'target_color':
                            lines += [f'{indent}tr, tg, tb = self.{px}_TARGET']
                        else:
                            lines += [f"{indent}br, bg_c, bb = self._detect_cal['baseline']"]
                        lines.append(
                            f"{indent}tol = self._detect_cal.get('tolerance', self.{px}_TOLERANCE)"
                            if mth != 'target_color' else
                            f'{indent}tol = self.{px}_TOLERANCE'
                        )

                    # ── Show overlay box while polling ─────────────────────
                    if has_corners:
                        lines.append(f'{indent}frame_grabber.set_detect_overlay('
                                     f'*self._warp_to_canvas((x, y, w, h), warp_info))')
                    else:
                        lines.append(f'{indent}frame_grabber.set_detect_overlay(x, y, w, h)')

                    # ── Poll call ──────────────────────────────────────────
                    if mth == 'target_color':
                        if has_corners:
                            lines += [
                                f'{indent}result = self._poll_target_color(',
                                f'{indent}    frame_grabber, stop_event, warp_info,',
                                f'{indent}    x, y, w, h, tr, tg, tb, tol,',
                                f'{indent}    self.{px}_PX_THRESHOLD,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]
                        else:
                            lines += [
                                f'{indent}result = self._poll_target_color(',
                                f'{indent}    frame_grabber, stop_event,',
                                f'{indent}    x, y, w, h, tr, tg, tb, tol,',
                                f'{indent}    self.{px}_PX_THRESHOLD,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]
                    elif mth == 'pixel_count':
                        if has_corners:
                            lines += [
                                f'{indent}result = self._poll_pixel_count(',
                                f'{indent}    frame_grabber, stop_event, warp_info,',
                                f'{indent}    x, y, w, h, br, bg_c, bb, tol,',
                                f'{indent}    self.{px}_PX_THRESHOLD,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]
                        else:
                            lines += [
                                f'{indent}result = self._poll_pixel_count(',
                                f'{indent}    frame_grabber, stop_event,',
                                f'{indent}    x, y, w, h, br, bg_c, bb, tol,',
                                f'{indent}    self.{px}_PX_THRESHOLD,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]
                    else:
                        if has_corners:
                            lines += [
                                f'{indent}result = self._poll_region(',
                                f'{indent}    frame_grabber, stop_event, warp_info,',
                                f'{indent}    x, y, w, h, br, bg_c, bb, tol,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]
                        else:
                            lines += [
                                f'{indent}result = self._poll_region(',
                                f'{indent}    frame_grabber, stop_event,',
                                f'{indent}    x, y, w, h, br, bg_c, bb, tol,',
                                f'{indent}    self.{px}_WINDOW, self.{px}_INTERVAL, log)',
                            ]

                    # ── Clear overlay, check result ────────────────────────
                    lines += [
                        f'{indent}frame_grabber.clear_detect_overlay()',
                        f'{indent}if stop_event.is_set(): {stop_stmt}',
                        f'{indent}if result is not None:',
                    ]
                    if mth == 'target_color':
                        lines.append(
                            f"{indent}    log(f'DETECTED! {{result}} px match target colour"
                            f" (threshold: {{self.{px}_PX_THRESHOLD}}).')")
                    elif mth == 'pixel_count':
                        lines.append(
                            f"{indent}    log(f'DETECTED! {{result}} px changed"
                            f" (threshold: {{self.{px}_PX_THRESHOLD}}).')")
                    else:
                        lines.append(
                            f"{indent}    r2, g2, b2 = result")
                        lines.append(
                            f"{indent}    log(f'DETECTED! avg RGB=({{r2:.1f}}, {{g2:.1f}}, {{b2:.1f}})')")
                    lines += [
                        f'{indent}    stop_event.wait()',
                        f'{indent}    {stop_stmt}',
                        '',
                    ]
            return lines

        # ── Build run loop lines + (if blocks) separate block methods ─────────
        if has_blocks:
            # Parse steps into (name, steps, infos) groups
            blocks_parsed = []
            cur_name, cur_steps, cur_infos = None, [], []
            for s, info in zip(self._steps, step_infos):
                if info['type'] == 'block':
                    if cur_name is not None or cur_steps:
                        blocks_parsed.append((cur_name or 'Main', cur_steps, cur_infos))
                    cur_name = s.get('name', 'Block').strip() or 'Block'
                    cur_steps, cur_infos = [], []
                else:
                    cur_steps.append(s)
                    cur_infos.append(info)
            if cur_steps or cur_name is not None:
                blocks_parsed.append((cur_name or 'Main', cur_steps, cur_infos))

            # Unique method names
            used: dict = {}
            block_method_names = []
            for bname, _, _ in blocks_parsed:
                slug = re.sub(r'_+', '_',
                              re.sub(r'[^\w]', '_', bname.lower()).strip('_')) or 'block'
                base = f'_block_{slug}'
                if base in used:
                    used[base] += 1
                    mn = f'{base}_{used[base]}'
                else:
                    used[base] = 0
                    mn = base
                block_method_names.append(mn)

            # run() loop calls each block method
            if has_corners:
                sig      = 'controller, frame_grabber, stop_event, log, request_calibration, warp_info'
                sig_def  = 'controller, frame_grabber, stop_event, log, request_calibration, warp_info'
            else:
                sig      = 'controller, frame_grabber, stop_event, log, request_calibration'
                sig_def  = 'controller, frame_grabber, stop_event, log, request_calibration'
            run_loop_lines = [
                f'            if not self.{mn}({sig}): break'
                for mn in block_method_names
            ] + [
                '            count += 1',
                "            log(f'Attempt {count} complete.')",
            ]

            # Block method bodies
            block_method_lines = []
            for (bname, bsteps, binfos), mn in zip(blocks_parsed, block_method_names):
                block_method_lines += [
                    '',
                    f'    def {mn}(self, {sig_def}):',
                    f'        """Block: {bname}"""',
                ]
                body = gen_steps(bsteps, binfos, indent='        ', stop_stmt='return False')
                block_method_lines += body if body else ['        pass']
                block_method_lines.append('        return True')
        else:
            # Flat loop — no block dividers
            flat_s = [s    for s, i in zip(self._steps, step_infos) if i['type'] != 'block']
            flat_i = [info for info in step_infos                    if info['type'] != 'block']
            run_loop_lines = gen_steps(flat_s, flat_i,
                                       indent='            ', stop_stmt='break') + [
                '            count += 1',
                "            log(f'Attempt {count} complete.')",
            ]
            block_method_lines = []

        # ── Assemble ──────────────────────────────────────────────────────────
        L += const_lines
        L += ['']

        if has_cal_detect:
            L += [
                '    def __init__(self):',
                '        super().__init__()',
                '        self._detect_cal = None',
                '',
            ]

        L += [
            '    def run(self, controller, frame_grabber, stop_event, log, request_calibration):',
            "        log('Script started.')",
        ]
        if has_cal_detect:
            L.append('        self._detect_cal = None  # reset region each run — asked on first cycle')
        if has_corners:
            L += [
                '',
                '        # ── 4-corner screen calibration (every run) ───────────────────────',
                f"        log('Click the four corners of the {console} screen in any order.')",
                '        warp_info = request_calibration(',
                f"            'Click the 4 corners of the {console} screen', mode='corners'",
                '        )',
                '        if stop_event.is_set() or warp_info is None:',
                "            log('Script stopped.')",
                '            return',
                "        log(f'Screen calibrated ({warp_info[\"out_w\"]}×{warp_info[\"out_h\"]} px).')",
            ]
        L += [
            '        count = 0',
            '',
            '        while not stop_event.is_set():',
        ]
        L += run_loop_lines
        L += ['', "        log('Script stopped.')"]

        L += block_method_lines

        if has_cal:
            L += [
                '',
                '    # ── Calibration ──────────────────────────────────────────────────────',
                '',
                '    def _cal_path(self):',
                "        base = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)",
                '                else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))',
                "        cal_dir = os.path.join(base, 'calibration')",
                '        os.makedirs(cal_dir, exist_ok=True)',
                f'        return os.path.join(cal_dir, {repr(cal_name)})',
                '',
                '    def _load_cal(self):',
                '        try:',
                "            with open(self._cal_path(), 'r') as f:",
                '                self._cal = json.load(f)',
                '        except (FileNotFoundError, json.JSONDecodeError):',
                '            self._cal = None',
                '',
                '    def _save_cal(self):',
                "        with open(self._cal_path(), 'w') as f:",
                '            json.dump(self._cal, f)',
            ]
        if has_cal_detect:
            if has_corners:
                L += [
                    '',
                    '    def _do_calibrate(self, request_calibration, frame_grabber, stop_event, warp_info):',
                    '        """Called the first time the detect step is reached."""',
                    '        canvas_rect = request_calibration(',
                    '            "Draw a box around the area to watch for changes.")',
                    '        if stop_event.is_set():',
                    '            return',
                    '        x, y, w, h = self._canvas_to_warp(canvas_rect, warp_info)',
                    '        frame = frame_grabber.get_latest_frame()',
                    '        if frame is None:',
                    '            return',
                    '        frame = self.warp_frame(frame, warp_info)',
                    '        r, g, b = self.avg_rgb(frame, x, y, w, h)',
                    "        self._detect_cal = {'region': [x, y, w, h], 'baseline': [r, g, b], 'tolerance': 25}",
                ]
            else:
                L += [
                    '',
                    '    def _do_calibrate(self, request_calibration, frame_grabber, stop_event):',
                    '        """Called the first time the detect step is reached."""',
                    '        region = request_calibration(',
                    '            "Draw a box around the area to watch for changes.")',
                    '        if stop_event.is_set():',
                    '            return',
                    '        x, y, w, h = region',
                    '        frame = frame_grabber.get_latest_frame()',
                    '        if frame is None:',
                    '            return',
                    '        r, g, b = self.avg_rgb(frame, x, y, w, h)',
                    "        self._detect_cal = {'region': [x, y, w, h], 'baseline': [r, g, b], 'tolerance': 25}",
                ]
        if has_avg_rgb:
            if has_corners:
                L += [
                    '',
                    '    def _poll_region(self, frame_grabber, stop_event, warp_info,',
                    '                     x, y, w, h, br, bg_c, bb, tolerance, window, interval, log=None):',
                    '        """Avg RGB detection — fires when the region average shifts by > tolerance."""',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                frame = self.warp_frame(frame, warp_info)',
                    '                r, g, b = self.avg_rgb(frame, x, y, w, h)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... avg RGB=({r:.1f}, {g:.1f}, {b:.1f})  \'',
                    '                        f\'Δ=({abs(r-br):.1f}, {abs(g-bg_c):.1f}, {abs(b-bb):.1f})  tolerance={tolerance}\')',
                    '                    next_log = time.time() + 2.0',
                    '                if (abs(r-br) > tolerance or abs(g-bg_c) > tolerance',
                    '                        or abs(b-bb) > tolerance):',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        frame2 = self.warp_frame(frame2, warp_info)',
                    '                        r2, g2, b2 = self.avg_rgb(frame2, x, y, w, h)',
                    '                        if (abs(r2-br) > tolerance or abs(g2-bg_c) > tolerance',
                    '                                or abs(b2-bb) > tolerance):',
                    '                            return (r2, g2, b2)',
                    '            time.sleep(interval)',
                    '        return None',
                ]
            else:
                L += [
                    '',
                    '    def _poll_region(self, frame_grabber, stop_event,',
                    '                     x, y, w, h, br, bg_c, bb, tolerance, window, interval, log=None):',
                    '        """Avg RGB detection — fires when the region average shifts by > tolerance."""',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                r, g, b = self.avg_rgb(frame, x, y, w, h)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... avg RGB=({r:.1f}, {g:.1f}, {b:.1f})  \'',
                    '                        f\'Δ=({abs(r-br):.1f}, {abs(g-bg_c):.1f}, {abs(b-bb):.1f})  tolerance={tolerance}\')',
                    '                    next_log = time.time() + 2.0',
                    '                if (abs(r-br) > tolerance or abs(g-bg_c) > tolerance',
                    '                        or abs(b-bb) > tolerance):',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        r2, g2, b2 = self.avg_rgb(frame2, x, y, w, h)',
                    '                        if (abs(r2-br) > tolerance or abs(g2-bg_c) > tolerance',
                    '                                or abs(b2-bb) > tolerance):',
                    '                            return (r2, g2, b2)',
                    '            time.sleep(interval)',
                    '        return None',
                ]
        if has_pixel_count:
            if has_corners:
                L += [
                    '',
                    '    def _poll_pixel_count(self, frame_grabber, stop_event, warp_info,',
                    '                          x, y, w, h, br, bg_c, bb, tolerance,',
                    '                          px_threshold, window, interval, log=None):',
                    '        """Pixel count detection — fires when >= px_threshold pixels deviate',
                    '        from the baseline by more than tolerance (per channel).',
                    '        More sensitive than avg_rgb for localised events like a shiny sparkle.',
                    '        """',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                frame = self.warp_frame(frame, warp_info)',
                    '                n = self.count_matching_pixels(',
                    '                    frame, x, y, w, h, br, bg_c, bb, tolerance)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... {n} px changed  (threshold: {px_threshold}  tolerance: {tolerance})\')',
                    '                    next_log = time.time() + 2.0',
                    '                if n >= px_threshold:',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        frame2 = self.warp_frame(frame2, warp_info)',
                    '                        n2 = self.count_matching_pixels(',
                    '                            frame2, x, y, w, h, br, bg_c, bb, tolerance)',
                    '                        if n2 >= px_threshold:',
                    '                            return n2',
                    '            time.sleep(interval)',
                    '        return None',
                ]
            else:
                L += [
                    '',
                    '    def _poll_pixel_count(self, frame_grabber, stop_event,',
                    '                          x, y, w, h, br, bg_c, bb, tolerance,',
                    '                          px_threshold, window, interval, log=None):',
                    '        """Pixel count detection — fires when >= px_threshold pixels deviate',
                    '        from the baseline by more than tolerance (per channel).',
                    '        More sensitive than avg_rgb for localised events like a shiny sparkle.',
                    '        """',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                n = self.count_matching_pixels(',
                    '                    frame, x, y, w, h, br, bg_c, bb, tolerance)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... {n} px changed  (threshold: {px_threshold}  tolerance: {tolerance})\')',
                    '                    next_log = time.time() + 2.0',
                    '                if n >= px_threshold:',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        n2 = self.count_matching_pixels(',
                    '                            frame2, x, y, w, h, br, bg_c, bb, tolerance)',
                    '                        if n2 >= px_threshold:',
                    '                            return n2',
                    '            time.sleep(interval)',
                    '        return None',
                ]
        if has_target_color:
            if has_corners:
                L += [
                    '',
                    '    def _poll_target_color(self, frame_grabber, stop_event, warp_info,',
                    '                          x, y, w, h, tr, tg, tb, tolerance,',
                    '                          px_threshold, window, interval, log=None):',
                    '        """Target colour detection — fires when >= px_threshold pixels',
                    '        match the target colour (tr, tg, tb) within tolerance.',
                    '        Use for detecting a specific sparkle/highlight colour.',
                    '        """',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                frame = self.warp_frame(frame, warp_info)',
                    '                n = self.count_target_pixels(',
                    '                    frame, x, y, w, h, tr, tg, tb, tolerance)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... {n} px match target  (threshold: {px_threshold}  tolerance: {tolerance})\')',
                    '                    next_log = time.time() + 2.0',
                    '                if n >= px_threshold:',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        frame2 = self.warp_frame(frame2, warp_info)',
                    '                        n2 = self.count_target_pixels(',
                    '                            frame2, x, y, w, h, tr, tg, tb, tolerance)',
                    '                        if n2 >= px_threshold:',
                    '                            return n2',
                    '            time.sleep(interval)',
                    '        return None',
                ]
            else:
                L += [
                    '',
                    '    def _poll_target_color(self, frame_grabber, stop_event,',
                    '                          x, y, w, h, tr, tg, tb, tolerance,',
                    '                          px_threshold, window, interval, log=None):',
                    '        """Target colour detection — fires when >= px_threshold pixels',
                    '        match the target colour (tr, tg, tb) within tolerance.',
                    '        Use for detecting a specific sparkle/highlight colour.',
                    '        """',
                    '        deadline = time.time() + window',
                    '        next_log = time.time() + 2.0',
                    '        while time.time() < deadline:',
                    '            if stop_event.is_set():',
                    '                return None',
                    '            frame = frame_grabber.get_latest_frame()',
                    '            if frame is not None:',
                    '                n = self.count_target_pixels(',
                    '                    frame, x, y, w, h, tr, tg, tb, tolerance)',
                    '                if log and time.time() >= next_log:',
                    '                    log(f\'Watching... {n} px match target  (threshold: {px_threshold}  tolerance: {tolerance})\')',
                    '                    next_log = time.time() + 2.0',
                    '                if n >= px_threshold:',
                    '                    time.sleep(interval * 2)',
                    '                    frame2 = frame_grabber.get_latest_frame()',
                    '                    if frame2 is not None:',
                    '                        n2 = self.count_target_pixels(',
                    '                            frame2, x, y, w, h, tr, tg, tb, tolerance)',
                    '                        if n2 >= px_threshold:',
                    '                            return n2',
                    '            time.sleep(interval)',
                    '        return None',
                ]

        if has_ldr:
            L += [
                '',
                '    def _poll_ldr(self, controller, stop_event, mode, threshold,',
                '                  window, interval, log=None):',
                '        """LDR light-sensor detection.',
                '        mode=\'below\': fires when sensor < threshold (screen darkens).',
                '        mode=\'above\': fires when sensor > threshold (screen brightens).',
                '        Returns the sensor value that triggered, or None on timeout.',
                '        """',
                '        deadline = time.time() + window',
                '        next_log = time.time() + 2.0',
                '        direction = f\'< {threshold}\' if mode == \'below\' else f\'> {threshold}\'',
                '        while time.time() < deadline:',
                '            if stop_event.is_set():',
                '                return None',
                '            val = controller.read_light()',
                '            if log and time.time() >= next_log:',
                "                log(f'LDR: {val}  (waiting for {direction})')",
                '                next_log = time.time() + 2.0',
                '            if mode == \'below\' and val < threshold:',
                '                return val',
                '            if mode == \'above\' and val > threshold:',
                '                return val',
                '            time.sleep(interval)',
                '        return None',
            ]

        if has_corners and has_detect:
            L += [
                '',
                '    # ── Coordinate conversion helpers ─────────────────────────────────────',
                '',
                '    def _canvas_to_warp(self, canvas_rect, warp_info):',
                '        """Convert a rectangle in canvas pixels to warped-frame pixels."""',
                '        cx, cy, cw, ch = canvas_rect',
                '        scale = min(self._PANEL_W / warp_info[\'out_w\'],',
                '                    self._PANEL_H / warp_info[\'out_h\'])',
                '        x_off = (self._PANEL_W - warp_info[\'out_w\'] * scale) / 2',
                '        y_off = (self._PANEL_H - warp_info[\'out_h\'] * scale) / 2',
                '        x = max(0, int((cx - x_off) / scale))',
                '        y = max(0, int((cy - y_off) / scale))',
                '        w = max(1, int(cw / scale))',
                '        h = max(1, int(ch / scale))',
                '        return x, y, w, h',
                '',
                '    def _warp_to_canvas(self, warp_rect, warp_info):',
                '        """Convert a rectangle in warped-frame pixels to canvas pixels."""',
                '        x, y, w, h = warp_rect',
                '        scale = min(self._PANEL_W / warp_info[\'out_w\'],',
                '                    self._PANEL_H / warp_info[\'out_h\'])',
                '        x_off = (self._PANEL_W - warp_info[\'out_w\'] * scale) / 2',
                '        y_off = (self._PANEL_H - warp_info[\'out_h\'] * scale) / 2',
                '        cx = int(x * scale + x_off)',
                '        cy = int(y * scale + y_off)',
                '        cw = max(1, int(w * scale))',
                '        ch = max(1, int(h * scale))',
                '        return cx, cy, cw, ch',
            ]

        return '\n'.join(L) + '\n'

    # ── Name derivation helpers ────────────────────────────────────────────────

    def _to_class_name(self, name: str) -> str:
        """'FRLG Shiny Starter' → 'FRLGShinyStarter'"""
        import re
        words = re.split(r'[\s_\-]+', name.strip())
        return ''.join(w.capitalize() for w in words if w)

    def _to_filename(self, name: str) -> str:
        """'FRLG Shiny Starter' → 'frlg_shiny_starter.py'"""
        import re
        slug = re.sub(r'[\s\-]+', '_', name.strip().lower())
        slug = re.sub(r'[^\w]', '', slug)
        return slug + '.py'

    def _to_cal_name(self, name: str) -> str:
        """'FRLG Shiny Starter' → 'frlg_shiny_starter.json'"""
        return self._to_filename(name).replace('.py', '.json')

    # ── AI prompt builder ──────────────────────────────────────────────────────

    def _build_ai_prompt(self) -> str:
        m          = self._spec_dict()['meta']
        console    = m.get('console', '3DS')
        setup      = m.get('setup', '')
        name       = 'MyScript'
        class_name = self._to_class_name(name)
        filename   = self._to_filename(name)
        cal_name   = self._to_cal_name(name)
        has_corners      = bool(m.get('corners', True))
        has_fixed_detect = any(s.get('type') == 'detect' and     s.get('fixed', False) for s in self._steps)
        has_cal_detect   = any(s.get('type') == 'detect' and not s.get('fixed', False) for s in self._steps)
        has_detect       = has_fixed_detect or has_cal_detect
        has_avg_rgb      = any(s.get('type') == 'detect' and s.get('method', 'avg_rgb') == 'avg_rgb'
                               for s in self._steps)
        has_pixel_count  = any(s.get('type') == 'detect' and s.get('method') == 'pixel_count'
                               for s in self._steps)
        has_cal          = False  # no JSON cal file — region is in-memory only

        file_path = f'scripts/{filename}'

        # ── Spec section ──────────────────────────────────────────────────────
        spec_lines = [f'Console: {console}']
        if setup:
            spec_lines.append(f'Setup:   {setup}')
        if has_corners:
            spec_lines.append('Corners: True  (script prompts user to click 4 screen corners every run)')
        spec_lines.append('')
        spec_lines.append('LOOP STEPS:')

        for i, s in enumerate(self._steps, start=1):
            t    = s.get('type', '?')
            note = f'   # {s["note"]}' if s.get('note') else ''
            if t == 'button':
                spec_lines.append(
                    f'  {i:3d}. [{s.get("button","?"):8s}]  delay_after={s.get("delay",0):.2f}s{note}')
            elif t == 'wait':
                spec_lines.append(
                    f'  {i:3d}. [Wait      ]  duration={s.get("delay",0):.2f}s{note}')
            elif t == 'random_wait':
                spec_lines.append(
                    f'  {i:3d}. [RandomWait]  min={s.get("min",0.5):.2f}s  max={s.get("max",2.0):.2f}s{note}')
            elif t == 'detect':
                r      = s.get('region') or [0, 0, 0, 0]
                bl     = s.get('baseline') or [0, 0, 0]
                mth    = s.get('method', 'avg_rgb')
                is_fix = s.get('fixed', False)
                px_thr_str = (f'  px_threshold={s.get("px_threshold", 10)}'
                              if mth == 'pixel_count' else '')
                fix_str = 'fixed=True (use REGION/BASELINE constants — no user setup)' if is_fix else 'fixed=False (ask user to draw region on first run — calibration)'
                spec_lines.append(
                    f'  {i:3d}. [DETECT    ]  '
                    f'method={mth}  '
                    f'poll_window={s.get("window",6):.1f}s  '
                    f'poll_interval={s.get("interval",0.08):.2f}s  '
                    f'tolerance={s.get("tolerance",25)}'
                    f'{px_thr_str}{note}')
                spec_lines.append(f'          {fix_str}')
                if is_fix:
                    spec_lines.append(
                        f'          Region:   x={r[0]}  y={r[1]}  w={r[2]}  h={r[3]}')
                    spec_lines.append(
                        f'          Baseline: R={bl[0]:.1f}  G={bl[1]:.1f}  B={bl[2]:.1f}')

        spec_lines += ['',
                       'ON DETECT:    stop loop and alert the user',
                       'ON LOOP END:  return to step 1']

        spec_text = '\n'.join(spec_lines)

        # ── Full prompt ───────────────────────────────────────────────────────
        _poll_region_snippet = '''
Detection polling — Avg RGB method (use when spec says method=avg_rgb):
```python
def _poll_region(self, frame_grabber, stop_event,
                 x, y, w, h, br, bg_c, bb, tolerance, window, interval):
    deadline = time.time() + window
    while time.time() < deadline:
        if stop_event.is_set():
            return None
        frame = frame_grabber.get_latest_frame()
        if frame is not None:
            r, g, b = self.avg_rgb(frame, x, y, w, h)
            if abs(r-br)>tolerance or abs(g-bg_c)>tolerance or abs(b-bb)>tolerance:
                time.sleep(interval * 2)
                frame2 = frame_grabber.get_latest_frame()
                if frame2 is not None:
                    r2,g2,b2 = self.avg_rgb(frame2, x, y, w, h)
                    if abs(r2-br)>tolerance or abs(g2-bg_c)>tolerance or abs(b2-bb)>tolerance:
                        return (r2, g2, b2)
        time.sleep(interval)
    return None
```
''' if has_avg_rgb else ''

        _poll_pixel_snippet = '''
Detection polling — Pixel Count method (use when spec says method=pixel_count):
```python
def _poll_pixel_count(self, frame_grabber, stop_event,
                      x, y, w, h, br, bg_c, bb, tolerance,
                      px_threshold, window, interval):
    """Return pixel-count (int) when >= px_threshold changed pixels detected, else None."""
    deadline = time.time() + window
    while time.time() < deadline:
        if stop_event.is_set():
            return None
        frame = frame_grabber.get_latest_frame()
        if frame is not None:
            n = self.count_matching_pixels(frame, x, y, w, h, br, bg_c, bb, tolerance)
            if n >= px_threshold:
                time.sleep(interval * 2)
                frame2 = frame_grabber.get_latest_frame()
                if frame2 is not None:
                    n2 = self.count_matching_pixels(frame2, x, y, w, h, br, bg_c, bb, tolerance)
                    if n2 >= px_threshold:
                        return n2
        time.sleep(interval)
    return None
```

Usage inside the loop (Pixel Count):
```python
# Inside run(), inside the while loop, at the DETECT step (method=pixel_count):
if not self._cal:
    self._do_calibrate(request_calibration, frame_grabber, stop_event)
    if stop_event.is_set(): break
x, y, w, h = self._cal['region']
br, bg_c, bb = self._cal['baseline']
tol = self._cal['tolerance']
result = self._poll_pixel_count(frame_grabber, stop_event,
                                x, y, w, h, br, bg_c, bb, tol,
                                PX_THRESHOLD, WINDOW, INTERVAL)
```
''' if has_pixel_count else ''

        _cal_guard = ('# Ask on first cycle of each run (in-memory — reset when script restarts)\n'
                      'if self._detect_cal is None:\n    ')
        _cal_indent = '    '

        _avg_rgb_cal_usage = f'''
Pattern for using detection region inside the loop (Avg RGB DETECT step):
```python
# Inside run(), inside the while loop, at the DETECT step (method=avg_rgb):
{_cal_guard}{_cal_indent}self._do_calibrate(request_calibration, frame_grabber, stop_event)
{_cal_indent}if stop_event.is_set(): break
x, y, w, h = self._detect_cal['region']
br, bg_c, bb = self._detect_cal['baseline']
tol = self._detect_cal.get('tolerance', self.DETECT_N_TOLERANCE)
result = self._poll_region(frame_grabber, stop_event,
                           x, y, w, h, br, bg_c, bb, tol, WINDOW, INTERVAL)
```
''' if has_avg_rgb and has_cal_detect else ''

        _px_count_cal_usage = f'''
Pattern for using detection region inside the loop (Pixel Count DETECT step):
```python
# Inside run(), inside the while loop, at the DETECT step (method=pixel_count):
{_cal_guard}{_cal_indent}self._do_calibrate(request_calibration, frame_grabber, stop_event)
{_cal_indent}if stop_event.is_set(): break
x, y, w, h = self._detect_cal['region']
br, bg_c, bb = self._detect_cal['baseline']
tol = self._detect_cal.get('tolerance', self.DETECT_N_TOLERANCE)
result = self._poll_pixel_count(frame_grabber, stop_event,
                                x, y, w, h, br, bg_c, bb, tol,
                                self.DETECT_N_PX_THRESHOLD, WINDOW, INTERVAL)
```
''' if has_pixel_count and has_cal_detect else ''

        cal_section = f'''
## Detection region infrastructure (required — this spec includes non-fixed DETECT steps)

{'Non-fixed DETECT steps: user draws region on first cycle of each run (in-memory, not saved to file).' if has_cal_detect else ''}
{'Fixed DETECT steps: region/baseline are class constants — no user input needed.' if has_fixed_detect else ''}

IMPORTANT: You MUST include every helper method that is called (_poll_region,
_poll_pixel_count, _do_calibrate, etc.) as actual indented methods inside the class body.
Use self._detect_cal (NOT self._cal) — it is an instance dict reset at the start of run().

```python
import time

# In __init__:
self._detect_cal = None

# At top of run(), before the loop — reset region each run:
self._detect_cal = None

{'# _do_calibrate — stores region in memory only (not saved to file):' if has_cal_detect else ''}
{'def _do_calibrate(self, request_calibration, frame_grabber, stop_event):' if has_cal_detect else ''}
{'    region = request_calibration("Draw a box around the area to watch for changes.")' if has_cal_detect else ''}
{'    if stop_event.is_set(): return' if has_cal_detect else ''}
{'    x, y, w, h = region' if has_cal_detect else ''}
{'    frame = frame_grabber.get_latest_frame()' if has_cal_detect else ''}
{'    if frame is None: return' if has_cal_detect else ''}
{'    r, g, b = self.avg_rgb(frame, x, y, w, h)' if has_cal_detect else ''}
{'    self._detect_cal = {{"region": [x, y, w, h], "baseline": [r, g, b], "tolerance": 25}}' if has_cal_detect else ''}
```
{_avg_rgb_cal_usage}{_px_count_cal_usage}{_poll_region_snippet}{_poll_pixel_snippet}''' if has_cal_detect else ''

        prompt = f'''\
Please generate a complete GamePRo Python automation script from the spec at the \
bottom of this prompt.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GAMEPRO SCRIPTING FRAMEWORK REFERENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GamePRo scripts are Python files that control an Arduino via USB serial to
press physical buttons on a game console.  Scripts live in the scripts/ folder
and are auto-discovered by the GUI — no registration needed.

## Script skeleton

```python
import time
from scripts.base_script import BaseScript

class MyScript(BaseScript):
    NAME = "Display Name"          # shown in the GUI script list
    DESCRIPTION = "Short description."

    # ── Timing constants (seconds) — one per delay in the loop ──
    STEP_1_DELAY = 2.5
    # ...

    def run(self, controller, frame_grabber, stop_event, log, request_calibration):
        """
        Called in a background thread by the GUI.
        Must return (or check stop_event) to stop cleanly.
        """
        log("Script started.")
        count = 0

        while not stop_event.is_set():
            # press a button
            controller.press_a()
            if not self.wait(self.STEP_1_DELAY, stop_event): break

            # ... more steps ...

            count += 1
            log(f"Attempt {{count}} complete.")

        log("Script stopped.")
```

## controller methods (press, hold, release)

```
press_a()      press_b()      press_x()      press_y()
press_up()     press_down()   press_left()   press_right()
hold_up()      hold_down()    hold_left()    hold_right()
release_all()
soft_reset()    # Home / SR1 button
soft_reset_z()  # SR2 button
wonder_trade()  # + button (Switch) or W button
```

Each call blocks until the Arduino ACKs (servo finished), so there is no
need to add extra sleep for the button press itself — only add wait() for
the time needed for the GAME to respond after the press.

## BaseScript helper methods (call on self)

```python
self.wait(seconds, stop_event) -> bool
    # Sleeps for `seconds`, checks stop_event every 50 ms.
    # Returns True if completed normally, False if Stop was pressed.
    # ALWAYS use:  if not self.wait(N, stop_event): break

self.avg_rgb(frame, x, y, w, h) -> (R, G, B)
    # Returns the average colour of a rectangular region in a BGR numpy frame.
    # frame comes from frame_grabber.get_latest_frame() — may be None.
```

## frame_grabber

```python
frame = frame_grabber.get_latest_frame()   # BGR numpy array, or None
```

## request_calibration

```python
region = request_calibration("Instruction shown to user")
# Blocks the script thread; user draws a rectangle on the live video.
# Returns (x, y, w, h) in frame pixels.
# Returns (0, 0, 1, 1) if Stop was pressed — check stop_event immediately after.
```
{cal_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SCRIPT SPEC (recorded by user)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{spec_text}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GENERATION INSTRUCTIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate a complete, working Python script with these requirements:

1. File path:   {file_path}

2. Class name: {class_name}  (inherits from BaseScript)

3. Define every delay_after value as a named class constant in seconds
   (e.g.  HOME_DELAY = 2.50).  Use descriptive names matching the note/button.

4. The main while-loop must follow the step sequence exactly, in order.

5. {('Include _load_cal / _save_cal / _cal_path methods.  Call _load_cal() once at the top of run().' + (' For fixed=True DETECT steps: read region/baseline directly from the DETECT_N_REGION / DETECT_N_BASELINE class constants — no user setup needed.' if has_fixed_detect else '') + (' For fixed=False DETECT steps: call _do_calibrate() lazily inside the loop at the DETECT step, only if self._cal has no region key.  Use the polling pattern matching method= (avg_rgb → _poll_region, pixel_count → _poll_pixel_count).' if has_cal_detect else '')) if has_cal else 'No calibration or detect steps — no cal infrastructure needed.'}

6. After EVERY wait() call check:  if not self.wait(...): break
   Also check stop_event.is_set() immediately after any detect step.

7. On detection: log the RGB values, the attempt count, then pause with
   stop_event.wait() so the user can act, then break.

8. Log the attempt/reset count at the end of each loop iteration.

9. Include a module-level docstring explaining: game, setup instructions,
   what the detection looks for, and how to tune the timing constants.

10. Output ONLY the Python code — no prose explanation.

--- JSON SPEC (machine-readable) ---
{json.dumps(self._spec_dict(), indent=2)}
'''
        return prompt


# ── Detection comparison dialog ────────────────────────────────────────────────

class DetectionCompareDialog(tk.Toplevel):
    """
    Side-by-side comparison: webcam baseline (normal state) vs a reference
    shiny image.  Lets the user visually verify that the detection region
    will respond to the colour change in a shiny encounter before running
    the script.

    Both canvases show the same detection region as a green rectangle.
    Dragging on either canvas resets the region on both.
    The stats bar shows Δ RGB and whether the change would trigger detection.
    """

    CW = 480   # canvas width  (matches main builder canvas)
    CH = 360   # canvas height
    SC = 640 / 480  # canvas-pixel → frame-pixel multiplier

    def __init__(self, parent, frame_grabber, step: dict, on_apply=None):
        super().__init__(parent)
        self.title('Detection Comparison — Baseline vs Shiny')
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        self._grabber  = frame_grabber
        self._on_apply = on_apply   # callback(region: list, baseline: list)

        r = step.get('region') or [0, 0, 0, 0]
        self._webcam_region = list(r)   # [fx, fy, fw, fh] — set by dragging on webcam canvas
        self._ref_region    = list(r)   # [fx, fy, fw, fh] — set by dragging on ref canvas
        self._tol    = step.get('tolerance', 25)
        self._method = step.get('method', 'avg_rgb')
        self._px_thr = step.get('px_threshold', 10)

        self._frozen       = False      # True = left canvas frozen
        self._webcam_frame = None       # last BGR webcam frame
        self._ref_frame    = None       # reference image as BGR 640×480

        self._dragging   = None   # 'webcam' | 'ref' | None
        self._drag_start = None

        # PhotoImage references (prevents garbage collection)
        self._wcam_photo = None
        self._ref_photo  = None

        # Detection setting vars — editable inside the dialog
        _meth_label = ('Pixel Count'  if self._method == 'pixel_count'  else
                       'Target Color' if self._method == 'target_color' else
                       'Avg RGB')
        self._cmp_method_var  = tk.StringVar(value=_meth_label)
        self._cmp_tol_var     = tk.IntVar(value=self._tol)
        self._cmp_px_var      = tk.IntVar(value=self._px_thr)
        tc = step.get('target_color')
        self._cmp_target_color = list(tc) if tc else None   # [R, G, B] or None

        self._build()
        self._schedule_update()

        self.update_idletasks()
        pw = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        ph = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f'+{pw}+{ph}')
        self.protocol('WM_DELETE_WINDOW', self._on_close)

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build(self):
        tk.Label(
            self, bg=BG2, fg=FG2, font=('Arial', 8), pady=4, wraplength=980,
            text=(
                'Draw a region on either image to set the detection area.  '
                'LEFT = webcam in the normal (pre-shiny) state — this sets the baseline.  '
                'RIGHT = a screenshot of the shiny encounter to compare against.'
            ),
        ).pack(fill='x')

        # ── Two canvases ──────────────────────────────────────────────────
        canvases = tk.Frame(self, bg=BG, padx=6, pady=4)
        canvases.pack(fill='x')

        # Left: webcam (baseline)
        left = tk.Frame(canvases, bg=BG)
        left.pack(side='left', padx=(0, 6))

        lhdr = tk.Frame(left, bg=BG2, padx=4, pady=3)
        lhdr.pack(fill='x')
        tk.Label(lhdr, text='Webcam — Baseline (normal state)',
                 bg=BG2, fg=FG, font=('Arial', 9, 'bold')).pack(side='left')
        self._freeze_btn = tk.Button(
            lhdr, text='❄ Freeze', command=self._toggle_freeze,
            bg=BG3, fg=FG, relief='flat', font=('Arial', 8),
            cursor='hand2', padx=6, pady=1)
        self._freeze_btn.pack(side='right')

        self._wcam_cv = tk.Canvas(
            left, width=self.CW, height=self.CH,
            bg='#111111', highlightthickness=1,
            highlightbackground=BG3, cursor='crosshair')
        self._wcam_cv.pack()
        self._wcam_cv.bind('<ButtonPress-1>',   lambda e: self._drag_start_ev(e, 'webcam'))
        self._wcam_cv.bind('<B1-Motion>',        lambda e: self._drag_move_ev(e, 'webcam'))
        self._wcam_cv.bind('<ButtonRelease-1>', lambda e: self._drag_end_ev(e, 'webcam'))

        self._wcam_lbl = tk.Label(left, text='Region avg: —',
                                   bg=BG, fg=FG2, font=('Courier', 8))
        self._wcam_lbl.pack(anchor='w')

        # Right: reference shiny image
        right = tk.Frame(canvases, bg=BG)
        right.pack(side='left')

        rhdr = tk.Frame(right, bg=BG2, padx=4, pady=3)
        rhdr.pack(fill='x')
        tk.Label(rhdr, text='Reference — Shiny state',
                 bg=BG2, fg=FG, font=('Arial', 9, 'bold')).pack(side='left')
        self._ref_name_lbl = tk.Label(rhdr, text='(no image loaded)',
                                       bg=BG2, fg=FG2, font=('Arial', 8))
        self._ref_name_lbl.pack(side='left', padx=6)
        tk.Button(
            rhdr, text='Load Shiny Image…', command=self._load_ref,
            bg='#226622', fg=FG, relief='flat', font=('Arial', 8),
            cursor='hand2', padx=6, pady=1).pack(side='right')

        self._ref_cv = tk.Canvas(
            right, width=self.CW, height=self.CH,
            bg='#111111', highlightthickness=1,
            highlightbackground=BG3, cursor='crosshair')
        self._ref_cv.pack()
        self._ref_cv.bind('<ButtonPress-1>',   lambda e: self._drag_start_ev(e, 'ref'))
        self._ref_cv.bind('<B1-Motion>',        lambda e: self._drag_move_ev(e, 'ref'))
        self._ref_cv.bind('<ButtonRelease-1>', lambda e: self._drag_end_ev(e, 'ref'))
        self._ref_cv.bind('<Button-3>',         self._pick_target_color)

        self._ref_rgb_lbl = tk.Label(right, text='Region avg: —',
                                      bg=BG, fg=FG2, font=('Courier', 8))
        self._ref_rgb_lbl.pack(anchor='w')

        # ── Stats bar ─────────────────────────────────────────────────────
        stats = tk.Frame(self, bg=BG3, padx=8, pady=5)
        stats.pack(fill='x', padx=6, pady=(0, 4))

        self._delta_lbl = tk.Label(stats, text='Δ R: —   Δ G: —   Δ B: —',
                                    bg=BG3, fg=FG, font=('Courier', 9))
        self._delta_lbl.pack(side='left')

        self._trigger_lbl = tk.Label(stats, text='', bg=BG3, fg=FG,
                                      font=('Arial', 9, 'bold'))
        self._trigger_lbl.pack(side='left', padx=16)

        # ── Target colour row ─────────────────────────────────────────────
        tc_row = tk.Frame(self, bg=BG3, padx=8, pady=4)
        tc_row.pack(fill='x', padx=6, pady=(0, 2))
        self._cmp_target_swatch = tk.Label(
            tc_row, text='   ', bg='#888888', relief='solid', width=3)
        self._cmp_target_swatch.pack(side='left', padx=(0, 6))
        init_tc = self._cmp_target_color
        init_tc_text = (f'Target colour: R={init_tc[0]:.0f}  G={init_tc[1]:.0f}  B={init_tc[2]:.0f}'
                        if init_tc else
                        'Target colour: right-click the reference image to pick a pixel')
        if init_tc:
            hx = f'#{int(init_tc[0]):02x}{int(init_tc[1]):02x}{int(init_tc[2]):02x}'
            self._cmp_target_swatch.config(bg=hx)
        self._cmp_target_lbl = tk.Label(
            tc_row, text=init_tc_text, bg=BG3, fg=FG, font=('Courier', 8))
        self._cmp_target_lbl.pack(side='left')

        # ── Detection settings row ────────────────────────────────────────
        det = tk.Frame(self, bg=BG2, padx=8, pady=5)
        det.pack(fill='x', padx=6, pady=(0, 2))

        def _lbl(text):
            tk.Label(det, text=text, bg=BG2, fg=FG2,
                     font=('Arial', 8)).pack(side='left', padx=(8, 2))

        _lbl('Method:')
        self._cmp_method_combo = ttk.Combobox(
            det, textvariable=self._cmp_method_var,
            values=['Avg RGB', 'Pixel Count', 'Target Color'], width=11, state='readonly')
        self._cmp_method_combo.pack(side='left')
        self._cmp_method_combo.bind('<<ComboboxSelected>>', self._on_cmp_method_change)

        _lbl('Tolerance:')
        tk.Spinbox(det, from_=1, to=255, increment=1, textvariable=self._cmp_tol_var,
                   width=5, bg=BG, fg=FG, relief='flat',
                   font=('Courier', 9), buttonbackground=BG3,
                   insertbackground=FG).pack(side='left')

        self._cmp_px_row = tk.Frame(det, bg=BG2)
        self._cmp_px_row.pack(side='left')
        tk.Label(self._cmp_px_row, text='Px Threshold:', bg=BG2, fg=FG2,
                 font=('Arial', 8)).pack(side='left', padx=(8, 2))
        tk.Spinbox(self._cmp_px_row, from_=1, to=100000, increment=1,
                   textvariable=self._cmp_px_var,
                   width=7, bg=BG, fg=FG, relief='flat',
                   font=('Courier', 9), buttonbackground=BG3,
                   insertbackground=FG).pack(side='left')

        # Show/hide px threshold row based on initial method
        if self._method != 'pixel_count':
            self._cmp_px_row.pack_forget()

        tk.Label(det, text='Changes here are applied to the step when you click Apply.',
                 bg=BG2, fg=FG2, font=('Arial', 7)).pack(side='left', padx=(16, 0))

        # ── Bottom row ────────────────────────────────────────────────────
        bot = tk.Frame(self, bg=BG2, padx=8, pady=6)
        bot.pack(fill='x')

        tk.Button(
            bot, text='Apply to Step', command=self._apply,
            bg=ACCENT, fg=FG, relief='flat', font=('Arial', 9, 'bold'),
            cursor='hand2', padx=10, pady=4).pack(side='left', padx=(0, 8))
        tk.Label(
            bot, text='Saves region, baseline, method and tolerance to the detect step.',
            bg=BG2, fg=FG2, font=('Arial', 8)).pack(side='left')
        tk.Button(
            bot, text='Close', command=self._on_close,
            bg=BG3, fg=FG, relief='flat', font=('Arial', 9),
            cursor='hand2', padx=10, pady=4).pack(side='right')

    # ── Detection settings ────────────────────────────────────────────────────

    def _on_cmp_method_change(self, _=None):
        mth = self._cmp_method_var.get()
        if mth in ('Pixel Count', 'Target Color'):
            self._cmp_px_row.pack(side='left')
        else:
            self._cmp_px_row.pack_forget()

    # ── Reference image loading ───────────────────────────────────────────────

    def _load_ref(self):
        import os
        path = filedialog.askopenfilename(
            parent=self,
            title='Load Reference Shiny Image',
            filetypes=[
                ('Image files', '*.png *.jpg *.jpeg *.bmp *.gif *.webp'),
                ('All files', '*.*'),
            ],
        )
        if not path:
            return
        img = cv2.imread(path)
        if img is None:
            messagebox.showerror('Load Error', f'Could not open:\n{path}', parent=self)
            return
        # Resize to 640×480 so coordinates match webcam frame exactly
        self._ref_frame = cv2.resize(img, (640, 480))
        self._ref_name_lbl.config(text=os.path.basename(path))

    # ── Target colour picker ──────────────────────────────────────────────────

    def _pick_target_color(self, event):
        """Right-click on the reference canvas to pick a target pixel colour."""
        if self._ref_frame is None:
            return
        fx = max(0, min(int(event.x * self.SC), 639))
        fy = max(0, min(int(event.y * self.SC), 479))
        b, g, r = self._ref_frame[fy, fx]
        r, g, b = int(r), int(g), int(b)
        self._cmp_target_color = [float(r), float(g), float(b)]
        hx = f'#{r:02x}{g:02x}{b:02x}'
        self._cmp_target_swatch.config(bg=hx)
        self._cmp_target_lbl.config(
            text=f'Target colour: R={r}  G={g}  B={b}  (picked at canvas {event.x},{event.y})')
        # Auto-switch method to Target Color
        self._cmp_method_var.set('Target Color')
        self._on_cmp_method_change()

    # ── Drag to set region ────────────────────────────────────────────────────

    def _drag_start_ev(self, event, side):
        self._dragging   = side
        self._drag_start = (event.x, event.y)

    def _drag_move_ev(self, event, side):
        if self._dragging != side or not self._drag_start:
            return
        cv = self._wcam_cv if side == 'webcam' else self._ref_cv
        cv.delete('drag_rect')
        x0, y0 = self._drag_start
        cv.create_rectangle(x0, y0, event.x, event.y,
                             outline='#00ff00', width=2, tags='drag_rect')

    def _drag_end_ev(self, event, side):
        if self._dragging != side or not self._drag_start:
            return
        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        cv_done = self._wcam_cv if side == 'webcam' else self._ref_cv
        self._dragging   = None
        self._drag_start = None
        cv_done.delete('drag_rect')
        rx, ry = min(x0, x1), min(y0, y1)
        rw, rh = abs(x1 - x0), abs(y1 - y0)
        if rw < 4 or rh < 4:
            return
        region = [
            int(rx * self.SC),
            int(ry * self.SC),
            int(rw * self.SC),
            int(rh * self.SC),
        ]
        if side == 'webcam':
            self._webcam_region = region
        else:
            self._ref_region = region

    # ── Canvas rendering ──────────────────────────────────────────────────────

    def _schedule_update(self):
        if not self.winfo_exists():
            return
        self._update_canvases()
        self.after(100, self._schedule_update)

    def _update_canvases(self):
        # Webcam side
        if not self._frozen and self._grabber:
            frame = self._grabber.get_latest_frame()
            if frame is not None:
                self._webcam_frame = frame
        if self._webcam_frame is not None:
            self._render(self._wcam_cv, self._webcam_frame, 'wcam', self._webcam_region)
            rgb = self._sample_rgb(self._webcam_frame, self._webcam_region)
            if rgb:
                r, g, b = rgb
                self._wcam_lbl.config(text=f'Region avg: R={r:.1f}  G={g:.1f}  B={b:.1f}')

        # Reference side
        if self._ref_frame is not None:
            self._render(self._ref_cv, self._ref_frame, 'ref', self._ref_region)
            rgb = self._sample_rgb(self._ref_frame, self._ref_region)
            if rgb:
                r, g, b = rgb
                self._ref_rgb_lbl.config(text=f'Region avg: R={r:.1f}  G={g:.1f}  B={b:.1f}')

        self._update_stats()

    def _render(self, cv, frame, photo_attr, region):
        """Render a frame onto a canvas, drawing the region rectangle on top."""
        fx, fy, fw, fh = region
        display = cv2.resize(frame, (self.CW, self.CH))
        if fw > 0 and fh > 0:
            cx  = int(fx / self.SC)
            cy  = int(fy / self.SC)
            cw_ = int(fw / self.SC)
            ch_ = int(fh / self.SC)
            cv2.rectangle(display, (cx, cy), (cx + cw_, cy + ch_), (0, 255, 0), 2)
        img   = Image.fromarray(cv2.cvtColor(display, cv2.COLOR_BGR2RGB))
        photo = ImageTk.PhotoImage(img)
        setattr(self, f'_{photo_attr}_photo', photo)   # prevent GC
        cv.delete('bg_img')
        cv.create_image(0, 0, anchor='nw', image=photo, tags='bg_img')
        if self._dragging:
            cv.tag_raise('drag_rect')

    # ── Stats ─────────────────────────────────────────────────────────────────

    def _sample_rgb(self, frame, region):
        """Return (R, G, B) average of the given region, or None."""
        fx, fy, fw, fh = region
        if fw <= 0 or fh <= 0 or frame is None:
            return None
        crop = frame[fy:fy + fh, fx:fx + fw]
        if crop.size == 0:
            return None
        m = crop.mean(axis=(0, 1))   # BGR order
        return float(m[2]), float(m[1]), float(m[0])

    def _count_changed_pixels(self, tol):
        """Count pixels in the ref region that deviate from the webcam region baseline."""
        wx, wy, ww, wh = self._webcam_region
        rx, ry, rw, rh = self._ref_region
        if ww <= 0 or wh <= 0 or rw <= 0 or rh <= 0:
            return 0
        base      = self._webcam_frame[wy:wy + wh, wx:wx + ww].astype(float)
        ref       = self._ref_frame[ry:ry + rh, rx:rx + rw].astype(float)
        base_mean = base.mean(axis=(0, 1))   # scalar baseline from webcam region avg
        diff      = np.abs(ref - base_mean)
        changed   = np.any(diff > tol, axis=2)
        return int(changed.sum())

    def _count_target_pixels_in(self, frame, region):
        """Count pixels in frame/region matching self._cmp_target_color within tolerance."""
        if frame is None or not self._cmp_target_color:
            return 0
        fx, fy, fw, fh = region
        if fw <= 0 or fh <= 0:
            return 0
        tr, tg, tb = self._cmp_target_color
        crop = frame[fy:fy + fh, fx:fx + fw].astype(float)
        diff = np.abs(crop - [tb, tg, tr])   # BGR order
        return int(np.all(diff <= self._cmp_tol_var.get(), axis=2).sum())

    def _update_stats(self):
        tol    = self._cmp_tol_var.get()
        method_str = self._cmp_method_var.get()
        method = ('pixel_count'  if method_str == 'Pixel Count'  else
                  'target_color' if method_str == 'Target Color' else
                  'avg_rgb')
        px_thr = self._cmp_px_var.get()

        if method == 'target_color':
            if not self._cmp_target_color:
                self._delta_lbl.config(text='Right-click the reference image to pick a target colour')
                self._trigger_lbl.config(text='', fg=FG)
                return
            n_wcam = self._count_target_pixels_in(self._webcam_frame, self._webcam_region)
            n_ref  = self._count_target_pixels_in(self._ref_frame,    self._ref_region)
            self._wcam_lbl.config(text=f'Target matches (normal): {n_wcam} px')
            self._ref_rgb_lbl.config(text=f'Target matches (shiny): {n_ref} px')
            self._delta_lbl.config(
                text=f'Normal: {n_wcam} px   Shiny: {n_ref} px   threshold: {px_thr}   tol={tol}')
            hit = n_ref >= px_thr
            label = f'WOULD DETECT ✓  ({n_ref} ≥ {px_thr})' if hit else f'No detect ✗  ({n_ref} < {px_thr})'
            self._trigger_lbl.config(text=label, fg='#44ee44' if hit else '#ee4444')
            return

        w_rgb = self._sample_rgb(self._webcam_frame, self._webcam_region)
        r_rgb = self._sample_rgb(self._ref_frame,    self._ref_region)

        if not w_rgb or not r_rgb:
            self._delta_lbl.config(text='Δ R: —   Δ G: —   Δ B: —')
            self._trigger_lbl.config(text='', fg=FG)
            return

        wr, wg, wb = w_rgb
        rr, rg, rb = r_rgb
        dr = abs(rr - wr)
        dg = abs(rg - wg)
        db = abs(rb - wb)
        self._delta_lbl.config(
            text=f'Δ R: {dr:.1f}   Δ G: {dg:.1f}   Δ B: {db:.1f}   (tol={tol})')

        if method == 'pixel_count' and self._ref_frame is not None:
            n   = self._count_changed_pixels(tol)
            hit = n >= px_thr
            label = (f'Changed pixels: {n} / threshold: {px_thr} — '
                     f'{"WOULD DETECT ✓" if hit else "No detect ✗"}')
        else:
            hit   = dr > tol or dg > tol or db > tol
            label = 'WOULD DETECT ✓' if hit else 'No detect ✗'

        self._trigger_lbl.config(text=label, fg='#44ee44' if hit else '#ee4444')

    # ── Freeze toggle ─────────────────────────────────────────────────────────

    def _toggle_freeze(self):
        self._frozen = not self._frozen
        if self._frozen:
            self._freeze_btn.config(text='▶ Live', bg='#1a5500')
        else:
            self._freeze_btn.config(text='❄ Freeze', bg=BG3)

    # ── Apply & Close ─────────────────────────────────────────────────────────

    def _apply(self):
        if self._webcam_frame is None:
            messagebox.showwarning(
                'No Webcam Frame',
                'No webcam frame available to sample the baseline from.',
                parent=self)
            return
        fx, fy, fw, fh = self._webcam_region
        if fw <= 0 or fh <= 0:
            messagebox.showwarning(
                'No Region', 'Draw a detection region on the webcam canvas first.', parent=self)
            return
        crop = self._webcam_frame[fy:fy + fh, fx:fx + fw]
        m    = crop.mean(axis=(0, 1))
        baseline = [round(float(m[2]), 1), round(float(m[1]), 1), round(float(m[0]), 1)]
        method_str = self._cmp_method_var.get()
        method = ('pixel_count'  if method_str == 'Pixel Count'  else
                  'target_color' if method_str == 'Target Color' else
                  'avg_rgb')
        if method == 'target_color' and not self._cmp_target_color:
            messagebox.showwarning(
                'No Target Colour',
                'Right-click the reference image to pick a target colour first.',
                parent=self)
            return
        if self._on_apply:
            self._on_apply(list(self._webcam_region), baseline,
                           method, self._cmp_tol_var.get(), self._cmp_px_var.get(),
                           self._cmp_target_color)
        self._on_close()

    def _on_close(self):
        self.destroy()


# ══════════════════════════════════════════════════════════════════════════════
# LDR Test Dialog
# ══════════════════════════════════════════════════════════════════════════════

class LdrTestDialog(tk.Toplevel):
    """
    Real-time LDR (light-sensor) test window.
    Samples controller.read_light() at ~100 ms intervals, plots the values
    on a scrolling canvas graph and lets the user drag a threshold line to
    set the trigger value, then apply it back to the step.
    """

    GRAPH_W  = 600
    GRAPH_H  = 220
    INTERVAL = 0.10   # seconds between samples
    MAX_PTS  = 300     # keep last N samples visible

    def __init__(self, parent, controller, initial_threshold: int, on_apply=None):
        super().__init__(parent)
        self.title('LDR Sensor Test')
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        self._controller  = controller
        self._on_apply    = on_apply
        self._threshold   = initial_threshold

        self._samples: list[int] = []
        self._running   = False
        self._thread    = None
        self._dragging  = False

        self._thr_var   = tk.IntVar(value=initial_threshold)

        self._build()
        self._draw_graph()

        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.grab_set()

    # ── UI construction ────────────────────────────────────────────────────

    def _build(self):
        pad = dict(padx=6, pady=4)

        # ── Title bar ──
        tk.Label(self, text='LDR Sensor Test', bg=BG,
                 fg=FG, font=('Segoe UI', 12, 'bold')).pack(pady=(10, 2))
        tk.Label(self, text='Sample the light sensor and drag the line to set a threshold.',
                 bg=BG, fg=FG2, font=('Segoe UI', 9)).pack(pady=(0, 6))

        # ── Graph canvas ──
        self._canvas = tk.Canvas(
            self, width=self.GRAPH_W, height=self.GRAPH_H,
            bg='#0a0a1a', highlightthickness=1, highlightbackground=BG3)
        self._canvas.pack(padx=10, pady=(0, 4))
        self._canvas.bind('<ButtonPress-1>',   self._on_drag_start)
        self._canvas.bind('<B1-Motion>',       self._on_drag_move)
        self._canvas.bind('<ButtonRelease-1>', self._on_drag_end)

        # ── Stats bar ──
        stats_fr = tk.Frame(self, bg=BG2)
        stats_fr.pack(fill='x', padx=10, pady=(0, 4))

        self._cur_lbl = tk.Label(stats_fr, text='Current: —', bg=BG2, fg=FG,
                                 font=('Consolas', 10), width=16, anchor='w')
        self._cur_lbl.pack(side='left', **pad)

        self._min_lbl = tk.Label(stats_fr, text='Min: —', bg=BG2, fg='#88ccff',
                                 font=('Consolas', 10), width=12, anchor='w')
        self._min_lbl.pack(side='left', **pad)

        self._max_lbl = tk.Label(stats_fr, text='Max: —', bg=BG2, fg='#ffcc44',
                                 font=('Consolas', 10), width=12, anchor='w')
        self._max_lbl.pack(side='left', **pad)

        self._mid_lbl = tk.Label(stats_fr, text='Mid: —', bg=BG2, fg='#aaffaa',
                                 font=('Consolas', 10), width=14, anchor='w')
        self._mid_lbl.pack(side='left', **pad)

        # ── Threshold row ──
        thr_fr = tk.Frame(self, bg=BG)
        thr_fr.pack(fill='x', padx=10, pady=(0, 6))

        tk.Label(thr_fr, text='Threshold:', bg=BG, fg=FG,
                 font=('Segoe UI', 9)).pack(side='left', padx=(0, 4))

        tk.Spinbox(thr_fr, from_=0, to=1020, textvariable=self._thr_var,
                   width=6, bg=BG2, fg=FG, insertbackground=FG,
                   command=self._on_thr_spinbox).pack(side='left')

        tk.Label(thr_fr, text='(drag red line on graph)',
                 bg=BG, fg=FG2, font=('Segoe UI', 8)).pack(side='left', padx=(8, 0))

        # ── Suggest midpoint button ──
        self._suggest_btn = tk.Button(
            thr_fr, text='↕ Suggest Midpoint', bg=BG3, fg=FG,
            relief='flat', font=('Segoe UI', 8),
            command=self._suggest_midpoint)
        self._suggest_btn.pack(side='right', padx=(0, 0))

        # ── Control buttons ──
        btn_fr = tk.Frame(self, bg=BG)
        btn_fr.pack(pady=(0, 10))

        self._start_btn = tk.Button(
            btn_fr, text='▶ Start', width=10, bg='#1a5500', fg=FG,
            relief='flat', font=('Segoe UI', 9, 'bold'),
            command=self._start)
        self._start_btn.pack(side='left', padx=4)

        self._stop_btn = tk.Button(
            btn_fr, text='■ Stop', width=10, bg=BG3, fg=FG,
            relief='flat', font=('Segoe UI', 9),
            state='disabled', command=self._stop)
        self._stop_btn.pack(side='left', padx=4)

        self._apply_btn = tk.Button(
            btn_fr, text='✔ Apply to Step', width=14, bg=ACCENT, fg=FG,
            relief='flat', font=('Segoe UI', 9, 'bold'),
            command=self._apply)
        self._apply_btn.pack(side='left', padx=4)

        tk.Button(btn_fr, text='Close', width=8, bg=BG3, fg=FG,
                  relief='flat', font=('Segoe UI', 9),
                  command=self._on_close).pack(side='left', padx=4)

    # ── Sampling thread ────────────────────────────────────────────────────

    def _start(self):
        if self._running or self._controller is None:
            return
        self._running = True
        self._start_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._thread = threading.Thread(target=self._sample_loop, daemon=True)
        self._thread.start()

    def _stop(self):
        self._running = False
        self._start_btn.config(state='normal')
        self._stop_btn.config(state='disabled')

    def _sample_loop(self):
        while self._running:
            try:
                val = self._controller.read_light()
            except Exception:
                val = 0
            self._samples.append(int(val))
            if len(self._samples) > self.MAX_PTS:
                self._samples = self._samples[-self.MAX_PTS:]
            # schedule UI update on main thread
            try:
                self.after(0, self._refresh_ui)
            except Exception:
                break
            time.sleep(self.INTERVAL)

    # ── Graph drawing ──────────────────────────────────────────────────────

    def _refresh_ui(self):
        self._draw_graph()
        self._update_stats()

    def _draw_graph(self):
        c = self._canvas
        c.delete('all')
        w, h = self.GRAPH_W, self.GRAPH_H
        PAD_L, PAD_R, PAD_T, PAD_B = 40, 10, 10, 20

        gw = w - PAD_L - PAD_R
        gh = h - PAD_T - PAD_B

        # Background grid lines (horizontal at 0, 255, 510, 765, 1020)
        for raw_v in range(0, 1021, 255):
            gy = PAD_T + gh - int(raw_v / 1020 * gh)
            c.create_line(PAD_L, gy, w - PAD_R, gy, fill='#1e2e5a', dash=(3, 4))
            c.create_text(PAD_L - 4, gy, text=str(raw_v), fill='#445588',
                          font=('Consolas', 7), anchor='e')

        # Axis
        c.create_line(PAD_L, PAD_T, PAD_L, h - PAD_B, fill='#334477')
        c.create_line(PAD_L, h - PAD_B, w - PAD_R, h - PAD_B, fill='#334477')

        # Plot samples
        pts = self._samples
        if len(pts) >= 2:
            xs = [PAD_L + int(i / max(len(pts) - 1, 1) * gw) for i in range(len(pts))]
            ys = [PAD_T + gh - int(v / 1020 * gh) for v in pts]
            for i in range(len(pts) - 1):
                c.create_line(xs[i], ys[i], xs[i+1], ys[i+1],
                               fill='#44aaff', width=1)
            # Current value dot
            c.create_oval(xs[-1]-3, ys[-1]-3, xs[-1]+3, ys[-1]+3,
                          fill='#ffffff', outline='')

        # Threshold line
        thr = max(0, min(1020, self._thr_var.get()))
        ty = PAD_T + gh - int(thr / 1020 * gh)
        c.create_line(PAD_L, ty, w - PAD_R, ty, fill='#ff4444', width=2)
        c.create_text(w - PAD_R - 2, ty - 6, text=f'{thr}',
                      fill='#ff8888', font=('Consolas', 8), anchor='e')

        # Store graph geometry for drag conversion
        self._graph_geom = (PAD_L, PAD_T, gw, gh)

    def _update_stats(self):
        pts = self._samples
        if not pts:
            return
        cur = pts[-1]
        mn  = min(pts)
        mx  = max(pts)
        mid = (mn + mx) // 2
        self._cur_lbl.config(text=f'Current: {cur}')
        self._min_lbl.config(text=f'Min: {mn}')
        self._max_lbl.config(text=f'Max: {mx}')
        self._mid_lbl.config(text=f'Mid: {mid}')

    # ── Threshold drag ─────────────────────────────────────────────────────

    def _y_to_ldr(self, y):
        if not hasattr(self, '_graph_geom'):
            return self._thr_var.get()
        PAD_L, PAD_T, gw, gh = self._graph_geom
        raw = (PAD_T + gh - y) / gh * 1020
        return max(0, min(1020, int(raw)))

    def _on_drag_start(self, event):
        self._dragging = True
        self._set_threshold(self._y_to_ldr(event.y))

    def _on_drag_move(self, event):
        if self._dragging:
            self._set_threshold(self._y_to_ldr(event.y))

    def _on_drag_end(self, event):
        self._dragging = False

    def _set_threshold(self, value: int):
        self._thr_var.set(value)
        self._draw_graph()

    def _on_thr_spinbox(self):
        self._draw_graph()

    def _suggest_midpoint(self):
        pts = self._samples
        if not pts:
            return
        mid = (min(pts) + max(pts)) // 2
        self._set_threshold(mid)

    # ── Apply & Close ──────────────────────────────────────────────────────

    def _apply(self):
        thr = max(0, min(1020, self._thr_var.get()))
        if self._on_apply:
            self._on_apply(thr)

    def _on_close(self):
        self._running = False
        self.destroy()
