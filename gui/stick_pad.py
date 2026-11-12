"""
Joystick pad for the Manual Controls panel (Switch firmware v1.6+).

Drag the knob: the stick follows (angle + how far from the centre = magnitude).
Let go: it springs back to centre, like a real stick. With Lock ticked it stays
where you left it until you click the centre or untick Lock.

Angle 0 = up, 90 = right (clockwise), as in GameProController.stick_hold().
"""

import math
import tkinter as tk

BG2, FG2, DIM = '#0f1d4a', '#aabbdd', '#556688'


class StickPad(tk.Frame):
    SIZE = 92          # canvas size (px)
    KNOB = 13          # knob radius (px)
    DEAD = 0.08        # inside this fraction of the radius = centre

    def __init__(self, parent, send, **kw):
        """send(angle, magnitude) moves the stick; send(None, 0) centres it."""
        super().__init__(parent, bg=BG2, **kw)
        self._send = send
        self._enabled = False
        self._supported = False
        self._pos = (0.0, 0.0)            # knob position, -1..1 (y up)
        self._last = None                  # last (angle, magnitude) sent

        tk.Label(self, text='Stick', bg=BG2, fg=FG2, font=('Arial', 8)).pack(pady=(0, 2))
        s = self.SIZE
        self.canvas = tk.Canvas(self, width=s, height=s, bg=BG2, highlightthickness=0)
        self.canvas.pack()
        self._readout = tk.Label(self, text='', bg=BG2, fg=DIM, font=('Arial', 7))
        self._readout.pack()
        self._lock = tk.BooleanVar(value=False)
        self._lock_cb = tk.Checkbutton(self, text='Lock', variable=self._lock,
                                       command=self._on_lock, bg=BG2, fg=FG2,
                                       selectcolor='#243580', activebackground=BG2,
                                       activeforeground=FG2, font=('Arial', 7),
                                       bd=0, highlightthickness=0)
        self._lock_cb.pack()

        self.canvas.bind('<ButtonPress-1>', self._on_drag)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self._draw()

    # ── State (called by the app) ───────────────────────────────────────────

    def config(self, **kw):
        """Accepts state='normal'/'disabled' like the other manual buttons."""
        if 'state' in kw:
            self._enabled = kw.pop('state') == 'normal'
            self._lock_cb.config(state='normal' if self._active() else 'disabled')
            if not self._active():
                self._pos, self._last = (0.0, 0.0), None
            self._draw()
        if kw:
            super().config(**kw)

    configure = config

    def set_supported(self, supported: bool):
        self._supported = supported
        self.config(state='normal' if self._enabled else 'disabled')

    def _active(self):
        return self._enabled and self._supported

    # ── Mouse ───────────────────────────────────────────────────────────────

    def _on_drag(self, e):
        if not self._active():
            return
        r = self.SIZE / 2 - self.KNOB
        x, y = (e.x - self.SIZE / 2) / r, (self.SIZE / 2 - e.y) / r
        d = math.hypot(x, y)
        if d > 1:
            x, y, d = x / d, y / d, 1.0
        if d < self.DEAD:
            self._pos = (0.0, 0.0)
            self._emit(None, 0)
        else:
            self._pos = (x, y)
            self._emit(round(math.degrees(math.atan2(x, y))) % 360, round(d * 100))
        self._draw()

    def _on_release(self, _e):
        if not self._active() or self._lock.get():
            return
        self._pos = (0.0, 0.0)
        self._emit(None, 0)
        self._draw()

    def _on_lock(self):
        if not self._lock.get() and self._pos != (0.0, 0.0):
            self._pos = (0.0, 0.0)
            self._emit(None, 0)
            self._draw()

    def _emit(self, angle, mag):
        cur = (angle, mag)
        if cur == self._last:
            return
        last = self._last
        # small changes are not worth a servo move
        if (angle is not None and last and last[0] is not None
                and abs((angle - last[0] + 180) % 360 - 180) < 3 and abs(mag - last[1]) < 4):
            return
        self._last = cur
        self._send(angle, mag)

    # ── Drawing ─────────────────────────────────────────────────────────────

    def _draw(self):
        c, s, k = self.canvas, self.SIZE, self.KNOB
        c.delete('all')
        on = self._active()
        ring = '#3a5fbf' if on else '#2a3550'
        c.create_oval(2, 2, s - 2, s - 2, outline=ring, width=2, fill='#16245c' if on else BG2)
        m = s / 2
        for a in range(0, 360, 45):                     # direction ticks
            t = math.radians(a)
            r1, r2 = m - 8, m - 3
            c.create_line(m + r1 * math.sin(t), m - r1 * math.cos(t),
                          m + r2 * math.sin(t), m - r2 * math.cos(t), fill=ring)
        r = m - k
        x, y = m + self._pos[0] * r, m - self._pos[1] * r
        c.create_line(m, m, x, y, fill='#88aaff' if on else '#2a3550', width=2)
        c.create_oval(x - k, y - k, x + k, y + k,
                      fill='#2a3f8f' if on else '#1c2748', outline='#ffffff' if on else '#2a3550', width=2)
        if not self._supported:
            self._readout.config(text='needs Switch fw 1.6')
        elif self._last and self._last[0] is not None:
            self._readout.config(text=f'{self._last[0]}°  {self._last[1]}%')
        else:
            self._readout.config(text='drag to move')
