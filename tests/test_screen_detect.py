"""
Tests for core/screen_detect.py on synthetic camera pictures.

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.screen_detect import detect_screen  # noqa: E402

# A GBA-shaped (3:2) screen, slightly off square-on, as on the Switch rig
GBA = [[35, 56], [573, 61], [574, 418], [31, 418]]


def camera_picture(corners, seed=0, dark_corner=False):
    """Dark bezel, lit screen with some game-like content, sensor noise."""
    rng = np.random.default_rng(seed)
    img = np.full((480, 640, 3), 12, np.uint8)
    cv2.fillPoly(img, [np.array(corners, np.int32)], (170, 200, 120))
    for _ in range(25):                               # bright and mid-tone "sprites"
        x, y = rng.integers(80, 520), rng.integers(100, 380)
        cv2.rectangle(img, (int(x), int(y)), (int(x + 30), int(y + 25)),
                      tuple(int(v) for v in rng.integers(60, 255, 3)), -1)
    if dark_corner:                                   # dark scenery touching the bottom-left corner
        cv2.rectangle(img, (31, 330), (150, 418), (30, 30, 30), -1)
    noise = rng.integers(-4, 5, img.shape)
    return np.clip(img.astype(int) + noise, 0, 255).astype(np.uint8)


class ScreenDetectTests(unittest.TestCase):

    def assertNear(self, pts, expect, tol=3):
        self.assertIsNotNone(pts)
        self.assertLessEqual(np.abs(np.array(pts) - np.array(expect)).max(), tol, pts)

    def test_finds_screen(self):
        pts, why = detect_screen(camera_picture(GBA))
        self.assertNear(pts, GBA)

    def test_dark_content_at_corner_still_fits_edges(self):
        pts, why = detect_screen(camera_picture(GBA, dark_corner=True))
        self.assertNear(pts, GBA)

    def test_switch_screen(self):
        sw = [[40, 70], [600, 72], [600, 387], [40, 385]]   # 16:9
        pts, why = detect_screen(camera_picture(sw))
        self.assertNear(pts, sw)

    def test_screen_off_the_picture_is_rejected(self):
        pts, why = detect_screen(camera_picture([[-20, 40], [660, 40], [660, 430], [-20, 430]]))
        self.assertIsNone(pts)

    def test_unknown_shape_is_rejected(self):
        pts, why = detect_screen(camera_picture([[100, 60], [540, 60], [540, 420], [100, 420]]))  # 1.22
        self.assertIsNone(pts)
        self.assertIn('shape', why)

    def test_dark_picture_is_rejected(self):
        pts, why = detect_screen(np.full((480, 640, 3), 10, np.uint8))
        self.assertIsNone(pts)


if __name__ == '__main__':
    unittest.main()
