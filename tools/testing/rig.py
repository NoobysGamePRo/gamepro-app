"""
Rig profiles for headless test runs (tests/rigs/<name>.yaml).

A rig is one robot + camera + console. Its profile holds the serial port, the camera index,
and the answers to every request_calibration() a script makes, so a test can run without
anyone clicking on the video panel:

    name: switch1
    console: Switch
    port: COM6
    camera: 1
    corners:                # 4 clicks on the 640x480 camera frame, any order
      default: [[36, 58], [576, 58], [576, 420], [36, 420]]
    rects:                  # (x, y, w, h) exactly as drawn on the app's video panel
      FRLG_Shiny_Lapras.1: [300, 120, 40, 40]

Missing answers can be captured once with --calibrate: an OpenCV window shows what the app's
video panel would show (including the perspective warp) and the answer is saved here.
"""

import os

import cv2
import numpy as np
import yaml

PANEL_W, PANEL_H = 640, 480
PANEL_BG = (74, 29, 15)          # BGR of the video panel's padding colour (15, 29, 74)


def load_rig(path: str) -> dict:
    with open(path, encoding='utf-8') as f:
        rig = yaml.safe_load(f) or {}
    rig.setdefault('corners', {})
    rig.setdefault('rects', {})
    rig['_path'] = path
    return rig


def save_rig(rig: dict):
    data = {k: v for k, v in rig.items() if not k.startswith('_')}
    with open(rig['_path'], 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=None)


def panel_image(frame, warp):
    """The 640x480 image the app's video panel shows for this frame (see VideoPanel._update)."""
    if warp is None:
        return frame.copy()
    warped = cv2.warpPerspective(frame, warp['matrix'], (warp['out_w'], warp['out_h']))
    scale = min(PANEL_W / warp['out_w'], PANEL_H / warp['out_h'])
    disp_w = max(1, int(warp['out_w'] * scale))
    disp_h = max(1, int(warp['out_h'] * scale))
    disp = cv2.resize(warped, (disp_w, disp_h))
    out = np.full((PANEL_H, PANEL_W, 3), PANEL_BG, dtype=np.uint8)
    x_off, y_off = (PANEL_W - disp_w) // 2, (PANEL_H - disp_h) // 2
    out[y_off:y_off + disp_h, x_off:x_off + disp_w] = disp
    return out


def capture_rect(grabber, warp, prompt: str):
    """Let the operator drag a rectangle on the live panel image. Returns [x, y, w, h] or None."""
    title = f'GamePRo test - {prompt} (drag, then Enter; Esc cancels)'
    frame = grabber.get_latest_frame()
    if frame is None:
        return None
    x, y, w, h = cv2.selectROI(title, panel_image(frame, warp), showCrosshair=False)
    cv2.destroyWindow(title)
    if w <= 0 or h <= 0:
        return None
    return [int(x), int(y), int(w), int(h)]


def capture_corners(grabber, prompt: str):
    """Let the operator click 4 corners on the live camera frame. Returns [[x, y] x4] or None."""
    title = f'GamePRo test - {prompt} (click 4 corners; Esc cancels)'
    pts = []

    def on_mouse(event, x, y, _flags, _param):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 4:
            pts.append([int(x), int(y)])

    cv2.namedWindow(title)
    cv2.setMouseCallback(title, on_mouse)
    while len(pts) < 4:
        frame = grabber.get_latest_frame()
        if frame is not None:
            img = frame.copy()
            for i, (px, py) in enumerate(pts, 1):
                cv2.circle(img, (px, py), 8, (0, 255, 255), -1)
                cv2.putText(img, str(i), (px - 4, py + 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 0), 1)
            cv2.imshow(title, img)
        if cv2.waitKey(30) == 27:
            cv2.destroyWindow(title)
            return None
    cv2.destroyWindow(title)
    return pts


def default_rig_path(name: str) -> str:
    app_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return name if name.endswith('.yaml') else os.path.join(app_dir, 'tests', 'rigs', name + '.yaml')
