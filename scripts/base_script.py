"""
BaseScript — abstract base class for all GamePRo automation scripts.

To create a new script:
  1. Create a .py file anywhere inside the scripts/ folder (subfolders are fine).
  2. Define a class that inherits from BaseScript.
  3. Set NAME (shown in the GUI) and optionally DESCRIPTION.
  4. Implement the run() method.

The GUI will discover your script automatically on next launch — no registration needed.

Example
-------
from scripts.base_script import BaseScript
import time

class MyScript(BaseScript):
    NAME = "My Script"
    DESCRIPTION = "Does something useful."

    def run(self, controller, frame_grabber, stop_event, log, request_calibration):
        log("Starting My Script")
        while not stop_event.is_set():
            controller.press_a()
            time.sleep(1)
        log("Done")
"""

from abc import ABC, abstractmethod
import threading
from typing import Callable, Tuple, Optional


class BaseScript(ABC):
    """Abstract base class for all GamePRo automation scripts."""

    # ── Override these in your subclass ──────────────────────────────────────

    NAME: str = "Unnamed Script"
    DESCRIPTION: str = ""

    # ── Implement this in your subclass ──────────────────────────────────────

    @abstractmethod
    def run(
        self,
        controller,
        frame_grabber,
        stop_event: threading.Event,
        log: Callable[[str], None],
        request_calibration: Callable[[str], Tuple[int, int, int, int]],
    ):
        """
        Main script entry point. Called in a background thread by the GUI.

        Parameters
        ----------
        controller : GameProController
            Serial interface to the Arduino. Call press_a(), soft_reset(), etc.
        frame_grabber : FrameGrabber
            Live webcam feed. Call frame_grabber.get_latest_frame() to get a
            BGR numpy array (640×480), or None if not yet available.
        stop_event : threading.Event
            Check stop_event.is_set() regularly in your loop. When True, clean
            up and return as soon as possible.
        log : callable(str)
            Thread-safe function to append a message to the GUI log.
        request_calibration : callable(prompt_str) -> (x, y, w, h)
            Blocks your script thread and switches the video panel into
            click-drag mode. The user draws a rectangle on the live feed and
            the result is returned as (x, y, width, height) in frame pixels.
            If Stop is pressed during calibration, returns (0, 0, 1, 1) —
            check stop_event immediately after.
        """

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def wait(seconds: float, stop_event: threading.Event) -> bool:
        """
        Sleep for `seconds` while checking stop_event every 50 ms.
        Returns True if the wait completed normally, False if stop was requested.
        """
        import time
        end = time.time() + seconds
        while time.time() < end:
            if stop_event.is_set():
                return False
            time.sleep(0.05)
        return True

    @staticmethod
    def avg_rgb(frame, x: int, y: int, w: int, h: int) -> Tuple[float, float, float]:
        """
        Return the average (R, G, B) of a rectangular region in a BGR frame.
        Frame is a numpy ndarray from FrameGrabber.get_latest_frame().
        """
        import numpy as np
        region = frame[y:y + h, x:x + w]       # BGR slice
        mean = region.mean(axis=(0, 1))          # [B_avg, G_avg, R_avg]
        return float(mean[2]), float(mean[1]), float(mean[0])   # → (R, G, B)
