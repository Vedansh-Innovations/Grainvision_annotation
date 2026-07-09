"""
Magic-wand snap: given a shape the assayer drew (polygon / rectangle /
ellipse), look INSIDE that region with OpenCV, find the grain there, and
return the grain's real boundary. If no grain can be found in the region we
return None and the caller keeps the assayer's drawing unchanged.
"""
import cv2
import numpy as np


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _poly_mask(poly, shape, off):
    m = np.zeros(shape, np.uint8)
    pts = np.array([[int(x - off[0]), int(y - off[1])] for x, y in poly], np.int32)
    cv2.fillPoly(m, [pts], 255)
    return m


def snap_polygon(crop_bgr, polygon, min_area=15, max_area=10**9):
    """Return snapped polygon [[x,y],...] in full-crop coords, or None."""
    if crop_bgr is None or len(polygon) < 3:
        return None
    H, W = crop_bgr.shape[:2]
    x0, y0, x1, y1 = _bbox(polygon)
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    pad = int(0.35 * max(bw, bh)) + 4
    rx0, ry0 = max(0, int(x0 - pad)), max(0, int(y0 - pad))
    rx1, ry1 = min(W, int(x1 + pad)), min(H, int(y1 + pad))
    if rx1 - rx0 < 3 or ry1 - ry0 < 3:
        return None
    roi = crop_bgr[ry0:ry1, rx0:rx1]
    roi_area = roi.shape[0] * roi.shape[1]

    # Grain vs plate: grains are darker -> INV+Otsu makes them white.
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if (th > 0).mean() > 0.85:            # region is almost all "grain" -> ambiguous
        return None
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, k, iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, k, iterations=1)

    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    drawn = _poly_mask(polygon, th.shape, (rx0, ry0))
    drawn_area = float((drawn > 0).sum()) or 1.0
    dcx, dcy = (x0 + x1) / 2 - rx0, (y0 + y1) / 2 - ry0
    cap = min(max_area, 0.9 * roi_area)

    # Score each contour by how much of the DRAWN region it covers, plus a
    # bonus for containing the centre of the drawing. This picks the grain the
    # assayer aimed at even if their box was loose or slightly off.
    best, best_score = None, 0.0
    for c in contours:
        a = cv2.contourArea(c)
        if a < min_area or a > cap:
            continue
        cmask = np.zeros(th.shape, np.uint8)
        cv2.drawContours(cmask, [c], -1, 255, -1)
        inter = float((cv2.bitwise_and(cmask, drawn) > 0).sum())
        coverage = inter / drawn_area                       # how much of drawing is grain
        contains = cv2.pointPolygonTest(c, (float(dcx), float(dcy)), False) >= 0
        score = coverage + (0.4 if contains else 0.0)
        if score > best_score:
            best_score, best = score, c

    # Nothing overlaps meaningfully -> keep the assayer's drawing.
    if best is None or best_score < 0.20:
        return None

    peri = cv2.arcLength(best, True)
    approx = cv2.approxPolyDP(best, 0.008 * peri, True)
    if len(approx) < 3:
        return None
    return [[int(p[0][0] + rx0), int(p[0][1] + ry0)] for p in approx]
