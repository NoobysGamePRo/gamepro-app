"""
GamePRo Calibration Dialog.

Allows the user to read the 25 calibration values stored in the Arduino's
EEPROM, edit them, and write them back.  Opens as a modal Toplevel window
from the hardware row 'Calibrate' button.

CSV field order (must match the Arduino sketch exactly):
  leftMax, rightMax, leftrightOff,
  upMax, downMax, updownOff,
  YMax, AMax, AYOff,
  BMax, XMax, BXOff,
  SRMax, SROff, WMax,
  arrowBackoffU, arrowBackoffD, arrowBackoffL, arrowBackoffR,
  arrow_releaseU, arrow_releaseD, arrow_releaseL, arrow_releaseR,
  button_release, prePress
"""

import os
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from core.controller import GameProController

# Brand colours (duplicated from app.py to avoid circular import)
BG       = '#1a2d6b'
BG2      = '#0f1d4a'
BG3      = '#243580'
ACCENT   = '#cc0000'
ACCENT_H = '#aa0000'
FG       = '#ffffff'
FG2      = '#aabbdd'


# ── Field definitions ──────────────────────────────────────────────────────────
# Each entry: (csv_index, label, min, max)
# Groups are rendered as separate columns in the dialog.

_GROUP_DPAD = [
    (0,  'Left Max',    0, 180),
    (1,  'Right Max',   0, 180),
    (2,  'L/R Centre',  0, 180),
    (3,  'Up Max',      0, 180),
    (4,  'Down Max',    0, 180),
    (5,  'U/D Centre',  0, 180),
]

_GROUP_FACE = [
    (6,  'Y Max',       0, 180),
    (7,  'A Max',       0, 180),
    (8,  'A/Y Centre',  0, 180),
    (9,  'B Max',       0, 180),
    (10, 'X Max',       0, 180),
    (11, 'B/X Centre',  0, 180),
    (12, 'SR Max',      0, 180),
    (13, 'SR Centre',   0, 180),
    (14, '+ / WT Max',  0, 180),
]

_GROUP_TIMING = [
    (15, 'Backoff Up',        0,    30),
    (16, 'Backoff Down',      0,    30),
    (17, 'Backoff Left',      0,    30),
    (18, 'Backoff Right',     0,    30),
    (19, 'Release Up (ms)',   0,  2000),
    (20, 'Release Down (ms)', 0,  2000),
    (21, 'Release Left (ms)', 0,  2000),
    (22, 'Release Right(ms)', 0,  2000),
    (23, 'Button Rel. (ms)',  0,  2000),
    (24, 'Pre-Press (ms)',    0,  2000),
]

_GROUPS = [
    ('D-Pad Positions',  _GROUP_DPAD),
    ('Face Buttons',     _GROUP_FACE),
    ('Backoff & Timing', _GROUP_TIMING),
]

_NUM_FIELDS      = 25
_NUM_FIELDS_FULL = 30   # 25 calibration + 5 servo pin numbers

# ── Servo pin assignments ───────────────────────────────────────────────────────
# Indices 25-29 in the extended CSV (firmware v4.6+).
# Order matches servo declaration order in the Arduino sketch.
_SERVO_PIN_LABELS   = ['X / B', 'A / Y', 'Home / SR', 'D-Pad LR', 'D-Pad UD']
_SERVO_PIN_DEFAULTS = [2, 3, 6, 7, 8]


