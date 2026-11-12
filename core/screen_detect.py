"""
Automatic screen-corner detection for request_calibration(mode='corners').

The console screen is the large lit area in an otherwise dark camera picture. The lit area
is found with a threshold relative to the bezel, then each of its 4 sides is fitted as a
straight line (RANSAC), so dark game content touching an edge only removes part of a side.
The corners are where the lines cross.

A result is only returned if it looks like a screen seen roughly straight on: solid,
fully inside the camera picture, opposite sides about equal, and a known screen shape.
Otherwise None is returned and the person clicks the corners as before.
"""

import time

import cv2
import numpy as np

# Width / height of the screens a GamePRo camera sees:
# GBC 10:9, DS / 3DS lower 4:3, GBA 3:2, 3DS upper 5:3, Switch 16:9
SCREEN_SHAPES = (10 / 9, 4 / 3, 3 / 2, 5 / 3, 16 / 9)


def order_corners(pts):
    """Sort 4 points into TL, TR, BR, BL (same rule as the manual corner clicks)."""
    pts = np.asarray(pts, np.float64)
    s, d = pts.sum(1), pts[:, 0] - pts[:, 1]
    return np.array([pts[s.argmin()], pts[d.argmax()], pts[s.argmax()], pts[d.argmin()]])


def _lit_mask(gray):
    band = np.concatenate([gray[:12].ravel(), gray[-12:].ravel(),
                           gray[:, :12].ravel(), gray[:, -12:].ravel()])
    bg = float(np.percentile(band, 10))                 # bezel / desk brightness
    thr = bg + max(12.0, 0.25 * (np.percentile(gray, 90) - bg))
    mask = (gray > thr).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((25, 25), np.uint8))


def _fit_side(pts, rng, iters=200, tol=1.5):
    """RANSAC line through the boundary points of one side. Returns ((point, dir), inliers)."""
    best_in = None
    for _ in range(iters):
        a, b = pts[rng.choice(len(pts), 2, replace=False)]
        d = b - a
        n = np.hypot(*d)
        if n < 20:
            continue
        inl = np.abs((pts - a) @ (np.array([-d[1], d[0]]) / n)) < tol
        if best_in is None or inl.sum() > best_in.sum():
            best_in = inl
    if best_in is None:
        return None, 0
    vx, vy, x0, y0 = cv2.fitLine(pts[best_in].astype(np.float32), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
    return (np.array([x0, y0]), np.array([vx, vy])), int(best_in.sum())


def _intersect(l1, l2):
    (p, r), (q, s) = l1, l2
    m = np.array([r, -s]).T
    if abs(np.linalg.det(m)) < 1e-6:
        return None
    return p + np.linalg.solve(m, q - p)[0] * r


def detect_screen(frame, min_area_frac=0.15, edge_margin=3):
    """
    Find the 4 corners of the lit screen in one BGR frame.
    Returns ([[x, y] x4] as TL, TR, BR, BL, reason) or (None, reason).
    """
    h, w = frame.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    cnts, _ = cv2.findContours(_lit_mask(gray), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None, 'nothing lit'
    c = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area_frac * w * h:
        return None, 'lit area too small'

    # Rough quad from the hull, then refine each side with a line fit
    hull = cv2.convexHull(c)
    quad = None
    for eps in np.linspace(0.01, 0.08, 15):
        a = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
        if len(a) == 4:
            quad = order_corners(a.reshape(4, 2))
            break
    if quad is None:
        return None, 'not 4-sided'
    pts = c.reshape(-1, 2).astype(np.float64)
    rng = np.random.default_rng(0)
    # search band around each rough side: wide enough to reach the true edge when dark
    # content has cut a corner off the rough quad
    band = max(25.0, 0.2 * min(np.linalg.norm(quad[i] - quad[(i + 1) % 4]) for i in range(4)))
    lines = []
    for i in range(4):
        a, b = quad[i], quad[(i + 1) % 4]
        d = b - a
        length = np.hypot(*d)
        t = (pts - a) @ d / length ** 2
        near = pts[(np.abs((pts - a) @ (np.array([-d[1], d[0]]) / length)) < band) & (t > -0.05) & (t < 1.05)]
        if len(near) < 30:
            return None, 'side not found'
        line, support = _fit_side(near, rng)
        if line is None or support < 0.5 * length:
            return None, 'side too broken'
        lines.append(line)
    corners = [_intersect(lines[i - 1], lines[i]) for i in range(4)]
    if any(p is None for p in corners):
        return None, 'parallel sides'
    quad = order_corners(corners)

    # Sanity checks: a screen seen roughly straight on
    if (quad[:, 0] <= edge_margin).any() or (quad[:, 0] >= w - 1 - edge_margin).any() \
            or (quad[:, 1] <= edge_margin).any() or (quad[:, 1] >= h - 1 - edge_margin).any():
        return None, 'screen runs off the camera picture'
    poly = quad.astype(np.float32).reshape(-1, 1, 2)
    sample = pts[::5]
    outside = sum(cv2.pointPolygonTest(poly, (float(x), float(y)), True) < -4 for x, y in sample)
    if outside > 0.02 * len(sample):
        return None, 'lit area sticks out of the fit'
    tl, tr, br, bl = quad
    top, bot = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    lef, rig = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    if min(top, bot) / max(top, bot) < 0.95 or min(lef, rig) / max(lef, rig) < 0.95:
        return None, 'skewed'
    ratio = (top + bot) / (lef + rig)
    if min(abs(ratio - r) for r in SCREEN_SHAPES) > 0.04:
        return None, f'not a known screen shape ({ratio:.2f})'
    return np.round(quad).astype(int).tolist(), 'ok'


def detect_screen_live(frame_grabber, seconds=2.0, samples=8, min_agree=3, tol=4, stop_event=None):
    """
    Try several live frames; return the median corners if at least `min_agree` frames found
    the screen and agree within `tol` px, else None.
    """
    found = []
    for _ in range(samples):
        if stop_event is not None and stop_event.is_set():
            return None
        frame = frame_grabber.get_latest_frame() if frame_grabber is not None else None
        if frame is not None:
            pts, _ = detect_screen(frame)
            if pts is not None:
                found.append(pts)
        time.sleep(seconds / samples)
    if len(found) < min_agree:
        return None
    arr = np.array(found, np.float64)
    med = np.median(arr, axis=0)
    close = arr[np.abs(arr - med).max(axis=(1, 2)) <= tol]
    if len(close) < min_agree:
        return None
    return np.round(np.median(close, axis=0)).astype(int).tolist()
