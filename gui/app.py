"""
GamePRo Application — main tkinter window.

Layout:
  ┌──────────────────────────────────────────────┐
  │ [LOGO]  Noobys GamePRo                        │  header
  ├──────────────────────────────────────────────┤
  │ Serial Port: [COM3 ▼] ●  │ Webcam: [0 ▼]  ↺  │  hardware row
  ├────────────────────┬─────────────────────────┤
  │ Scripts            │ Live Preview             │
  │  ├─ Gen 4 HGSS     │ (640×480 @ 30fps)        │
  │  │  └─ Shiny...    │                          │
  │  └─ Generic        │                          │
  │  [▶ Run] [■ Stop]  │                          │
  ├────────────────────┤                          │
  │ Log                │                          │
  │  > ...             │                          │
  └────────────────────┴─────────────────────────┘
  │  www.noobysgamepro.com                        │  footer
  └──────────────────────────────────────────────┘
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import importlib.util
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
import time
import urllib.request
import zipfile
import webbrowser
from typing import Optional

from PIL import Image, ImageTk

from core.controller import GameProController
from core.camera import FrameGrabber
from gui.calibration_dialog import CalibrationDialog
from gui.script_builder import ScriptBuilderDialog
from gui.video_panel import VideoPanel
from scripts.base_script import BaseScript

# ── Brand colours ─────────────────────────────────────────────────────────────
BG       = '#1a2d6b'   # Deep navy (main background)
BG2      = '#0f1d4a'   # Darker navy (panels, inputs)
BG3      = '#243580'   # Mid navy (borders, treeview bg)
ACCENT   = '#cc0000'   # Red (buttons, active states)
ACCENT_H = '#aa0000'   # Red hover
FG       = '#ffffff'   # White
FG2      = '#aabbdd'   # Muted blue-white (labels, headings)

# ── Script update URL ─────────────────────────────────────────────────────────
# Update this URL once the GitHub repository has been created.
# It should point to the ZIP download of the main branch.
# Example: https://github.com/noobysgamepro/gamepro-scripts/archive/refs/heads/main.zip
SCRIPTS_REPO_ZIP = "https://github.com/NoobysGamePRo/gamepro-scripts/archive/refs/heads/main.zip"

# ── Firmware update URLs ──────────────────────────────────────────────────────
# GitHub Releases API for each hardware variant.
# In each repo, publish a Release and attach the compiled .hex as a release asset.
# The app finds the first .hex in the latest release and flashes it.
FIRMWARE_RELEASE_API_3DS    = "https://api.github.com/repos/NoobysGamePRo/gamepro-firmware-3ds/releases/latest"
FIRMWARE_RELEASE_API_SWITCH = "https://api.github.com/repos/NoobysGamePRo/gamepro-firmware-switch/releases/latest"

# ── App version & update check ────────────────────────────────────────────────
APP_VERSION     = 'v1.2'
APP_RELEASE_API = "https://api.github.com/repos/NoobysGamePRo/gamepro-app/releases/latest"
APP_DOWNLOAD_URL = "https://github.com/NoobysGamePRo/gamepro-app/releases/latest"


class ToolTip:
    """Simple hover tooltip for any tkinter widget."""

    def __init__(self, widget, text: str):
        self._widget = widget
        self._text = text
        self._tip = None
        widget.bind('<Enter>', self._show)
        widget.bind('<Leave>', self._hide)

    def _show(self, event=None):
        w = self._widget
        x = w.winfo_rootx() + w.winfo_width() // 2
        y = w.winfo_rooty() + w.winfo_height() + 4
        self._tip = tk.Toplevel(w)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f'+{x}+{y}')
        tk.Label(
            self._tip, text=self._text,
            bg='#ffffcc', fg='#000000',
            relief='solid', borderwidth=1,
            font=('Arial', 9), padx=5, pady=3,
        ).pack()

    def _hide(self, event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


class GameProApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('Noobys GamePRo')
        self.configure(bg=BG)
        self.resizable(False, False)

        self._controller: Optional[GameProController] = None
        self._grabber: Optional[FrameGrabber] = None
        self._script_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._script_map: dict = {}        # tree iid → BaseScript subclass
        self._port_display_map: dict = {}  # display label → actual device name
        self._preview_on: bool = False
        self._manual_btns: list = []       # control panel buttons (enabled/disabled together)
        self._light_value_label: Optional[tk.Label] = None
        self._light_canvas: Optional[tk.Canvas] = None
        self._light_live: bool = False
        self._light_live_btn: Optional[tk.Button] = None
        self._light_live_job = None
        self._firmware_btn_3ds: Optional[ttk.Button] = None
        self._firmware_btn_switch: Optional[ttk.Button] = None
        self._pending_cal_restore: Optional[str] = None  # CSV from pre-flash backup
        self._cal_dialog: Optional[CalibrationDialog] = None
        self._builder_dialog: Optional[ScriptBuilderDialog] = None

        self._setup_style()
        self._build_header()
        self._build_hardware_row()
        self._build_main_area()
        self._build_footer()

        self._populate_ports()
        self._populate_webcams()
        self._populate_scripts()

        self.protocol('WM_DELETE_WINDOW', self._on_close)

        # Check for app update in the background after the window is ready
        self.after(2000, self._check_for_app_update)

    # ── Style ──────────────────────────────────────────────────────────────────

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')

        style.configure('.', background=BG, foreground=FG, font=('Arial', 10))

        # Combobox
        style.configure('TCombobox',
                         fieldbackground=BG2, background=BG2,
                         foreground=FG, selectbackground=BG2,
                         selectforeground=FG, arrowcolor=FG2)
        style.map('TCombobox',
                  fieldbackground=[('readonly', BG2)],
                  background=[('readonly', BG2)])

        # Scrollbar
        style.configure('TScrollbar', background=BG3, troughcolor=BG2,
                         arrowcolor=FG2, bordercolor=BG2, darkcolor=BG2,
                         lightcolor=BG3)

        # Treeview
        style.configure('Treeview', background=BG2, foreground=FG,
                         fieldbackground=BG2, rowheight=26, borderwidth=0)
        style.configure('Treeview.Heading', background=BG, foreground=FG2,
                         font=('Arial', 10, 'bold'))
        style.map('Treeview',
                  background=[('selected', ACCENT)],
                  foreground=[('selected', FG)])

        # Buttons
        style.configure('Run.TButton', background=ACCENT, foreground=FG,
                         font=('Arial', 11, 'bold'), padding=6)
        style.map('Run.TButton', background=[('active', ACCENT_H),
                                              ('disabled', '#555555')])

        style.configure('Stop.TButton', background='#444444', foreground=FG,
                         font=('Arial', 11, 'bold'), padding=6)
        style.map('Stop.TButton', background=[('active', '#666666'),
                                               ('disabled', '#333333')])

        style.configure('Refresh.TButton', background=BG3, foreground=FG2,
                         font=('Arial', 9), padding=2)
        style.map('Refresh.TButton', background=[('active', BG)])

        style.configure('PreviewOff.TButton', background='#333333', foreground='#888888',
                         font=('Arial', 9, 'bold'), padding=3)
        style.map('PreviewOff.TButton', background=[('active', '#444444')])

        style.configure('PreviewOn.TButton', background='#005522', foreground='#00ee66',
                         font=('Arial', 9, 'bold'), padding=3)
        style.map('PreviewOn.TButton', background=[('active', '#007733')])

    # ── Header ─────────────────────────────────────────────────────────────────

    def _build_header(self):
        hdr = tk.Frame(self, bg=BG, pady=8)
        hdr.pack(fill='x', padx=12)

        # Logo
        logo_path = self._asset('logo.png')
        try:
            img = Image.open(logo_path)
            img.thumbnail((130, 65), Image.LANCZOS)
            self._logo_photo = ImageTk.PhotoImage(img)
            tk.Label(hdr, image=self._logo_photo, bg=BG).pack(side='left', padx=(0, 14))
        except Exception:
            tk.Label(hdr, text='NOOBYS GAMEPRO', bg=BG, fg=FG,
                     font=('Arial', 18, 'bold')).pack(side='left', padx=(0, 14))

        title_frame = tk.Frame(hdr, bg=BG)
        title_frame.pack(side='left', anchor='center')
        tk.Label(title_frame, text='GamePRo Controller',
                 bg=BG, fg=FG, font=('Arial', 16, 'bold')).pack(anchor='w')
        tk.Label(title_frame, text='Automated game controller software',
                 bg=BG, fg=FG2, font=('Arial', 9)).pack(anchor='w')

    # ── Hardware row ──────────────────────────────────────────────────────────

    def _build_hardware_row(self):
        row = tk.Frame(self, bg=BG2, pady=7)
        row.pack(fill='x', padx=12, pady=(0, 6))

        # Serial port
        tk.Label(row, text='Serial Port:', bg=BG2, fg=FG,
                 font=('Arial', 10)).pack(side='left', padx=(10, 4))
        self._port_var = tk.StringVar()
        self._port_combo = ttk.Combobox(row, textvariable=self._port_var,
                                         width=36, state='readonly')
        self._port_combo.pack(side='left', padx=2)
        self._port_combo.bind('<<ComboboxSelected>>', self._on_port_selected)

        self._port_dot = tk.Label(row, text='●', bg=BG2, fg='#666666',
                                   font=('Arial', 14))
        self._port_dot.pack(side='left', padx=(2, 6))

        port_refresh = ttk.Button(row, text='↺', style='Refresh.TButton', width=3,
                                   command=self._populate_ports)
        port_refresh.pack(side='left', padx=(0, 20))
        ToolTip(port_refresh, 'Rescan for connected serial ports\n'
                               '(use after plugging in the GamePRo)')

        # Webcam
        tk.Label(row, text='Webcam:', bg=BG2, fg=FG,
                 font=('Arial', 10)).pack(side='left', padx=(0, 4))
        self._cam_var = tk.StringVar()
        self._cam_combo = ttk.Combobox(row, textvariable=self._cam_var,
                                        width=5, state='readonly')
        self._cam_combo.pack(side='left', padx=2)
        self._cam_combo.bind('<<ComboboxSelected>>', self._on_cam_selected)

        cam_refresh = ttk.Button(row, text='↺', style='Refresh.TButton', width=3,
                                  command=self._populate_webcams)
        cam_refresh.pack(side='left', padx=4)
        ToolTip(cam_refresh, 'Rescan for connected webcams\n'
                              '(use after plugging in a camera)')

        # Calibrate button — right-aligned in the hardware row
        self._cal_btn = ttk.Button(row, text='⚙ Calibrate', style='Refresh.TButton',
                                    command=self._open_calibration)
        self._cal_btn.pack(side='right', padx=(0, 4))
        ToolTip(self._cal_btn,
                'Read and edit the servo calibration values\n'
                'stored on the Arduino (requires connection)')

        # Firmware update buttons — right-aligned in the hardware row
        self._firmware_btn_switch = ttk.Button(
            row, text='↑ Switch Firmware', style='Refresh.TButton',
            command=lambda: self._update_firmware(
                'Switch', FIRMWARE_RELEASE_API_SWITCH, self._firmware_btn_switch))
        self._firmware_btn_switch.pack(side='right', padx=(0, 4))
        ToolTip(self._firmware_btn_switch,
                'Download and flash the latest Nintendo Switch\n'
                'firmware to the Arduino on the selected port')

        self._firmware_btn_3ds = ttk.Button(
            row, text='↑ 3DS Firmware', style='Refresh.TButton',
            command=lambda: self._update_firmware(
                '3DS', FIRMWARE_RELEASE_API_3DS, self._firmware_btn_3ds))
        self._firmware_btn_3ds.pack(side='right', padx=(0, 4))
        ToolTip(self._firmware_btn_3ds,
                'Download and flash the latest Nintendo 3DS\n'
                'firmware to the Arduino on the selected port')

    # ── Main area ─────────────────────────────────────────────────────────────

    def _build_main_area(self):
        main = tk.Frame(self, bg=BG)
        main.pack(fill='both', expand=True, padx=12, pady=(0, 4))

        # ── Left panel: script tree + buttons + log
        left = tk.Frame(main, bg=BG, width=290)
        left.pack(side='left', fill='both', expand=False, padx=(0, 8))
        left.pack_propagate(False)

        scripts_hdr = tk.Frame(left, bg=BG)
        scripts_hdr.pack(fill='x', pady=(0, 2))
        tk.Label(scripts_hdr, text='Scripts', bg=BG, fg=FG2,
                 font=('Arial', 10, 'bold')).pack(side='left')
        self._get_scripts_btn = ttk.Button(scripts_hdr, text='↓ Get Scripts',
                                            style='Refresh.TButton',
                                            command=self._check_for_scripts)
        self._get_scripts_btn.pack(side='right')
        ToolTip(self._get_scripts_btn,
                'Download the latest scripts from the\n'
                'Noobys GamePRo community repository')

        self._builder_btn = ttk.Button(scripts_hdr, text='⚒ Script Builder',
                                        style='Refresh.TButton',
                                        command=self._open_script_builder)
        self._builder_btn.pack(side='right', padx=(0, 4))
        ToolTip(self._builder_btn,
                'Record a button sequence and detection regions\n'
                'to generate a script spec for Claude')

        tree_frame = tk.Frame(left, bg=BG2, highlightthickness=1,
                               highlightbackground=BG3)
        tree_frame.pack(fill='both', expand=True)

        self._tree = ttk.Treeview(tree_frame, show='tree', selectmode='browse')
        vsb = ttk.Scrollbar(tree_frame, orient='vertical',
                             command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        # Run / Stop buttons
        btn_row = tk.Frame(left, bg=BG)
        btn_row.pack(fill='x', pady=6)

        self._run_btn = ttk.Button(btn_row, text='▶  Run Script',
                                    style='Run.TButton',
                                    command=self._run_script)
        self._run_btn.pack(side='left', expand=True, fill='x', padx=(0, 4))

        self._stop_btn = ttk.Button(btn_row, text='■  Stop',
                                     style='Stop.TButton',
                                     command=self._stop_script,
                                     state='disabled')
        self._stop_btn.pack(side='left', expand=True, fill='x')

        # Log
        tk.Label(left, text='Log', bg=BG, fg=FG2,
                 font=('Arial', 10, 'bold')).pack(anchor='w', pady=(4, 2))

        log_frame = tk.Frame(left, bg=BG2, highlightthickness=1,
                              highlightbackground=BG3)
        log_frame.pack(fill='both', expand=True)

        self._log_text = tk.Text(log_frame, bg=BG2, fg=FG,
                                  font=('Courier', 9),
                                  state='disabled', wrap='word',
                                  height=10, relief='flat', borderwidth=4)
        log_vsb = ttk.Scrollbar(log_frame, orient='vertical',
                                  command=self._log_text.yview)
        self._log_text.configure(yscrollcommand=log_vsb.set)
        self._log_text.pack(side='left', fill='both', expand=True)
        log_vsb.pack(side='right', fill='y')

        # ── Right panel: video preview
        right = tk.Frame(main, bg=BG)
        right.pack(side='left', fill='both', expand=True)

        preview_hdr = tk.Frame(right, bg=BG)
        preview_hdr.pack(fill='x', pady=(0, 2))

        tk.Label(preview_hdr, text='Live Preview', bg=BG, fg=FG2,
                 font=('Arial', 10, 'bold')).pack(side='left')

        self._preview_btn = ttk.Button(preview_hdr, text='● Preview: OFF',
                                        style='PreviewOff.TButton',
                                        command=self._toggle_preview)
        self._preview_btn.pack(side='right')
        ToolTip(self._preview_btn, 'Toggle the live webcam feed on or off')

        self._video_panel = VideoPanel(right)
        self._video_panel.pack()
        self._video_panel.set_overlay_callback(self._on_video_overlay)

        self._build_manual_controls(right)

    # ── Manual Controls ───────────────────────────────────────────────────────

    def _build_manual_controls(self, parent):
        """
        Build the manual button control panel below the video preview.

        Layout (left → right):
          D-Pad / Joystick  |  A B X Y (round)  |  Special  |  Light Sensor

        D-pad buttons hold while the mouse button is held down and release on
        mouse-up.  ABXY buttons are round canvas widgets (tap only).
        """

        # ── Circular button helper ─────────────────────────────────────────
        class _CircleBtn:
            """Canvas-drawn circular button (used for ABXY face buttons)."""
            SIZE = 44

            def __init__(self, par, text, cmd, color):
                s = self.SIZE
                self.color    = color
                self.color_hi = self._adj(color,  35)
                self.color_lo = self._adj(color, -80)
                self.command  = cmd
                self._enabled = True

                c = tk.Canvas(par, width=s, height=s,
                              bg=par['bg'], highlightthickness=0, cursor='hand2')
                r = s // 2 - 2
                cx = cy = s // 2
                self._oval = c.create_oval(cx - r, cy - r, cx + r, cy + r,
                                           fill=color, outline='')
                self._lbl  = c.create_text(cx, cy, text=text, fill='white',
                                           font=('Arial', 12, 'bold'))
                c.bind('<Button-1>', self._click)
                c.bind('<Enter>',    self._enter)
                c.bind('<Leave>',    self._leave)
                self.canvas = c

            @staticmethod
            def _adj(h, d):
                return '#{:02x}{:02x}{:02x}'.format(
                    max(0, min(255, int(h[1:3], 16) + d)),
                    max(0, min(255, int(h[3:5], 16) + d)),
                    max(0, min(255, int(h[5:7], 16) + d)),
                )

            def _click(self, _):
                if self._enabled:
                    self.command()

            def _enter(self, _):
                if self._enabled:
                    self.canvas.itemconfig(self._oval, fill=self.color_hi)

            def _leave(self, _):
                self.canvas.itemconfig(
                    self._oval,
                    fill=self.color if self._enabled else self.color_lo)

            def config(self, **kw):
                if 'state' in kw:
                    self._enabled = kw['state'] == 'normal'
                    self.canvas.itemconfig(
                        self._oval,
                        fill=self.color if self._enabled else self.color_lo)
                    self.canvas.itemconfig(
                        self._lbl,
                        fill='white' if self._enabled else '#666666')
                    self.canvas.config(
                        cursor='hand2' if self._enabled else '')

            def grid(self, **kw):
                self.canvas.grid(**kw)

        # ── D-pad button helper ────────────────────────────────────────────
        # Single click  → press_* (tap, Arduino auto-releases after arrow_release ms)
        # Double click  → hold_*  (servo stays pressed; click Release All to return)
        def _dpad_btn(par, text, press_action, hold_action):
            btn = tk.Button(
                par, text=text,
                bg='#2a3f8f', fg=FG,
                activebackground='#3a5fbf', activeforeground=FG,
                relief='flat', bd=1,
                font=('Arial', 11, 'bold'),
                width=3, height=1, cursor='hand2',
            )

            _st = [0, None]   # [click_count, pending after-id]

            def _on_click(_, b=btn):
                if b['state'] == 'disabled':
                    return
                _st[0] += 1
                if _st[0] == 1:
                    # Wait 250 ms; if no second click arrives, fire single-press
                    _st[1] = self.after(250, _fire_single)
                else:
                    # Second click within window → cancel single, fire hold
                    if _st[1]:
                        self.after_cancel(_st[1])
                    _st[0] = 0
                    _st[1] = None
                    self._manual_send(hold_action)

            def _fire_single():
                _st[0] = 0
                _st[1] = None
                self._manual_send(press_action)

            btn.bind('<Button-1>', _on_click)
            self._manual_btns.append(btn)
            return btn

        # ── Outer container ────────────────────────────────────────────────
        outer = tk.Frame(parent, bg=BG2, pady=4)
        outer.pack(fill='x', pady=(4, 0))

        hdr = tk.Frame(outer, bg=BG2)
        hdr.pack(fill='x', padx=10, pady=(2, 4))
        tk.Label(hdr, text='Manual Controls', bg=BG2, fg=FG2,
                 font=('Arial', 9, 'bold')).pack(side='left')
        tk.Label(hdr, text='— test buttons before running a script',
                 bg=BG2, fg=FG2, font=('Arial', 8)).pack(side='left', padx=(6, 0))

        row = tk.Frame(outer, bg=BG2)
        row.pack(padx=10, pady=(0, 6), anchor='w')

        # ── D-Pad / Joystick ───────────────────────────────────────────────
        dpad = tk.Frame(row, bg=BG2)
        dpad.pack(side='left', padx=(0, 4))

        tk.Label(dpad, text='D-Pad / Joystick', bg=BG2, fg=FG2,
                 font=('Arial', 8)).grid(row=0, column=0, columnspan=3, pady=(0, 2))

        _dpad_btn(dpad, '↑', 'press_up',    'hold_up').grid(   row=1, column=1, padx=2, pady=2)
        _dpad_btn(dpad, '←', 'press_left',  'hold_left').grid( row=2, column=0, padx=2, pady=2)
        tk.Label(dpad, width=3, height=1, bg=BG2).grid(row=2, column=1)
        _dpad_btn(dpad, '→', 'press_right', 'hold_right').grid(row=2, column=2, padx=2, pady=2)
        _dpad_btn(dpad, '↓', 'press_down',  'hold_down').grid( row=3, column=1, padx=2, pady=2)

        tk.Label(dpad, text='click = press  \u00b7  dbl = hold', bg=BG2, fg='#556688',
                 font=('Arial', 7)).grid(row=4, column=0, columnspan=3, pady=(2, 0))

        # ── Separator ──────────────────────────────────────────────────────
        tk.Frame(row, bg=BG3, width=1).pack(side='left', fill='y', padx=10)

        # ── Face Buttons — round ABXY diamond ─────────────────────────────
        abxy = tk.Frame(row, bg=BG2)
        abxy.pack(side='left', padx=(0, 4))

        tk.Label(abxy, text='A / B / X / Y', bg=BG2, fg=FG2,
                 font=('Arial', 8)).grid(row=0, column=0, columnspan=3, pady=(0, 2))

        # X (blue)
        cbx = _CircleBtn(abxy, 'X', lambda: self._manual_send('press_x'), '#2255cc')
        cbx.grid(row=1, column=1, padx=3, pady=3)
        self._manual_btns.append(cbx)

        # Y (green)
        cby = _CircleBtn(abxy, 'Y', lambda: self._manual_send('press_y'), '#228833')
        cby.grid(row=2, column=0, padx=3, pady=3)
        self._manual_btns.append(cby)

        tk.Label(abxy, width=_CircleBtn.SIZE // 8, height=1,
                 bg=BG2).grid(row=2, column=1)   # centre gap

        # A (red)
        cba = _CircleBtn(abxy, 'A', lambda: self._manual_send('press_a'), '#cc2222')
        cba.grid(row=2, column=2, padx=3, pady=3)
        self._manual_btns.append(cba)

        # B (amber)
        cbb = _CircleBtn(abxy, 'B', lambda: self._manual_send('press_b'), '#bb7700')
        cbb.grid(row=3, column=1, padx=3, pady=3)
        self._manual_btns.append(cbb)

        # ── Separator ──────────────────────────────────────────────────────
        tk.Frame(row, bg=BG3, width=1).pack(side='left', fill='y', padx=10)

        # ── Special Buttons ────────────────────────────────────────────────
        special = tk.Frame(row, bg=BG2)
        special.pack(side='left', padx=(0, 4))

        tk.Label(special, text='Special', bg=BG2, fg=FG2,
                 font=('Arial', 8)).grid(row=0, column=0, columnspan=2,
                                         sticky='w', pady=(0, 2))

        SBS = dict(bg='#3a3a3a', fg=FG,
                   activebackground='#505050', activeforeground=FG,
                   relief='flat', bd=1, font=('Arial', 9), cursor='hand2')

        sr1 = tk.Button(special, text='Home / SR1', width=11,
                        command=lambda: self._manual_send('soft_reset'), **SBS)
        sr1.grid(row=1, column=0, padx=2, pady=2, sticky='ew')
        self._manual_btns.append(sr1)

        sr2 = tk.Button(special, text='SR2', width=6,
                        command=lambda: self._manual_send('soft_reset_z'), **SBS)
        sr2.grid(row=1, column=1, padx=2, pady=2, sticky='ew')
        self._manual_btns.append(sr2)

        plus = tk.Button(special, text='+ (Switch)', width=19,
                         command=lambda: self._manual_send('wonder_trade'), **SBS)
        plus.grid(row=2, column=0, columnspan=2, padx=2, pady=2, sticky='ew')
        self._manual_btns.append(plus)

        rel = tk.Button(special, text='Release All', width=19,
                        bg='#2a2a2a', fg='#999999',
                        activebackground='#3a3a3a', activeforeground=FG,
                        relief='flat', bd=1, font=('Arial', 9), cursor='hand2',
                        command=lambda: self._manual_send('release_all'))
        rel.grid(row=3, column=0, columnspan=2, padx=2, pady=2, sticky='ew')
        self._manual_btns.append(rel)

        # ── Separator ──────────────────────────────────────────────────────
        tk.Frame(row, bg=BG3, width=1).pack(side='left', fill='y', padx=10)

        # ── Light Sensor ───────────────────────────────────────────────────
        light = tk.Frame(row, bg=BG2)
        light.pack(side='left')

        tk.Label(light, text='Light Sensor', bg=BG2, fg=FG2,
                 font=('Arial', 8)).pack(anchor='w', pady=(0, 2))

        # Dial canvas
        self._light_canvas = tk.Canvas(
            light, width=120, height=72, bg=BG2,
            highlightthickness=0,
        )
        self._light_canvas.pack(anchor='w', pady=(0, 4))
        self._draw_light_dial(0)

        # Buttons row
        btn_row = tk.Frame(light, bg=BG2)
        btn_row.pack(anchor='w')

        light_btn = tk.Button(
            btn_row, text='Read',
            command=self._read_light_manual,
            bg='#225566', fg=FG,
            activebackground='#336677', activeforeground=FG,
            relief='flat', bd=1, font=('Arial', 9), cursor='hand2', width=6,
        )
        light_btn.pack(side='left', padx=(0, 4))
        self._manual_btns.append(light_btn)

        self._light_live_btn = tk.Button(
            btn_row, text='Live',
            command=self._toggle_light_live,
            bg='#334422', fg=FG,
            activebackground='#445533', activeforeground=FG,
            relief='flat', bd=1, font=('Arial', 9), cursor='hand2', width=6,
        )
        self._light_live_btn.pack(side='left')
        self._manual_btns.append(self._light_live_btn)

        # All buttons disabled until a controller connects
        self._set_manual_controls_state('disabled')

    def _set_manual_controls_state(self, state: str):
        """Enable or disable all manual control buttons."""
        if state == 'disabled':
            self._light_live = False
        for btn in self._manual_btns:
            try:
                btn.config(state=state)
            except Exception:
                pass

    def _manual_send(self, action_name: str):
        """Send a single command via the persistent controller (runs in background thread)."""
        if not self._controller or not self._controller.is_open():
            self._log('Not connected — select a serial port first.')
            return

        def _do():
            try:
                getattr(self._controller, action_name)()
            except Exception as e:
                self.after(0, lambda: self._log(f'Control error: {e}'))

        threading.Thread(target=_do, daemon=True).start()

    def _draw_light_dial(self, value: int):
        """Draw a semicircular dial showing value (0–1020)."""
        import math
        c = self._light_canvas
        if c is None:
            return
        c.delete('all')

        cx, cy, r = 60, 66, 52   # centre x, centre y, radius
        max_val = 1020

        # Background arc (full 180°, left to right)
        c.create_arc(cx - r, cy - r, cx + r, cy + r,
                     start=0, extent=180,
                     outline='#334455', width=10, style='arc')

        # Value arc
        frac = max(0.0, min(1.0, value / max_val))
        if frac > 0:
            extent = frac * 180
            # Colour: dark teal → bright cyan as value rises
            g = int(80 + frac * 175)
            b = int(120 + frac * 135)
            colour = f'#00{g:02x}{b:02x}'
            c.create_arc(cx - r, cy - r, cx + r, cy + r,
                         start=0, extent=extent,
                         outline=colour, width=10, style='arc')

        # Needle
        angle_deg = 180 - frac * 180   # 180° = left (0), 0° = right (1020)
        angle_rad = math.radians(angle_deg)
        nx = cx + (r - 8) * math.cos(angle_rad)
        ny = cy - (r - 8) * math.sin(angle_rad)
        c.create_line(cx, cy, nx, ny, fill='white', width=2)
        c.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill='white', outline='')

        # Value text
        c.create_text(cx, cy - 18, text=str(value),
                      fill='white', font=('Courier', 11, 'bold'))
        # Scale labels
        c.create_text(cx - r + 2, cy + 10, text='0',
                      fill='#667788', font=('Arial', 7), anchor='w')
        c.create_text(cx + r - 2, cy + 10, text='1020',
                      fill='#667788', font=('Arial', 7), anchor='e')

    def _read_light_manual(self):
        """Read the light sensor value once and update the dial."""
        if not self._controller or not self._controller.is_open():
            self._log('Not connected — select a serial port first.')
            return

        def _do():
            try:
                value = self._controller.read_light_value()
                self.after(0, lambda: self._draw_light_dial(value))
                self.after(0, lambda: self._log(f'Light sensor: {value} / 1020'))
            except Exception as e:
                self.after(0, lambda: self._log(f'Light sensor error: {e}'))

        threading.Thread(target=_do, daemon=True).start()

    def _toggle_light_live(self):
        """Toggle continuous live polling of the light sensor."""
        if not self._controller or not self._controller.is_open():
            self._log('Not connected — select a serial port first.')
            return
        self._light_live = not self._light_live
        if self._light_live_btn:
            self._light_live_btn.config(
                bg='#446600' if self._light_live else '#334422',
                relief='sunken' if self._light_live else 'flat',
            )
        if self._light_live:
            self._light_poll_once()

    def _light_poll_once(self):
        """Poll once and reschedule if live mode is still on."""
        if not self._light_live:
            return
        if not self._controller or not self._controller.is_open():
            self._light_live = False
            return

        def _do():
            try:
                value = self._controller.read_light_value()
                self.after(0, lambda: self._draw_light_dial(value))
            except Exception:
                pass
            self.after(0, self._light_reschedule)

        threading.Thread(target=_do, daemon=True).start()

    def _light_reschedule(self):
        if self._light_live:
            self._light_live_job = self.after(200, self._light_poll_once)

    # ── Firmware update ───────────────────────────────────────────────────────

    def _find_avrdude(self):
        """Return (avrdude_exe, avrdude_conf) from assets/avrdude/, or (None, None)."""
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        avr_dir = os.path.join(base, 'assets', 'avrdude')
        exe  = os.path.join(avr_dir, 'avrdude.exe')
        conf = os.path.join(avr_dir, 'avrdude.conf')
        if os.path.isfile(exe) and os.path.isfile(conf):
            return exe, conf
        return None, None

    def _update_firmware(self, label: str, api_url: str, btn: ttk.Button):
        """Download latest .hex from GitHub Releases and flash it to the Arduino.

        label   — human-readable variant name shown in log/dialogs ('3DS' or 'Switch')
        api_url — GitHub Releases API URL for the correct firmware repo
        btn     — the button that triggered this call (disabled during the update)
        """
        port = self._get_selected_port()
        if not port:
            messagebox.showwarning('No Port Selected',
                                   'Please select the GamePRo serial port first.',
                                   parent=self)
            return

        avrdude_exe, avrdude_conf = self._find_avrdude()
        if not avrdude_exe:
            messagebox.showerror('avrdude Not Found',
                                 'Could not find avrdude.exe in assets/avrdude/.\n'
                                 'Please rebuild the application.',
                                 parent=self)
            return

        if not messagebox.askyesno(
            'Update Firmware',
            f'This will flash the latest GamePRo {label} firmware to the Arduino on {port}.\n\n'
            'The device will be unresponsive for a few seconds during the update.\n\n'
            'Continue?',
            parent=self,
        ):
            return

        btn.config(state='disabled')
        self._log(f'Checking GitHub for latest {label} firmware...')

        def _do():
            try:
                # Fetch release metadata from GitHub API
                req = urllib.request.Request(
                    api_url,
                    headers={'User-Agent': 'GamePRo-App'},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    release = json.loads(resp.read().decode())

                tag    = release.get('tag_name', '?')
                assets = release.get('assets', [])
                hex_asset = next((a for a in assets if a['name'].endswith('.hex')), None)
                if not hex_asset:
                    raise ValueError(
                        f'No .hex file found in release {tag}.\n'
                        'Please attach a compiled .hex to the GitHub Release.')

                hex_name = hex_asset['name']
                hex_url  = hex_asset['browser_download_url']
                self.after(0, lambda: self._log(f'Found {tag}: {hex_name}'))
                self.after(0, lambda: self._log('Downloading firmware...'))

                with tempfile.NamedTemporaryFile(suffix='.hex', delete=False) as tf:
                    hex_path = tf.name
                urllib.request.urlretrieve(hex_url, hex_path)

                # Back up calibration from the current firmware before flashing
                self.after(0, lambda: self._log('Backing up calibration...'))
                cal_csv = None
                if self._controller and self._controller.is_open():
                    cal_csv = self._controller.read_calibration()
                if cal_csv:
                    self._pending_cal_restore = cal_csv
                    self.after(0, lambda: self._log('Calibration backed up.'))
                else:
                    self._pending_cal_restore = None
                    self.after(0, lambda: self._log(
                        'Calibration backup skipped (firmware may not support it yet).\n'
                        'You may need to recalibrate after the update.'))

                try:
                    self._flash_firmware(hex_path, port, avrdude_exe, avrdude_conf)
                finally:
                    try:
                        os.unlink(hex_path)
                    except OSError:
                        pass

            except urllib.error.URLError:
                self.after(0, lambda: self._log(
                    f'Could not reach GitHub. Check your internet connection.\n'
                    f'URL: {api_url}'))
            except Exception as e:
                self.after(0, lambda: self._log(f'Firmware update failed: {e}'))
            finally:
                self.after(0, lambda: btn.config(state='normal'))

        threading.Thread(target=_do, daemon=True).start()

    def _flash_firmware(self, hex_path: str, port: str,
                         avrdude_exe: str, avrdude_conf: str):
        """Close the serial port, run avrdude, then reconnect. Called from background thread."""
        # Release the serial port so avrdude can open it
        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None
            self.after(0, lambda: self._port_dot.config(fg='#666666'))
            self.after(0, lambda: self._set_manual_controls_state('disabled'))

        self.after(0, lambda: self._log(f'Flashing to {port}...'))

        cmd = [
            avrdude_exe,
            '-C', avrdude_conf,
            '-p', 'atmega328p',
            '-c', 'arduino',
            '-P', port,
            '-b', '115200',
            '-U', f'flash:w:{hex_path}:i',
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            self.after(0, lambda: self._log('Flash timed out — check the connection.'))
            return

        # avrdude writes most output to stderr; merge both streams
        raw = result.stderr + result.stdout
        for line in re.split(r'[\r\n]+', raw):
            line = line.strip()
            if line:
                self.after(0, lambda l=line: self._log(l))

        if result.returncode == 0:
            self.after(0, lambda: self._log('Firmware updated successfully!'))
        else:
            self.after(0, lambda: self._log(
                f'Flash failed (avrdude exit code {result.returncode}).'))
            return

        # Reconnect serial after a short pause for the Arduino to reboot
        self.after(1500, self._on_port_selected)

    # ── App update check ──────────────────────────────────────────────────────

    def _check_for_app_update(self):
        """Check GitHub for a newer app release. Runs silently in background."""
        def _do():
            try:
                req = urllib.request.Request(
                    APP_RELEASE_API,
                    headers={'User-Agent': 'GamePRo-App'},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    release = json.loads(resp.read().decode())
                latest = release.get('tag_name', '').strip()
                if latest and latest != APP_VERSION:
                    self.after(0, lambda: self._log(
                        f'Update available: {latest}  (you have {APP_VERSION})\n'
                        f'  Download at: {APP_DOWNLOAD_URL}'))
            except Exception:
                pass   # silently ignore — no internet, rate limit, etc.

        threading.Thread(target=_do, daemon=True).start()

    # ── Calibration dialog ────────────────────────────────────────────────────

    def _open_calibration(self):
        if not self._controller or not self._controller.is_open():
            messagebox.showwarning('Not Connected',
                                   'Please connect to the GamePRo first.',
                                   parent=self)
            return
        if self._cal_dialog and self._cal_dialog.winfo_exists():
            self._cal_dialog.lift()   # bring existing dialog to front
            return
        self._cal_dialog = CalibrationDialog(self, self._controller, self._log)

    def _open_script_builder(self):
        if self._builder_dialog and self._builder_dialog.winfo_exists():
            self._builder_dialog.lift()
            return
        self._builder_dialog = ScriptBuilderDialog(
            self, self._grabber, self._controller, self._get_scripts_dir())

    # ── Footer ────────────────────────────────────────────────────────────────

    def _build_footer(self):
        footer = tk.Frame(self, bg=BG2, pady=5)
        footer.pack(fill='x', padx=12, pady=(4, 10))

        link = tk.Label(footer,
                         text='www.noobysgamepro.com',
                         bg=BG2, fg=FG2,
                         font=('Arial', 9, 'underline'),
                         cursor='hand2')
        link.pack()
        link.bind('<Button-1>',
                  lambda e: webbrowser.open('https://www.noobysgamepro.com'))

    # ── Port / webcam population ──────────────────────────────────────────────

    def _populate_ports(self):
        # Close any open connection — it will reopen after selection
        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None
            self._port_dot.config(fg='#666666')
            self._set_manual_controls_state('disabled')

        ports_info = GameProController.list_ports()
        self._port_display_map = {}
        display_values = []
        for device, desc in ports_info:
            # Show  "COM3 — Arduino Nano (COM3)"  or just "COM3" if description
            # is unhelpfully identical to the device name
            if desc and desc.strip() and desc.strip() != device:
                label = f"{device} — {desc}"
            else:
                label = device
            display_values.append(label)
            self._port_display_map[label] = device

        self._port_combo['values'] = display_values
        if display_values:
            self._port_combo.current(0)
            self._on_port_selected()
        else:
            self._port_dot.config(fg='#666666')
            self._log('No serial ports found. Connect the GamePRo and click ↺')

    def _populate_webcams(self):
        cams = FrameGrabber.list_available()
        self._cam_combo['values'] = [str(c) for c in cams]
        if cams:
            self._cam_combo.current(0)
            self._log(f'Webcams found: {cams}  —  enable Preview to start capture')
        else:
            self._log('No webcams found. Connect your webcam and click ↺')

    def _on_port_selected(self, event=None):
        port = self._get_selected_port()
        if not port:
            return

        # Close any existing connection first
        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None

        self._port_dot.config(fg='#aaaa00')   # yellow = connecting
        self._set_manual_controls_state('disabled')
        self._log(f'Connecting to {port}...')

        def _open():
            try:
                ctrl = GameProController(port)
                self.after(0, lambda: self._on_controller_opened(ctrl, port))
            except Exception as e:
                self.after(0, lambda: self._on_controller_failed(port, str(e)))

        threading.Thread(target=_open, daemon=True).start()

    def _on_controller_opened(self, ctrl: GameProController, port: str):
        self._controller = ctrl
        self._port_dot.config(fg='#00cc44')
        self._log(f'Connected to {port}')
        self._set_manual_controls_state('normal')

        # If a firmware update just completed, restore the backed-up calibration
        if self._pending_cal_restore:
            cal = self._pending_cal_restore
            self._pending_cal_restore = None
            def _restore(c=ctrl, csv=cal):
                time.sleep(2.0)   # let the Arduino fully settle after reconnect
                # If backup has only 25 fields (old firmware), pad with default
                # pin assignments so new firmware (30 fields) accepts the write.
                if csv.count(',') == 24:
                    csv_padded = csv + ',2,3,6,7,8'
                else:
                    csv_padded = csv
                success = c.write_calibration(csv_padded)
                self.after(0, lambda: self._log(
                    'Calibration restored to Arduino.' if success
                    else 'Calibration restore failed — please recalibrate manually.'))
            threading.Thread(target=_restore, daemon=True).start()

    def _on_controller_failed(self, port: str, err: str):
        self._port_dot.config(fg='#cc0000')
        self._log(f'Cannot connect to {port}: {err}')

    def _on_cam_selected(self, event=None):
        # Only restart the grabber if preview is currently on
        if not self._preview_on:
            return
        idx_str = self._cam_var.get()
        if not idx_str:
            return
        if self._grabber:
            self._grabber.release()
        try:
            self._grabber = FrameGrabber(int(idx_str))
            self._video_panel.set_frame_grabber(self._grabber)
            self._log(f'Webcam {idx_str} switched')
        except Exception as e:
            self._log(f'Webcam error: {e}')

    def _on_video_overlay(self, canvas):
        """Draw the detect overlay box (if any) set by a running script."""
        if self._grabber is None:
            return
        box = self._grabber.get_detect_overlay()
        if box:
            x, y, w, h = box
            canvas.draw_rect(x, y, w, h, colour='#00ff88')

    def _toggle_preview(self):
        if self._preview_on:
            # Turn OFF
            self._preview_on = False
            if self._grabber:
                self._grabber.release()
                self._grabber = None
            self._video_panel.set_frame_grabber(None)
            self._preview_btn.config(text='● Preview: OFF', style='PreviewOff.TButton')
            self._log('Preview off.')
        else:
            # Turn ON
            idx_str = self._cam_var.get()
            if not idx_str:
                self._log('No webcam selected. Connect a webcam and click ↺')
                return
            try:
                self._grabber = FrameGrabber(int(idx_str))
                self._video_panel.set_frame_grabber(self._grabber)
                self._preview_on = True
                self._preview_btn.config(text='◼ Preview: ON', style='PreviewOn.TButton')
                self._log(f'Preview on (webcam {idx_str})')
            except Exception as e:
                self._log(f'Webcam error: {e}')

    def _get_selected_port(self) -> str:
        """Return the actual COM port device name from the current combobox selection."""
        display = self._port_var.get()
        return self._port_display_map.get(display, display)

    # ── Script tree ───────────────────────────────────────────────────────────

    def _populate_scripts(self):
        self._tree.delete(*self._tree.get_children())
        self._script_map = {}
        scripts_dir = self._get_scripts_dir()
        if os.path.isdir(scripts_dir):
            self._build_tree('', scripts_dir)
        else:
            self._log(f'Scripts directory not found: {scripts_dir}')

    def _get_scripts_dir(self) -> str:
        if getattr(sys, 'frozen', False):
            # External folder alongside the .exe — users can drop in new scripts
            return os.path.join(os.path.dirname(sys.executable), 'scripts')
        else:
            # Development: gamepro-scripts lives alongside gamepro-app
            app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            return os.path.join(os.path.dirname(app_dir), 'gamepro-scripts')

    def _check_for_scripts(self):
        """Download the latest scripts from GitHub and refresh the tree."""
        self._get_scripts_btn.config(state='disabled')
        self._log('Connecting to script repository...')

        def _download():
            try:
                scripts_dir = self._get_scripts_dir()
                os.makedirs(scripts_dir, exist_ok=True)

                self.after(0, lambda: self._log(f'Downloading from:\n  {SCRIPTS_REPO_ZIP}'))

                with tempfile.TemporaryDirectory() as tmp:
                    zip_path = os.path.join(tmp, 'scripts.zip')
                    urllib.request.urlretrieve(SCRIPTS_REPO_ZIP, zip_path)

                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        names = zf.namelist()
                        # The repo ZIP wraps everything in a root folder, e.g.
                        # "gamepro-scripts-main/gen_2_vc/random_encounter.py"
                        # Find that prefix and strip it.
                        root_prefix = names[0].split('/')[0] + '/' if names else ''

                        added = updated = 0
                        for name in names:
                            if not name.endswith('.py'):
                                continue
                            rel = name[len(root_prefix):]   # strip repo root
                            if not rel:
                                continue
                            dest = os.path.join(scripts_dir,
                                                rel.replace('/', os.sep))
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            data = zf.read(name)
                            if os.path.exists(dest):
                                with open(dest, 'rb') as f:
                                    if f.read() == data:
                                        continue       # unchanged — skip
                                with open(dest, 'wb') as f:
                                    f.write(data)
                                updated += 1
                            else:
                                with open(dest, 'wb') as f:
                                    f.write(data)
                                added += 1

                        # Ensure every subfolder has an __init__.py
                        for name in names:
                            if name.endswith('/'):
                                rel = name[len(root_prefix):].rstrip('/')
                                if not rel:
                                    continue
                                folder = os.path.join(scripts_dir,
                                                      rel.replace('/', os.sep))
                                os.makedirs(folder, exist_ok=True)
                                init = os.path.join(folder, '__init__.py')
                                if not os.path.exists(init):
                                    open(init, 'w').close()

                msg = (f'Scripts updated: {added} new, {updated} updated.'
                       if (added or updated)
                       else 'Already up to date — no changes.')
                self.after(0, lambda: self._log(msg))
                self.after(0, self._populate_scripts)

            except urllib.error.URLError:
                self.after(0, lambda: self._log(
                    'Could not reach the script repository.\n'
                    'Check your internet connection, or the GitHub URL\n'
                    'may not be active yet (SCRIPTS_REPO_ZIP in app.py).'))
            except Exception as e:
                self.after(0, lambda: self._log(f'Script update failed: {e}'))
            finally:
                self.after(0, lambda: self._get_scripts_btn.config(state='normal'))

        threading.Thread(target=_download, daemon=True).start()

    def _build_tree(self, parent_iid: str, directory: str):
        """Recursively add folders and scripts to the Treeview."""
        try:
            entries = sorted(os.scandir(directory),
                             key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return

        for entry in entries:
            if entry.name.startswith('_') or entry.name.startswith('.'):
                continue

            if entry.is_dir():
                label = entry.name.replace('_', ' ').title()
                iid = self._tree.insert(parent_iid, 'end',
                                         text=f'  \U0001f4c2  {label}', open=True)
                self._build_tree(iid, entry.path)

            elif entry.is_file() and entry.name.endswith('.py'):
                script_cls = self._load_script(entry.path)
                if script_cls:
                    iid = self._tree.insert(parent_iid, 'end',
                                             text=f'      {script_cls.NAME}')
                    self._script_map[iid] = script_cls

    def _load_script(self, path: str):
        """Dynamically load a .py file and return its BaseScript subclass, or None."""
        try:
            spec = importlib.util.spec_from_file_location('_gp_script', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if (isinstance(obj, type)
                        and issubclass(obj, BaseScript)
                        and obj is not BaseScript):
                    return obj
        except Exception as e:
            self._log(f'Failed to load {os.path.basename(path)}: {e}')
        return None

    # ── Run / Stop ────────────────────────────────────────────────────────────

    def _run_script(self):
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo('No Script', 'Please select a script to run.',
                                parent=self)
            return
        iid = selected[0]
        if iid not in self._script_map:
            messagebox.showinfo('Select a Script',
                                'Please select a script file, not a folder.',
                                parent=self)
            return

        if not self._controller or not self._controller.is_open():
            messagebox.showwarning(
                'Not Connected',
                'Please select a serial port and wait for the green connection dot.',
                parent=self)
            return

        script_cls = self._script_map[iid]
        script = script_cls()

        self._stop_event.clear()
        self._run_btn.config(state='disabled')
        self._stop_btn.config(state='normal')
        self._set_manual_controls_state('disabled')
        self._log(f'Starting: {script.NAME}')

        # Live LDR dial: update automatically whenever the script reads the sensor
        if self._controller:
            self._controller._ldr_display_callback = (
                lambda v: self.after(0, lambda: self._draw_light_dial(v))
            )

        def thread_target():
            try:
                script.run(
                    controller=self._controller,
                    frame_grabber=self._grabber,
                    stop_event=self._stop_event,
                    log=self._log,
                    request_calibration=self._video_panel.request_calibration,
                )
            except Exception as e:
                self._log(f'Script error: {e}')
            finally:
                self.after(0, self._on_script_done)

        self._script_thread = threading.Thread(target=thread_target, daemon=True)
        self._script_thread.start()

    def _stop_script(self):
        self._stop_event.set()
        self._video_panel.cancel_calibration()
        self._log('Stop requested...')

    def _on_script_done(self):
        self._run_btn.config(state='normal')
        self._stop_btn.config(state='disabled')
        if self._controller:
            self._controller._ldr_display_callback = None
        if self._grabber:
            self._grabber.clear_crop()
        self._video_panel.clear_warp()
        # Keep controller open for manual controls — only close on window close or port change
        if self._controller and self._controller.is_open():
            self._set_manual_controls_state('normal')
        self._log('Script finished.')

    # ── Log ───────────────────────────────────────────────────────────────────

    def _log(self, msg: str):
        """Thread-safe append to the log text widget."""
        def _append():
            self._log_text.config(state='normal')
            self._log_text.insert('end', f'> {msg}\n')
            self._log_text.see('end')
            self._log_text.config(state='disabled')
        self.after(0, _append)

    # ── Close ─────────────────────────────────────────────────────────────────

    def _on_close(self):
        self._light_live = False
        self._stop_event.set()
        self._video_panel.cancel_calibration()
        if self._grabber:
            self._grabber.release()
        if self._controller:
            self._controller.close()
        self.destroy()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _asset(self, filename: str) -> str:
        """Return absolute path to a file in the assets/ folder."""
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, 'assets', filename)
