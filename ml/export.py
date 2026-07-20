"""
COCO instance-segmentation export for model training.

Exports QC-APPROVED submissions only, with LABELED grains only (unlabeled
particles are skipped). Produces a ZIP bundle:

    annotations.json      # COCO: images[], annotations[], categories[]
    images/<file>.jpg     # the plate crops referenced by the annotations

This is directly consumable by SAM2 / Detectron2 / YOLO-seg training.
"""
import io
import json
import os
import zipfile
from datetime import datetime

from annotation.models import SubmissionStatus
from core.models import DEFAULT_ANNOTATION_CLASSES


def _category_map(commodity, submissions):
    """
    Ordered {class value → (category_id, display label)}.

    The five locked defaults always take stable ids 1–5 (so datasets exported
    at different times stay comparable). Admin-defined extra classes follow:
    the single commodity's extras in their configured order for a filtered
    export, or the sorted union of extras across the exported submissions'
    commodities for an all-commodities export.
    """
    mapping = {}
    for i, cls in enumerate(DEFAULT_ANNOTATION_CLASSES, start=1):
        mapping[cls["value"]] = (i, cls["label"])
    next_id = len(DEFAULT_ANNOTATION_CLASSES) + 1
    if commodity is not None:
        extras = commodity.extra_class_list
    else:
        seen, extras = set(), []
        commodities = {sub.commodity_id: sub.commodity for sub in submissions}.values()
        for c in commodities:
            for e in c.extra_class_list:
                if e["value"] not in seen:
                    seen.add(e["value"])
                    extras.append(e)
        extras.sort(key=lambda e: e["value"])
    for e in extras:
        if e["value"] not in mapping:
            mapping[e["value"]] = (next_id, e["label"])
            next_id += 1
    return mapping


def _bbox_and_area(polygon):
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    area = 0.0
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        area += ax * by - bx * ay
    return [x0, y0, x1 - x0, y1 - y0], abs(area) / 2.0


def _image_size(sub):
    """Best-effort width/height for the crop."""
    cs = (sub.capture_quality_scores or {}).get("crop_size")
    if cs and len(cs) == 2 and cs[0] and cs[1]:
        return int(cs[0]), int(cs[1])
    if sub.crop_image:
        try:
            from PIL import Image
            with sub.crop_image.open("rb") as f:
                return Image.open(f).size
        except Exception:
            pass
    return 0, 0


