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

from annotation.models import ParticleLabel, SubmissionStatus

CATEGORY_IDS = {
    ParticleLabel.GOOD: 1,
    ParticleLabel.BROKEN: 2,
    ParticleLabel.FOREIGN: 3,
    ParticleLabel.IMMATURE: 4,
    ParticleLabel.FUNGAL: 5,
}


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
            {"id": cid, "name": ParticleLabel(lbl).label, "supercategory": "grain"}
            for lbl, cid in CATEGORY_IDS.items()
        ],
    }

    ann_id = 1
    included = 0
    for sub in qs.select_related("commodity", "mandi", "assayer").prefetch_related("particles"):
        labeled = [p for p in sub.particles.all()
                   if p.effective_label in CATEGORY_IDS]
        if not labeled:          # nothing usable to train on
            continue
        included += 1
        w, h = _image_size(sub)
        img_id = str(sub.id)
        coco["images"].append({
            "id": img_id,
            "file_name": sub.crop_image.name if sub.crop_image else "",
            "width": w,
            "height": h,
            "commodity": sub.commodity.code,
            "mandi": sub.mandi.name if sub.mandi else "",
            "assayer": sub.assayer.get_full_name() or sub.assayer.username if sub.assayer else "",
        })
        for p in labeled:
            flat = [c for pt in p.polygon for c in pt]
            bbox, area = _bbox_and_area(p.polygon)
            coco["annotations"].append({
                "id": ann_id,
                "image_id": img_id,
                "category_id": CATEGORY_IDS[p.effective_label],
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
