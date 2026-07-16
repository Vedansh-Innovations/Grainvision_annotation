"""
Boundary detection & segmentation pipeline (PRD §6).

Stage 1  Plate isolation      — Hough Circle Transform, white-fill background
Stage 2  Particle segmentation — SAM2 automatic masks (engine)
                                  Watershed only stands in if SAM2 is not
                                  required and unavailable, or returns nothing.
Stage 3  Polygon extraction    — contour approx + convexity-defect merge flag
Stage 5  Feature extraction    — area, perimeter, aspect_ratio, solidity, colour
"""
import logging

import cv2
import numpy as np

from . import sam2_loader

logger = logging.getLogger(__name__)


# ── Stage 1: plate isolation ──────────────────────────────────────
def isolate_plate(bgr, pre_cropped=False):
    """
    Detect the circular ceramic plate and return:
      (crop_bgr, plate_mask, (cx, cy, r), dark_fraction)
    Background outside the plate is set to white for contrast uniformity.

    pre_cropped=True means the client already cropped the frame to the
    circular capture guide (86% centred square; guide inscribed in it). In
    that case the plate rim is largely OUTSIDE the image, so Hough circle
    detection has no rim to find — it would either fall back small or lock
    onto an interior circle (grain-pile edge, plate pattern) and crop away
    real content. Skip detection entirely and take the inscribed circle,
    which fully contains everything the assayer saw inside the guide.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    if pre_cropped:
        cx, cy, r = w // 2, h // 2, int(min(h, w) * 0.5)
        circles = None
    else:
        gray_blur = cv2.medianBlur(gray, 7)
        min_r = int(min(h, w) * 0.30)
        max_r = int(min(h, w) * 0.52)
        circles = cv2.HoughCircles(
            gray_blur, cv2.HOUGH_GRADIENT, dp=1.2, minDist=min(h, w),
            param1=120, param2=40, minRadius=min_r, maxRadius=max_r,
        )
        if circles is not None:
            cx, cy, r = np.round(circles[0, 0]).astype(int)
        else:
            # Fallback: assume a centered plate filling most of the frame.
            cx, cy, r = w // 2, h // 2, int(min(h, w) * 0.47)

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.circle(mask, (int(cx), int(cy)), int(r), 255, -1)

    white_bg = np.full_like(bgr, 255)
    composited = np.where(mask[..., None] == 255, bgr, white_bg)

    # Tight square crop around the plate.
    x0, y0 = max(0, cx - r), max(0, cy - r)
    x1, y1 = min(w, cx + r), min(h, cy + r)
    crop = composited[y0:y1, x0:x1]
    crop_mask = mask[y0:y1, x0:x1]

    # Dark-region fraction inside the plate (PRD §6.2 DARK_REGION).
    plate_pixels = gray[mask == 255]
    dark_fraction = float((plate_pixels < 40).mean()) if plate_pixels.size else 0.0

    return crop, crop_mask, (int(cx), int(cy), int(r)), dark_fraction


# ── Stage 2 (primary): SAM2 ───────────────────────────────────────
def _segment_sam2(crop_bgr, plate_mask, min_area, max_area):
    gen = sam2_loader.get_mask_generator()
    if gen is None:
        return None
    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    with sam2_loader.inference_lock:
        masks = gen.generate(rgb)

    binaries = []
    for m in masks:
        seg = m["segmentation"].astype(np.uint8)
        area = int(seg.sum())
        if area < min_area or area > max_area:
            continue
        # Discard masks that are mostly off-plate.
        on_plate = cv2.bitwise_and(seg, (plate_mask > 0).astype(np.uint8))
        if on_plate.sum() < 0.6 * area:
            continue
        binaries.append(seg * 255)
    return binaries


# ── Stage 2 (per-grain): tiled SAM2 ───────────────────────────────
def _segment_sam2_tiled(crop_bgr, plate_mask, min_area, max_area):
    """Run SAM2 over overlapping tiles for dense per-grain recall."""
    from django.conf import settings
    from . import tiling

    gen = sam2_loader.get_mask_generator()
    if gen is None:
        return None

    def generate_fn(rgb_tile):
        with sam2_loader.inference_lock:
            masks = gen.generate(rgb_tile)
        return [m["segmentation"].astype(np.uint8) for m in masks]

    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
    tile = settings.SAM2_TILE_SIZE or None
    return tiling.tiled_polygons(
        rgb, generate_fn, min_area=min_area, max_area=max_area,
        plate_mask=(plate_mask > 0).astype(np.uint8), tile=tile,
    )


# ── Stage 2 (remote): offload SAM2 to a GPU worker ────────────────
def _segment_remote(crop_bgr, url, min_area, max_area):
    """POST the crop to a remote GPU segmentation service; get polygons back."""
    import requests
    from django.conf import settings

    ok, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    headers = {}
    token = getattr(settings, "SAM2_REMOTE_TOKEN", "")
    if token:
        headers["X-Worker-Token"] = token
    resp = requests.post(
        url.rstrip("/") + "/segment",
        files={"image": ("crop.jpg", buf.tobytes(), "image/jpeg")},
        data={"min_area": str(min_area), "max_area": str(max_area)},
        headers=headers,
        timeout=getattr(settings, "SAM2_REMOTE_TIMEOUT", 120),
    )
    resp.raise_for_status()
    polys = resp.json().get("polygons", [])
    return [np.array(p, dtype=np.int32) for p in polys if len(p) >= 3]


# ── Stage 2 (fallback): Otsu + Watershed ──────────────────────────
def _segment_watershed(crop_bgr, plate_mask, min_area, max_area):
    """Per-grain instance segmentation with classic CV.

    Strategy: threshold grains -> distance transform -> find ONE local-maximum
    marker per grain (so touching grains get separate markers) -> watershed.
    The grain size is estimated from the isolated grains in the image, so the
    marker spacing adapts to the commodity instead of a fixed global cutoff.
    """
    import math

    from scipy import ndimage as ndi
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed as sk_watershed

    gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if plate_mask is not None:
        # Pull in from the rim a little so the metallic edge / shadow ring
        # isn't segmented as a giant "grain".
        er = max(4, int(0.025 * min(crop_bgr.shape[:2])))
        pm = cv2.erode(
            plate_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er, er))
        )
        th = cv2.bitwise_and(th, pm)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=1)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=1)
    if int((th > 0).sum()) == 0:
        return []

    # Estimate a single grain's radius from the smaller connected components
    # (isolated grains are small; clusters are large -> low percentile ~ 1 grain).
    n, lab, stats, _ = cv2.connectedComponentsWithStats(th, connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA].astype(float)
    areas = areas[(areas >= max(min_area, 8)) & (areas <= max_area)]
    typ_area = float(np.percentile(areas, 25)) if areas.size else max(min_area, 30)
    r = max(3, int(round(math.sqrt(typ_area / math.pi))))
    min_dist = max(3, int(round(r * 0.85)))

    dist = cv2.distanceTransform(th, cv2.DIST_L2, 5)
    # One marker per grain: local maxima of the distance map, spaced ~1 radius.
    coords = peak_local_max(
        dist,
        min_distance=min_dist,
        threshold_abs=max(1.5, 0.40 * r),
        labels=th.astype(bool),
        exclude_border=False,
    )
    if coords.shape[0] == 0:
        # fall back to whole-blob contours so we return something usable
        binaries = []
        cs, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cs:
            a = int(cv2.contourArea(c))
            if min_area <= a <= max_area:
                m = np.zeros(th.shape, np.uint8)
                cv2.drawContours(m, [c], -1, 255, -1)
                binaries.append(m)
        return binaries

    peaks = np.zeros(dist.shape, dtype=bool)
    peaks[tuple(coords.T)] = True
    markers, _ = ndi.label(peaks)
    labels = sk_watershed(-dist, markers, mask=th.astype(bool))

    binaries = []
    for label in range(1, int(labels.max()) + 1):
        seg = (labels == label).astype(np.uint8) * 255
        area = int((seg > 0).sum())
        if min_area <= area <= max_area:
            binaries.append(seg)
    return binaries


# ── Stage 3 + 5: polygon + features ───────────────────────────────
def _mask_to_polygon_and_features(seg, crop_bgr):
    contours, _ = cv2.findContours(seg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)

    eps = 0.01 * cv2.arcLength(cnt, True)
    approx = cv2.approxPolyDP(cnt, eps, True).reshape(-1, 2)
    if len(approx) < 3:
        return None

    area = float(cv2.contourArea(cnt))
    perimeter = float(cv2.arcLength(cnt, True))
    x, y, w, h = cv2.boundingRect(cnt)
    aspect_ratio = round(w / h, 3) if h else 0.0

    hull = cv2.convexHull(cnt)
    hull_area = float(cv2.contourArea(hull)) or 1.0
    solidity = round(area / hull_area, 3)

    # Convexity-defect analysis (PRD §6.1 Stage 3) → merge suspicion.
    flagged = False
    try:
        hull_idx = cv2.convexHull(cnt, returnPoints=False)
        if hull_idx is not None and len(hull_idx) > 3:
            defects = cv2.convexityDefects(cnt, hull_idx)
            if defects is not None:
                deep = [d for d in defects[:, 0, 3] if d / 256.0 > 6.0]
                if len(deep) >= 2 or solidity < 0.88:
                    flagged = True
    except cv2.error:
        flagged = solidity < 0.88

    # Mean colour in RGB and Lab.
    cell_mask = np.zeros(seg.shape, np.uint8)
    cv2.drawContours(cell_mask, [cnt], -1, 255, -1)
    mean_bgr = cv2.mean(crop_bgr, mask=cell_mask)[:3]
    mean_rgb = [round(mean_bgr[2], 1), round(mean_bgr[1], 1), round(mean_bgr[0], 1)]
    lab = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2LAB)
    mean_lab = [round(c, 1) for c in cv2.mean(lab, mask=cell_mask)[:3]]

    features = {
        "area": round(area, 1),
        "perimeter": round(perimeter, 1),
        "aspect_ratio": aspect_ratio,
        "solidity": solidity,
        "mean_rgb": mean_rgb,
        "mean_lab": mean_lab,
    }
    polygon = [[int(px), int(py)] for px, py in approx]
    return polygon, features, flagged


# ── Public entry point ────────────────────────────────────────────
def segment_image(bgr, commodity, pre_cropped=False):
    """
    Run the full pipeline on a decoded BGR image for a given commodity.

    Returns a dict:
      {
        "crop_bgr": np.ndarray,
        "plate": {"cx","cy","r"},
        "crop_size": [w, h],
        "engine": "sam2" | "watershed",
        "particles": [{"polygon","features","flagged_by_seg","origin"}],
        "dark_fraction": float,
        "merge_flagged_count": int,
      }
    """
    crop, crop_mask, (cx, cy, r), dark_fraction = isolate_plate(bgr, pre_cropped=pre_cropped)
    ch, cw = crop.shape[:2]

    min_area = commodity.min_particle_area_px
    plate_area = np.pi * (r ** 2)
    max_area = min(commodity.max_particle_area_px, int(0.40 * plate_area))

    from django.conf import settings

    engine = "sam2"
    binaries = None
    polys = None
    remote = getattr(settings, "SAM2_REMOTE_URL", "")

    if remote:
        # Cheapest GPU path: offload only segmentation to a scale-to-zero worker.
        polys = _segment_remote(crop, remote, min_area, max_area)
        engine = "sam2-remote"
    elif sam2_loader.available() and sam2_loader.get_mask_generator() is not None:
        if getattr(settings, "SAM2_TILES", False):
            polys = _segment_sam2_tiled(crop, crop_mask, min_area, max_area)
            engine = "sam2-tiled"
        else:
            binaries = _segment_sam2(crop, crop_mask, min_area, max_area)
    else:
        # SAM2 could not load (and we're not offloading remotely).
        if settings.SAM2_ENABLED and settings.SAM2_REQUIRED:
            raise RuntimeError(
                "SAM2 is the configured segmentation engine but is not available: "
                f"{sam2_loader.unavailable_reason()} Install torch + the sam2 package "
                "and place the checkpoint at SAM2_CHECKPOINT, set SAM2_REMOTE_URL to a "
                "GPU worker, or set SAM2_REQUIRED=False to permit the watershed engine."
            )

    if polys is None and not binaries:
        # Either SAM2 is not required (fallback permitted), or SAM2 ran but
        # returned no usable masks for this image — use watershed so the
        # assayer is never blocked by an empty result.
        binaries = _segment_watershed(crop, crop_mask, min_area, max_area)
        engine = "watershed"

    # Build particles from whichever representation we have (masks or polygons).
    def _mask_iter():
        if polys is not None:
            ch2, cw2 = crop.shape[:2]
            for poly in polys:
                m = np.zeros((ch2, cw2), np.uint8)
                cv2.fillPoly(m, [poly.astype(np.int32)], 255)
                yield m
        else:
            for seg in binaries:
                yield seg

    particles, merge_flagged = [], 0
    for seg in _mask_iter():
        result = _mask_to_polygon_and_features(seg, crop)
        if result is None:
            continue
        polygon, features, flagged = result
        if flagged:
            merge_flagged += 1
        particles.append({
            "polygon": polygon,
            "features": features,
            "flagged_by_seg": flagged,
            "origin": "auto",
        })

    # Stable ordering: top-to-bottom, left-to-right.
    particles.sort(key=lambda p: (p["polygon"][0][1], p["polygon"][0][0]))

    # Tag likely foreign matter (colour/size/shape outliers) as a hint.
    try:
        from .foreign import flag_foreign_suspects
        flag_foreign_suspects(particles)
    except Exception:
        pass

    return {
        "crop_bgr": crop,
        "plate": {"cx": cx, "cy": cy, "r": r},
        "crop_size": [cw, ch],
        "engine": engine,
        "particles": particles,
        "dark_fraction": round(dark_fraction, 4),
        "merge_flagged_count": merge_flagged,
    }