def build_coco(commodity=None):
    """COCO dict over QC-approved submissions (labeled grains only)."""
    from annotation.models import Submission

    qs = Submission.objects.filter(status=SubmissionStatus.QC_APPROVED)
    if commodity:
        qs = qs.filter(commodity=commodity)

    subs = list(qs.select_related("commodity", "mandi", "assayer").prefetch_related("particles"))
    categories = _category_map(commodity, subs)

    coco = {
        "info": {
            "description": "GrainVision AI annotation export",
            "version": "1.0",
            "date_created": datetime.utcnow().isoformat() + "Z",
        },
        "licenses": [{"id": 1, "name": "Proprietary — Prayathi Techno Solutions"}],
        "images": [],
        "annotations": [],
        "categories": [
            {"id": cid, "name": label, "supercategory": "grain"}
            for value, (cid, label) in categories.items()
        ],
    }

    ann_id = 1
    included = 0
    for sub in subs:
        labeled = [p for p in sub.particles.all()
                   if p.effective_label in categories]
        if not labeled:          # nothing usable to train on
            continue
        included += 1
        w, h = _image_size(sub)
        img_id = str(sub.id)

        # ── Weight-training metadata (the point of this dataset) ────
        q = sub.capture_quality_scores or {}
        scale = q.get("scale") or {}
        px_per_mm = scale.get("px_per_mm")
        # Per-class total pixel area from the stored polygons.
        class_area_px = {}
        for p in labeled:
            _, a = _bbox_and_area(p.polygon)
            class_area_px[p.effective_label] = round(class_area_px.get(p.effective_label, 0) + a, 1)
        weights = {"total_g": float(sub.total_weight_g) if sub.total_weight_g is not None else None}
        density = {}
        for value in categories:
            wg = sub.class_weight_g(value)
            weights[value + "_g"] = float(wg) if wg is not None else None
            a_px = class_area_px.get(value)
            if wg is not None and a_px and px_per_mm:
                a_mm2 = a_px / (px_per_mm ** 2)
                density[value + "_g_per_mm2"] = round(float(wg) / a_mm2, 6) if a_mm2 else None

        coco["images"].append({
            "id": img_id,
            "file_name": sub.crop_image.name if sub.crop_image else "",
            "raw_file_name": sub.raw_image.name if sub.raw_image else "",
            "width": w,
            "height": h,
            "commodity": sub.commodity.code,
            "mandi": sub.mandi.name if sub.mandi else "",
            "assayer": sub.assayer.get_full_name() or sub.assayer.username if sub.assayer else "",
            # measured per-class weights (grams) — the training target
            "weights": weights,
            # absolute physical scale from the detected blue plate rim
            "scale": {
                "rim_detected": scale.get("rim_detected", False),
                "px_per_mm": px_per_mm,
                "rim_inner_r_px": scale.get("rim_inner_r_px"),
                "rim_outer_r_px": scale.get("rim_outer_r_px"),
                "rim_coverage": scale.get("rim_coverage"),
                "plate_inner_diameter_mm": scale.get("plate_inner_diameter_mm"),
            },
            # per-class summed polygon areas + implied surface density
            "class_pixel_areas": class_area_px,
            "class_density": density,
            # capture-device metadata for recalibration
            "camera": q.get("camera") or {},
            "tilt": q.get("tilt"),
            "capture_quality": {k: q.get(k) for k in
                ("mean_luminance", "sharpness", "ring_cover", "balance", "torch", "glare")
                if k in q},
        })
        for p in labeled:
            flat = [c for pt in p.polygon for c in pt]
            bbox, area = _bbox_and_area(p.polygon)
            coco["annotations"].append({
                "id": ann_id,
                "image_id": img_id,
                "category_id": categories[p.effective_label][0],
                "segmentation": [flat],
                "bbox": [round(v, 1) for v in bbox],
                "area": round(area, 1),
                "iscrowd": 0,
                "attributes": {"origin": p.origin, "boundary_edited": p.boundary_edited},
            })
            ann_id += 1

    return coco, included


def build_export_zip(commodity=None):
    """Return (bytes, n_images, n_annotations): a training ZIP with images + COCO."""
    from django.core.files.storage import default_storage

    coco, _ = build_coco(commodity)
    # Rewrite file_name to a flat images/<basename> path and remember the source.
    sources = []
    for img in coco["images"]:
        src = img["file_name"]
        base = os.path.basename(src) if src else f"{img['id']}.jpg"
        img["file_name"] = f"images/{base}"
        if src:
            sources.append((src, f"images/{base}"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("annotations.json", json.dumps(coco, indent=2))
        z.writestr("README.txt",
                   "GrainVision COCO export\n"
                   "- annotations.json : COCO instance-segmentation (images, annotations, categories)\n"
                   "- images/          : plate crops referenced by annotations\n"
                   "Only QC-approved samples and labeled grains are included.\n")
        for src, arc in sources:
            try:
                with default_storage.open(src, "rb") as f:
                    z.writestr(arc, f.read())
            except Exception:
                pass  # image missing on disk — annotations still reference it
    return buf.getvalue(), len(coco["images"]), len(coco["annotations"])


def validate_coco(coco):
    errors = []
    for key in ("images", "annotations", "categories"):
        if key not in coco:
            errors.append(f"Missing top-level key: {key}")
    img_ids = {img["id"] for img in coco.get("images", [])}
    for ann in coco.get("annotations", []):
        for f in ("id", "image_id", "category_id", "segmentation", "bbox", "area"):
            if f not in ann:
                errors.append(f"Annotation {ann.get('id')} missing {f}")
        if ann.get("image_id") not in img_ids:
            errors.append(f"Annotation {ann.get('id')} references unknown image.")
    return (len(errors) == 0, errors)
