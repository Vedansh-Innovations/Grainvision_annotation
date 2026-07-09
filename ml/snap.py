"""
Magic-wand snap: given a shape the assayer drew, find the actual grain
boundary underneath it and return that boundary instead of the rough drawing.

Strategy (classic CV, CPU): crop a padded ROI around the drawn shape,
threshold the grains, and pick the contour that best overlaps the drawing.
If nothing overlaps confidently we return None so the caller keeps the
assayer's original drawing (they never lose their work).
"""
import cv2
import numpy as np


def _poly_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _mask_from_poly(poly, shape, offset):
    m = np.zeros(shape, np.uint8)
    pts = np.array([[int(x - offset[0]), int(y - offset[1])] for x, y in poly], np.int32)
    cv2.fillPoly(m, [pts], 255)
    return m


def snap_polygon(crop_bgr, polygon, min_area=20, max_area=10**9):
    """Return a snapped polygon (list of [x,y] in full-crop coords) or None."""
    if crop_bgr is None or len(polygon) < 3:
        return None
    H, W = crop_bgr.shape[:2]
    x0, y0, x1, y1 = _poly_bbox(polygon)
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    pad = int(0.6 * max(bw, bh)) + 6
    rx0, ry0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    rx1, ry1 = min(W, int(x1 + pad)), min(H, int(y1 + pad))
    if rx1 - rx0 < 3 or ry1 - ry0 < 3:
        return None
    roi = crop_bgr[ry0:ry1, rx0:rx1]

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, k, iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k, iterations=1)

    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    drawn = _mask_from_poly(polygon, th.shape, (rx0, ry0))
    drawn_area = float((drawn > 0).sum()) or 1.0

    best, best_iou = None, 0.0
    for c in contours:
        a = cv2.contourArea(c)
        if a < min_area or a > max_area:
            continue
        cmask = np.zeros(th.shape, np.uint8)
        cv2.drawContours(cmask, [c], -1, 255, -1)
        inter = float((cv2.bitwise_and(cmask, drawn) > 0).sum())
        cmask_area = float((cmask > 0).sum())
        union = cmask_area + drawn_area - inter
        iou = inter / union if union > 0 else 0.0
        if iou > best_iou:
            best_iou, best = iou, c

    # Require a real overlap so we don't snap onto a random nearby grain.
    if best is None or best_iou < 0.12:
        # Fallback for loose drawings: snap to the grain-sized contour whose
        # centre is nearest the centre of what the assayer drew.
        dcx = (x0 + x1) / 2 - rx0
        dcy = (y0 + y1) / 2 - ry0
        reach = 0.5 * max(bw, bh) + pad
        nearest, nd = None, 1e18
        for c in contours:
            a = cv2.contourArea(c)
            if a < min_area or a > max_area:
                continue
            M = cv2.moments(c)
            if M["m00"] == 0:
                continue
            ccx, ccy = M["m10"] / M["m00"], M["m01"] / M["m00"]
            d = ((ccx - dcx) ** 2 + (ccy - dcy) ** 2) ** 0.5
            if d < nd and d <= reach:
                nd, nearest = d, c
        best = nearest

    if best is None:
        return None

    peri = cv2.arcLength(best, True)
    approx = cv2.approxPolyDP(best, 0.01 * peri, True)
    if len(approx) < 3:
        return None
    return [[int(p[0][0] + rx0), int(p[0][1] + ry0)] for p in approx]
