"""
GamePRo Camera — thread-safe OpenCV webcam frame grabber.

Runs a background daemon thread that continuously pulls frames from a
cv2.VideoCapture so both the GUI preview and any running script can call
get_latest_frame() without blocking each other.
"""

import cv2
import threading
import numpy as np
from typing import Optional


class FrameGrabber:
    """
    Continuously captures frames from a webcam in a background thread.
    Call get_latest_frame() from any thread to retrieve the most recent frame.
    """

    FRAME_WIDTH = 640
    FRAME_HEIGHT = 480

    def __init__(self, index: int):
        self._cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.FRAME_WIDTH)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.FRAME_HEIGHT)

        self._frame: Optional[np.ndarray] = None
        self._lock = threading.Lock()
        self._running = True
        self._crop: Optional[tuple] = None   # (x, y, w, h) in raw-frame pixels
        self._detect_overlay: Optional[tuple] = None  # (x, y, w, h) in frame pixels

        self._thread = threading.Thread(target=self._grab_loop, daemon=True)
        self._thread.start()

    def _grab_loop(self):
        while self._running:
            ret, frame = self._cap.read()
            if ret:
                with self._lock:
                    self._frame = frame

    def set_crop(self, x: int, y: int, w: int, h: int):
        """
        Crop every frame to (x, y, w, h) in raw-frame pixels and scale back to 640×480.
        Call clear_crop() to remove.  Thread-safe.
        """
        with self._lock:
            self._crop = (int(x), int(y), int(w), int(h))

    def clear_crop(self):
        """Remove any active crop; frames return to full 640×480."""
        with self._lock:
            self._crop = None

    def set_detect_overlay(self, x: int, y: int, w: int, h: int):
        """Show a detection region box on the video panel while polling."""
        with self._lock:
            self._detect_overlay = (int(x), int(y), int(w), int(h))

    def clear_detect_overlay(self):
        """Remove the detection region box from the video panel."""
        with self._lock:
            self._detect_overlay = None

    def get_detect_overlay(self) -> Optional[tuple]:
        """Return the current detection overlay (x, y, w, h) or None."""
        with self._lock:
            return self._detect_overlay

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """
        Returns a copy of the latest captured BGR frame, or None if not yet available.
        If a crop is set, the frame is cropped then scaled back to 640×480.
        Safe to call from any thread.
        """
        with self._lock:
            if self._frame is None:
                return None
            frame = self._frame.copy()
            crop  = self._crop

        if crop is not None:
            x, y, w, h = crop
            fh, fw = frame.shape[:2]
            x1, y1 = max(0, x),     max(0, y)
            x2, y2 = min(fw, x + w), min(fh, y + h)
            cropped = frame[y1:y2, x1:x2]
            if cropped.size > 0:
                frame = cv2.resize(cropped, (self.FRAME_WIDTH, self.FRAME_HEIGHT))
        return frame

    def is_opened(self) -> bool:
        return self._cap.isOpened()

    def release(self):
        """Stop the grab loop and release the camera."""
        self._running = False
        self._cap.release()

    @staticmethod
    def list_available(max_devices: int = 10) -> list:
        """
        Probe webcam indices 0..max_devices-1 and return those that open successfully.
        """
        available = []
        for i in range(max_devices):
            cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            if cap.isOpened():
                available.append(i)
                cap.release()
        return available