class CalibrationDialog(tk.Toplevel):
    """Modal dialog for reading and writing Arduino EEPROM calibration values."""

    def __init__(self,
                 parent: tk.Tk,
                 controller: GameProController,
                 log_callback: Callable[[str], None]):
        super().__init__(parent)
        self._controller = controller
        self._log = log_callback

        self.title('GamePRo Calibration')
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        # Non-modal — user can interact with the main window while this is open
        # (e.g. test a button press after adjusting a value)

        # Map csv_index → IntVar (one per field)
        self._vars: dict[int, tk.IntVar] = {}
        self._spinboxes: dict[int, tk.Spinbox] = {}
        self._action_btns: list = []   # disabled during read/write
        self._pin_vars: list = []      # IntVar for each servo pin (indices 25-29)
        self._pin_spinboxes: list = [] # Spinbox widgets for pins
        self._pins_enabled = False     # True only when firmware returns 30 fields

        self._build()

        # Centre over parent
        self.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width()  - self.winfo_width())  // 2
        py = parent.winfo_y() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f'+{px}+{py}')

        # Auto-read current values from Arduino on open
        self.after(100, self._read)

    # ── Build UI ───────────────────────────────────────────────────────────────

    def _build(self):
        # Title bar
        hdr = tk.Frame(self, bg=BG, pady=8)
        hdr.pack(fill='x', padx=14)
        tk.Label(hdr, text='Calibration', bg=BG, fg=FG,
                 font=('Arial', 14, 'bold')).pack(side='left')
        tk.Label(hdr, text='— edit servo positions and timing values',
                 bg=BG, fg=FG2, font=('Arial', 9)).pack(side='left', padx=(8, 0))

        # Groups row
        groups_frame = tk.Frame(self, bg=BG)
        groups_frame.pack(fill='x', padx=14, pady=(0, 6))

        for col_idx, (group_name, fields) in enumerate(_GROUPS):
            if col_idx > 0:
                tk.Frame(groups_frame, bg=BG3, width=1).pack(
                    side='left', fill='y', padx=10)

            grp = tk.Frame(groups_frame, bg=BG)
            grp.pack(side='left', anchor='n')

            tk.Label(grp, text=group_name, bg=BG, fg=FG2,
                     font=('Arial', 9, 'bold')).grid(
                         row=0, column=0, columnspan=2,
                         sticky='w', pady=(0, 4))

            for row_idx, (csv_idx, label, lo, hi) in enumerate(fields, start=1):
                var = tk.IntVar(value=0)
                self._vars[csv_idx] = var

                tk.Label(grp, text=label, bg=BG, fg=FG,
                         font=('Arial', 9), anchor='w', width=16).grid(
                             row=row_idx, column=0, sticky='w',
                             padx=(0, 6), pady=2)

                sb = tk.Spinbox(
                    grp,
                    from_=lo, to=hi,
                    textvariable=var,
                    width=6,
                    bg=BG2, fg=FG,
                    buttonbackground=BG3,
                    relief='flat',
                    font=('Courier', 10),
                    highlightthickness=1,
                    highlightcolor=BG3,
                    highlightbackground=BG3,
                )
                sb.grid(row=row_idx, column=1, sticky='w', pady=2)
                self._spinboxes[csv_idx] = sb

        # ── Pin Assignments column ─────────────────────────────────────────────
        tk.Frame(groups_frame, bg=BG3, width=1).pack(side='left', fill='y', padx=10)

        pin_col = tk.Frame(groups_frame, bg=BG)
        pin_col.pack(side='left', anchor='n')

        tk.Label(pin_col, text='Pin Assignments', bg=BG, fg=FG2,
                 font=('Arial', 9, 'bold')).grid(
                     row=0, column=0, columnspan=2, sticky='w', pady=(0, 2))

        self._pin_note_lbl = tk.Label(
            pin_col,
            text='Requires firmware v4.6+',
            bg=BG, fg='#556688', font=('Arial', 7))
        self._pin_note_lbl.grid(row=1, column=0, columnspan=2,
                                 sticky='w', pady=(0, 6))

        for row_idx, (label, default) in enumerate(
                zip(_SERVO_PIN_LABELS, _SERVO_PIN_DEFAULTS), start=2):
            var = tk.IntVar(value=default)
            self._pin_vars.append(var)

            tk.Label(pin_col, text=label, bg=BG, fg=FG,
                     font=('Arial', 9), anchor='w', width=12).grid(
                         row=row_idx, column=0, sticky='w', padx=(0, 6), pady=2)

            sb = tk.Spinbox(
                pin_col, from_=2, to=13,
                textvariable=var,
                width=4,
                bg=BG2, fg=FG,
                buttonbackground=BG3,
                relief='flat',
                font=('Courier', 10),
                highlightthickness=1,
                highlightcolor=BG3,
                highlightbackground=BG3,
                state='disabled',
                disabledbackground=BG2,
                disabledforeground='#445577',
            )
            sb.grid(row=row_idx, column=1, sticky='w', pady=2)
            self._pin_spinboxes.append(sb)

        # Thin separator above buttons
        tk.Frame(self, bg=BG3, height=1).pack(fill='x', padx=14, pady=(6, 0))

        # Button row
        btn_row = tk.Frame(self, bg=BG, pady=8)
        btn_row.pack(padx=14)

        SBS = dict(
            bg=ACCENT, fg=FG,
            activebackground=ACCENT_H, activeforeground=FG,
            relief='flat', bd=0,
            font=('Arial', 10, 'bold'),
            cursor='hand2', padx=12, pady=5,
        )

        read_btn = tk.Button(btn_row, text='↓  Read from Arduino',
                             command=self._read, **SBS)
        read_btn.pack(side='left', padx=(0, 8))
        self._action_btns.append(read_btn)

        save_btn = tk.Button(btn_row, text='↑  Save to Arduino',
                             command=self._save, **SBS)
        save_btn.pack(side='left', padx=(0, 8))
        self._action_btns.append(save_btn)

        tk.Button(
            btn_row, text='Close',
            command=self.destroy,
            bg='#444444', fg=FG,
            activebackground='#666666', activeforeground=FG,
            relief='flat', bd=0,
            font=('Arial', 10),
            cursor='hand2', padx=12, pady=5,
        ).pack(side='left')

    # ── Read ──────────────────────────────────────────────────────────────────

    def _read(self):
        self._set_btns('disabled')
        self._log('Reading calibration from Arduino...')

        def _do():
            try:
                csv = self._controller.read_calibration()
                if csv is None:
                    self.after(0, lambda: self._log(
                        'Calibration read failed — firmware may not support it.'))
                    return
                parts = csv.split(',')
                n = len(parts)
                if n not in (_NUM_FIELDS, _NUM_FIELDS_FULL):
                    self.after(0, lambda: self._log(
                        f'Unexpected response ({n} values, expected {_NUM_FIELDS} or {_NUM_FIELDS_FULL}).'))
                    return
                values = [int(p) for p in parts]
                cal_values  = values[:_NUM_FIELDS]
                pin_values  = values[_NUM_FIELDS:] if n == _NUM_FIELDS_FULL else None
                self.after(0, lambda v=cal_values: self._populate(v))
                self.after(0, lambda pv=pin_values: self._populate_pins(pv))
                msg = ('Calibration loaded (firmware v4.6+ — pin assignments unlocked).'
                       if pin_values is not None
                       else 'Calibration loaded (firmware v4.5 — upgrade to v4.6+ to edit pins).')
                self.after(0, lambda: self._log(msg))
            except Exception as e:
                self.after(0, lambda: self._log(f'Calibration read error: {e}'))
            finally:
                self.after(0, lambda: self._set_btns('normal'))

        threading.Thread(target=_do, daemon=True).start()

    def _populate(self, values: list[int]):
        for csv_idx, val in enumerate(values):
            if csv_idx in self._vars:
                self._vars[csv_idx].set(val)

    def _populate_pins(self, pin_values):
        """Set pin spinboxes. pin_values is a list of 5 ints, or None (old firmware)."""
        if pin_values is not None:
            for i, (var, sb) in enumerate(zip(self._pin_vars, self._pin_spinboxes)):
                if i < len(pin_values):
                    var.set(pin_values[i])
                sb.config(state='normal')
            self._pin_note_lbl.config(
                text='Pin changes saved to Arduino EEPROM.',
                fg=FG2)
            self._pins_enabled = True
        else:
            for sb in self._pin_spinboxes:
                sb.config(state='disabled')
            self._pin_note_lbl.config(
                text='Requires firmware v4.6+',
                fg='#556688')
            self._pins_enabled = False

    # ── Save ──────────────────────────────────────────────────────────────────

    def _save(self):
        # Build CSV: 25 fields for old firmware, 30 fields (+ pins) for v4.6+
        try:
            cal_part = ','.join(str(self._vars[i].get()) for i in range(_NUM_FIELDS))
            if self._pins_enabled:
                pin_part = ','.join(str(v.get()) for v in self._pin_vars)
                csv = cal_part + ',' + pin_part
            else:
                csv = cal_part
        except Exception as e:
            self._log(f'Calibration value error: {e}')
            return

        self._set_btns('disabled')
        self._log('Saving calibration to Arduino...')

        def _do(csv_str=csv):
            try:
                ok = self._controller.write_calibration(csv_str)
                if ok:
                    self.after(0, lambda: self._log(
                        'Calibration saved to Arduino EEPROM.'))
                else:
                    self.after(0, lambda: self._log(
                        'Calibration save failed — no ACK from Arduino.'))
            except Exception as e:
                self.after(0, lambda: self._log(f'Calibration save error: {e}'))
            finally:
                self.after(0, lambda: self._set_btns('normal'))

        threading.Thread(target=_do, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_btns(self, state: str):
        for btn in self._action_btns:
            btn.config(state=state)
