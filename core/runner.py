"""
GamePRo script runner — the one place that loads and runs a script.

The GUI (gui/app.py) and the headless test mode (tools/testing/gamepro_test.py) both
call these functions, so a script under test runs through exactly the same code path
a customer's does.
"""

import importlib.util
import os
import sys

import cv2
import numpy as np

from scripts.base_script import BaseScript


def default_scripts_dir() -> str:
    """Where the app looks for scripts."""
    if getattr(sys, 'frozen', False):
        # External folder alongside the .exe — users can drop in new scripts
        return os.path.join(os.path.dirname(sys.executable), 'scripts')
    # Development: gamepro-scripts lives alongside gamepro-app
    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(os.path.dirname(app_dir), 'gamepro-scripts')


def load_script_class(path: str, log=None):
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
        if log:
            log(f'Failed to load {os.path.basename(path)}: {e}')
    return None


def run_script(script, controller, frame_grabber, stop_event, log, request_calibration):
    """
    Run one script instance to completion on the calling thread.
    Returns the exception if the script crashed, otherwise None.
    """
    try:
        script.run(
            controller=controller,
            frame_grabber=frame_grabber,
            stop_event=stop_event,
            log=log,
            request_calibration=request_calibration,
        )
    except Exception as e:
        log(f'Script error: {e}')
        return e
    return None


def corners_to_warp(pts):
    """
    Perspective warp from 4 clicked corners (any order), as returned by
    request_calibration(mode='corners'): {'matrix': ndarray, 'out_w': int, 'out_h': int}.
    """
    # Sort clicks into TL / TR / BR / BL regardless of click order.
    # Sum (x+y): smallest = TL, largest = BR.
    # Diff (x-y): largest = TR (high x, low y), smallest = BL (low x, high y).
    s = [p[0] + p[1] for p in pts]
    d = [p[0] - p[1] for p in pts]
    tl = pts[s.index(min(s))]
    br = pts[s.index(max(s))]
    tr = pts[d.index(max(d))]
    bl = pts[d.index(min(d))]
    src = np.array([tl, tr, br, bl], dtype=np.float32)

    # Output dimensions: average of opposite edge lengths
    w_top  = float(np.linalg.norm(src[1] - src[0]))
    w_bot  = float(np.linalg.norm(src[2] - src[3]))
    h_left = float(np.linalg.norm(src[3] - src[0]))
    h_right= float(np.linalg.norm(src[2] - src[1]))

    out_w = max(1, int((w_top + w_bot) / 2))
    out_h = max(1, int((h_left + h_right) / 2))

    dst = np.array([
        [0,         0        ],
        [out_w - 1, 0        ],
        [out_w - 1, out_h - 1],
        [0,         out_h - 1],
    ], dtype=np.float32)

    return {
        'matrix': cv2.getPerspectiveTransform(src, dst),
        'out_w':  out_w,
        'out_h':  out_h,
    }
