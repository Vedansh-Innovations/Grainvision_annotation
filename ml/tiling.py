"""
Tiled segmentation for dense, small objects (e.g. a full plate of grains).

A single SAM2 pass over a whole plate misses grains in tight clusters because
each prompt point is too coarse. Tiling slices the crop into overlapping tiles,
segments each tile (where every grain is large relative to the tile, so the
prompt grid lands several points on it), maps the masks back to full-image
coordinates, and removes duplicates created by the overlaps via bounding-box NMS.

`generate_fn(rgb_tile) -> list[np.ndarray]` returns binary masks (uint8, tile
coordinates). This is engine-agnostic: it works with SAM2's generator or any
callable that returns masks, so the same code runs locally or in the GPU worker.
"""
import cv2
import numpy as np


def _bbox_nms(boxes, scores, iou_thresh):
    if len(boxes) == 0:
        return []
    boxes = boxes.astype(np.float32)
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        iou = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[np.where(iou <= iou_thresh)[0] + 1]
    return keep


def tiled_polygons(rgb, generate_fn, *, min_area, max_area, plate_mask=None,
                   tile=None, overlap=None, iou_thresh=0.45):
    """
    Segment `rgb` (HxWx3) in overlapping tiles and return a de-duplicated list of
    contours (each an (N,2) int32 array) in full-image coordinates.
    """
    H, W = rgb.shape[:2]
    if tile is None:
        tile = max(384, min(H, W) // 3)          # ~3-4 tiles per side
    if overlap is None:
        overlap = max(48, tile // 5)
    step = max(1, tile - overlap)

    contours, boxes, scores = [], [], []
    margin = 2
    for oy in range(0, max(1, H - overlap), step):
        for ox in range(0, max(1, W - overlap), step):
            y2, x2 = min(oy + tile, H), min(ox + tile, W)
            sub = rgb[oy:y2, ox:x2]
            sh, sw = sub.shape[:2]
            if sh < 16 or sw < 16:
                continue
            for m in generate_fn(sub):
                seg = (m > 0).astype(np.uint8)
                a = int(seg.sum())
                if a < min_area or a > max_area:
                    continue
                cs, _ = cv2.findContours(seg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not cs:
                    continue
                cnt = max(cs, key=cv2.contourArea)
                if cv2.contourArea(cnt) < min_area:
                    continue
                lb = cnt.reshape(-1, 2)                     # tile-local coords
                lx, ly, lw, lh = cv2.boundingRect(lb.astype(np.int32))
                # Drop fragments touching an INNER tile edge (overlap guarantees
                # the whole grain is captured by the neighbouring tile). Edges at
                # the global image border are kept.
                if (lx <= margin and ox > 0) or (ly <= margin and oy > 0) or \
                   (lx + lw >= sw - margin and x2 < W) or (ly + lh >= sh - margin and y2 < H):
                    continue
                cnt = lb + [ox, oy]                          # -> full-image coords
                bx, by, bw, bh = cv2.boundingRect(cnt.astype(np.int32))
                if plate_mask is not None:
                    cxp, cyp = int(bx + bw / 2), int(by + bh / 2)
                    if 0 <= cyp < H and 0 <= cxp < W and plate_mask[cyp, cxp] == 0:
                        continue
                contours.append(cnt.astype(np.int32))
                boxes.append([bx, by, bx + bw, by + bh])
                scores.append(float(cv2.contourArea(cnt)))

    if not contours:
        return []
    keep = _bbox_nms(np.array(boxes), np.array(scores), iou_thresh)
    kept = [contours[i] for i in keep]

    # Safety net: merge near-duplicate detections (fragments / overlap copies)
    # whose centroids are closer than ~0.6x their typical radius.
    cents, radii = [], []
    for c in kept:
        m = cv2.moments(c)
        if m["m00"] == 0:
            cents.append((0, 0)); radii.append(1); continue
        cents.append((m["m10"] / m["m00"], m["m01"] / m["m00"]))
        radii.append(np.sqrt(cv2.contourArea(c) / np.pi))
    order = np.argsort([-cv2.contourArea(c) for c in kept])   # largest first
    accepted, acc_pts = [], []
    for idx in order:
        cx, cy = cents[idx]
        thr = 0.6 * radii[idx]
        if any((cx - px) ** 2 + (cy - py) ** 2 < thr * thr for px, py in acc_pts):
            continue
        accepted.append(kept[idx]); acc_pts.append((cx, cy))
    return accepted
