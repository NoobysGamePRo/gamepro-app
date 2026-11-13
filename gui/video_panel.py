"""
GamePRo VideoPanel — tkinter Canvas that displays a live webcam feed.

Features:
- 30fps update loop via root.after()
- Converts OpenCV BGR frames to PIL ImageTk for tkinter display
- Optional overlay drawing (scripts draw detection boxes, etc.)
- Click-drag region selection for script calibration (mode='rect')
- 4-corner perspective warp for screen straightening (mode='corners')
"""

import json
import os
import sys
import tkinter as tk
from PIL import Image, ImageTk
import threading
import numpy as np
import cv2
from typing import Optional, Callable, Tuple

from core.runner import corners_to_warp
from core.screen_detect import detect_screen_live

PANEL_W = 640
PANEL_H = 480

# "Use detected corners" button drawn on the canvas during corners calibration
_AUTO_BTN = (PANEL_W // 2 - 120, PANEL_H - 52, PANEL_W // 2 + 120, PANEL_H - 16)


# ── Last confirmed screen corners (offered when the screen is too dark to detect) ──

def _corners_store_path() -> str:
    """calibration/screen_corners.json next to the exe (or the app folder when run from source)."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'calibration', 'screen_corners.json')


def load_last_corners(camera) -> Optional[list]:
    try:
        with open(_corners_store_path(), encoding='utf-8') as f:
            pts = json.load(f).get(str(camera))
        if isinstance(pts, list) and len(pts) == 4:
            return [(int(x), int(y)) for x, y in pts]
    except (OSError, ValueError, TypeError):
        pass
    return None


def save_last_corners(camera, pts):
    path = _corners_store_path()
    try:
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        data[str(camera)] = [[int(x), int(y)] for x, y in pts]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except OSError:
        pass                                   # remembering is a convenience only


class VideoPanel(tk.Canvas):
    """
    Canvas widget that shows a live webcam feed and supports:
      - Overlay drawing (via set_overlay_callback)
      - Click-drag region selection for calibration (via request_calibration)
      - 4-corner perspective warp for screen straightening (mode='corners')
    """

    def __init__(self, parent, **kwargs):
        super().__init__(
            parent,
            width=PANEL_W,
            height=PANEL_H,
            bg='#0f1d4a',
            highlightthickness=2,
            highlightbackground='#243580',
            **kwargs,
        )
        self._frame_grabber = None
        self._photo = None
        self._overlay_callback: Optional[Callable] = None

        # Calibration state (rect drag)
        self._cal_mode = False
        self._cal_start: Optional[Tuple[int, int]] = None
        self._cal_rect_id = None
        self._cal_result: Optional[Tuple[int, int, int, int]] = None
        self._cal_event: Optional[threading.Event] = None
        self._cal_prompt = ''

        # Corners calibration state (4 individual clicks)
        self._corners_mode = False
        self._corners_collected = []      # list of (x, y) tuples
        self._corners_marker_ids = []     # canvas item IDs for dot markers
        self._corners_result = None
        self._corners_event: Optional[threading.Event] = None
        self._corners_prompt = ''
        self._auto_corners = None         # detected screen corners awaiting one-click confirm
        self._auto_from_last = False      # True: _auto_corners are the last confirmed ones
        self._auto_searching = False
        self._auto_failed = False         # detection has not found the screen (yet)

        # Perspective warp state (active after corners calibration)
        self._warp_matrix = None
        self._warp_out_w = 0
        self._warp_out_h = 0

        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)

        self._update()

    # ── Frame grabber ─────────────────────────────────────────────────────────

    def set_frame_grabber(self, grabber):
        """Attach a FrameGrabber; passing None clears the display."""
        self._frame_grabber = grabber

    # ── Overlay ───────────────────────────────────────────────────────────────

    def set_overlay_callback(self, cb: Optional[Callable]):
        """
        cb(canvas: VideoPanel) is called every frame update after the image
        is drawn. Use self.create_rectangle / create_text etc. with tag='overlay'
        so they are cleaned up each frame.
        """
        self._overlay_callback = cb

    def draw_rect(self, x: int, y: int, w: int, h: int,
                  colour: str = 'red', tag: str = 'overlay'):
        """Convenience: draw a rectangle overlay on the video feed."""
        self.create_rectangle(x, y, x + w, y + h,
                               outline=colour, width=2, tags=tag)

    # ── Update loop ───────────────────────────────────────────────────────────

    def _update(self):
        # Clear per-frame overlays (not corner markers — they use 'corner_marker' tag)
        self.delete('overlay')

        if self._frame_grabber is not None:
            frame = self._frame_grabber.get_latest_frame()
            if frame is not None:
                # Apply perspective warp if active
                if self._warp_matrix is not None:
                    warped = cv2.warpPerspective(
                        frame, self._warp_matrix,
                        (self._warp_out_w, self._warp_out_h)
                    )
                    # Scale to fit panel preserving aspect ratio
                    scale = min(PANEL_W / self._warp_out_w,
                                PANEL_H / self._warp_out_h)
                    disp_w = max(1, int(self._warp_out_w * scale))
                    disp_h = max(1, int(self._warp_out_h * scale))
                    disp = cv2.resize(warped, (disp_w, disp_h))
                    rgb = disp[:, :, ::-1]
                    img = Image.fromarray(rgb)
                    # Centre in panel with background colour
                    padded = Image.new('RGB', (PANEL_W, PANEL_H), (15, 29, 74))
                    x_off = (PANEL_W - disp_w) // 2
                    y_off = (PANEL_H - disp_h) // 2
                    padded.paste(img, (x_off, y_off))
                    self._photo = ImageTk.PhotoImage(image=padded)
                else:
                    rgb = frame[:, :, ::-1]
                    img = Image.fromarray(rgb)
                    self._photo = ImageTk.PhotoImage(image=img)

                self.delete('frame')
                self.create_image(0, 0, anchor='nw',
                                  image=self._photo, tags='frame')
        else:
            self.delete('frame')
            self._photo = None

        # Script overlay
        if self._overlay_callback:
            self._overlay_callback(self)

        # Rect calibration prompt
        if self._cal_mode:
            self.create_text(
                PANEL_W // 2, 20,
                text=self._cal_prompt,
                fill='yellow',
                font=('Arial', 12, 'bold'),
                tags='overlay',
            )
            if self._cal_rect_id:
                self.tag_raise(self._cal_rect_id)

        # Corners calibration prompt + raise persistent markers above the frame
        elif self._corners_mode:
            n = len(self._corners_collected)
            auto = self._auto_corners if n == 0 else None
            if auto:
                if self._auto_from_last:
                    text = "Too dark to find the screen  —  click ✓ or the 4 corners"
                    btn = '✓  Use last corners'
                else:
                    text = "Screen found  —  click ✓ below, or click the 4 corners"
                    btn = '✓  Use detected corners'
                flat = [v for p in auto for v in p]
                self.create_polygon(*flat, outline='#00ee55', fill='', width=3, tags='overlay')
                x0, y0, x1, y1 = _AUTO_BTN
                self.create_rectangle(x0, y0, x1, y1, fill='#1e8a3c', outline='white',
                                      width=2, tags='overlay')
                self.create_text((x0 + x1) // 2, (y0 + y1) // 2, text=btn,
                                 fill='white', font=('Arial', 12, 'bold'), tags='overlay')
            elif self._auto_failed and n == 0:
                text = "Too dark to find the screen  —  click the 4 corners"
                self.create_text(
                    PANEL_W // 2, PANEL_H - 20,
                    text="Tip: show a bright screen (e.g. the party menu) before pressing Run",
                    fill='white', font=('Arial', 10), tags='overlay')
            elif self._auto_searching and n == 0:
                text = f"{self._corners_prompt}  —  looking for the screen..."
            else:
                text = (
                    f"{self._corners_prompt}  —  "
                    f"Click corner {n + 1} of 4 (any order)"
                )
            self.create_text(
                PANEL_W // 2, 20,
                text=text,
                fill='yellow',
                font=('Arial', 12, 'bold'),
                tags='overlay',
            )
            for mid in self._corners_marker_ids:
                try:
                    self.tag_raise(mid)
                except Exception:
                    pass

        self.after(33, self._update)

    # ── Calibration — rect drag ────────────────────────────────────────────────

    def request_calibration(self, prompt: str, mode: str = 'rect'):
        """
        Called from a script thread.

        mode='rect'  (default): switches to click-drag mode; blocks until the
            user draws a rectangle. Returns (x, y, width, height).

        mode='corners': switches to 4-click mode; blocks until the user clicks
            all four corners in any order. Computes a perspective warp, applies it to the
            display, and returns a dict:
                {'matrix': ndarray, 'out_w': int, 'out_h': int}
            Returns None if cancelled.
        """
        if mode == 'corners':
            return self._request_corners(prompt)

        # ── rect mode (existing behaviour) ───────────────────────────────────
        event = threading.Event()
        self._cal_event = event
        self._cal_result = None
        self._cal_prompt = prompt
        self._cal_mode = True
        self._cal_start = None
        if self._cal_rect_id:
            self.delete(self._cal_rect_id)
            self._cal_rect_id = None

        event.wait()
        return self._cal_result

    def _request_corners(self, prompt: str):
        """Collect 4 corner clicks and compute perspective warp."""
        # Clear any previous markers
        for mid in self._corners_marker_ids:
            try:
                self.delete(mid)
            except Exception:
                pass
        self._corners_marker_ids = []
        self._corners_collected = []
        self._corners_result = None
        self._corners_prompt = prompt

        self._auto_corners = None
        event = threading.Event()
        self._corners_event = event
        self._corners_mode = True

        # Try to find the screen automatically; the person confirms with one click,
        # or ignores it and clicks the 4 corners as before. A dark screen (a cave, a mostly
        # black room) can't be found, so the last confirmed corners for this camera are
        # offered instead, and detection keeps trying in case the picture brightens.
        camera = getattr(self._frame_grabber, 'index', None)
        last = load_last_corners(camera) if camera is not None else None
        self._auto_from_last = False
        self._auto_failed = False
        self._auto_searching = True
        while not event.is_set() and self._corners_mode and not self._corners_collected:
            try:
                found = detect_screen_live(self._frame_grabber, stop_event=event)
            except Exception:
                found = None
            if event.is_set() or not self._corners_mode or self._corners_collected:
                break
            if found:
                self._auto_failed = False
                self._auto_from_last = False
                self._auto_corners = found
                break
            self._auto_searching = False
            if last:
                if self._auto_corners is None:
                    self._auto_from_last = True
                    self._auto_corners = last
            else:
                self._auto_failed = True
        self._auto_searching = False

        event.wait()
        self._auto_corners = None
        self._auto_failed = False
        if self._corners_result is not None and camera is not None:
            save_last_corners(camera, self._corners_collected)
        return self._corners_result

    def cancel_calibration(self):
        """Unblock any waiting calibration call (e.g. when Stop is pressed)."""
        if self._cal_event and not self._cal_event.is_set():
            self._cal_result = (0, 0, 1, 1)
            self._cal_mode = False
            self._cal_event.set()

        self._auto_corners = None
        self._auto_failed = False
        if self._corners_event and not self._corners_event.is_set():
            self._corners_result = None
            self._corners_mode = False
            self._corners_event.set()

    def clear_warp(self):
        """Remove the perspective warp and restore the raw camera view."""
        self._warp_matrix = None
        self._warp_out_w = 0
        self._warp_out_h = 0
        # Clean up any lingering corner markers
        for mid in self._corners_marker_ids:
            try:
                self.delete(mid)
            except Exception:
                pass
        self._corners_marker_ids = []

    # ── Corners collection helpers ─────────────────────────────────────────────

    def _collect_corner(self, cx: int, cy: int):
        """Record one corner click, draw its marker, trigger warp after 4th."""
        n = len(self._corners_collected) + 1
        self._corners_collected.append((cx, cy))

        # Draw numbered yellow circle marker (persists between frames)
        r = 8
        oid = self.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill='yellow', outline='black', width=2,
            tags='corner_marker',
        )
        tid = self.create_text(
            cx, cy, text=str(n),
            fill='black', font=('Arial', 9, 'bold'),
            tags='corner_marker',
        )
        self._corners_marker_ids.extend([oid, tid])

        if n == 4:
            self._compute_warp()
            self._corners_mode = False
            if self._corners_event:
                self._corners_event.set()

    def _compute_warp(self):
        """Compute perspective transform from the 4 collected corner points."""
        warp = corners_to_warp(self._corners_collected)
        self._warp_matrix = warp['matrix']
        self._warp_out_w  = warp['out_w']
        self._warp_out_h  = warp['out_h']
        self._corners_result = warp

    # ── Mouse handlers ────────────────────────────────────────────────────────

    def _on_press(self, event):
        if self._corners_mode:
            auto = self._auto_corners if not self._corners_collected else None
            self._auto_corners = None
            x0, y0, x1, y1 = _AUTO_BTN
            if auto and x0 <= event.x <= x1 and y0 <= event.y <= y1:
                for px, py in auto:           # confirmed: same as clicking them
                    self._collect_corner(px, py)
            else:
                self._collect_corner(event.x, event.y)
            return
        if self._cal_mode:
            self._cal_start = (event.x, event.y)
            if self._cal_rect_id:
                self.delete(self._cal_rect_id)
                self._cal_rect_id = None

    def _on_drag(self, event):
        if self._corners_mode:
            return   # corners mode uses clicks only, not drags
        if self._cal_mode and self._cal_start:
            if self._cal_rect_id:
                self.delete(self._cal_rect_id)
            x0, y0 = self._cal_start
            self._cal_rect_id = self.create_rectangle(
                x0, y0, event.x, event.y,
                outline='red', width=2,
            )

    def _on_release(self, event):
        if self._corners_mode:
            return   # corners mode uses clicks only, not drags
        if self._cal_mode and self._cal_start and self._cal_event:
            x0, y0 = self._cal_start
            x1, y1 = event.x, event.y
            x = min(x0, x1)
            y = min(y0, y1)
            w = max(abs(x1 - x0), 1)
            h = max(abs(y1 - y0), 1)
            self._cal_result = (x, y, w, h)
            self._cal_mode = False
            self._cal_start = None
            if self._cal_rect_id:
                self.delete(self._cal_rect_id)
                self._cal_rect_id = None
            self._cal_event.set()
