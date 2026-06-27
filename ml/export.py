"""
COCO-format export of approved, pipeline-eligible submissions (PRD §10.1,
§12.2, §15.4). The output validates against the COCO instance-segmentation
schema: images[], annotations[] (polygon segmentation + bbox + area),
categories[].
"""
from datetime import datetime

from annotation.models import ParticleLabel, SubmissionStatus
from annotation.services import pipeline_eligibility

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
    # Shoelace area.
    area = 0.0
    n = len(polygon)
    for i in range(n):
        x1p, y1p = polygon[i]
        x2p, y2p = polygon[(i + 1) % n]
        area += x1p * y2p - x2p * y1p
    return [x0, y0, x1 - x0, y1 - y0], abs(area) / 2.0


def build_coco(commodity=None):
    """Build a COCO dict over eligible submissions, optionally one commodity."""
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
    for sub in qs.select_related("commodity", "mandi").prefetch_related("particles"):
        eligible, _reasons = pipeline_eligibility(sub)
        if not eligible:
            continue
        included += 1
        w, h = sub.capture_quality_scores.get("crop_size", [0, 0]) or [0, 0]
        coco["images"].append({
            "id": str(sub.id),
            "file_name": sub.crop_image.name if sub.crop_image else "",
            "width": w,
            "height": h,
            "commodity": sub.commodity.code,
            "mandi": sub.mandi.name,
            "assayer_id": sub.assayer_id,
            "weights_g": {
                "total": float(sub.total_weight_g),
                "foreign_matter": float(sub.foreign_matter_g),
                "fungal": float(sub.fungal_grains_g),
                "immature": float(sub.immature_grains_g),
                "organic": float(sub.organic_matter_g),
            },
        })
        for p in sub.particles.all():
            lbl = p.effective_label
            if lbl == ParticleLabel.UNLABELED or lbl not in CATEGORY_IDS:
                continue
            flat = [coord for pt in p.polygon for coord in pt]
            bbox, area = _bbox_and_area(p.polygon)
            coco["annotations"].append({
                "id": ann_id,
                "image_id": str(sub.id),
                "category_id": CATEGORY_IDS[lbl],
                "segmentation": [flat],
                "bbox": [round(v, 1) for v in bbox],
                "area": round(area, 1),
                "iscrowd": 0,
                "attributes": {"origin": p.origin, "boundary_edited": p.boundary_edited},
            })
            ann_id += 1

    return coco, included


def validate_coco(coco):
    """Light schema validation matching PRD §15.4.4."""
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
