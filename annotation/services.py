"""
Business rules that the PRD treats as non-negotiable:

  * §3.3  measurement validation (defect sum cannot exceed total)
  * §6.2  segmentation quality flags
  * §8.1  weight-vs-label cross-validation (amber QC warnings)
  * §12.2 per-submission pipeline-eligibility gates
"""
from decimal import Decimal

from .models import ParticleLabel, SubmissionStatus, CaptureMode


# ── §3.3 measurement validation ───────────────────────────────────
def validate_measurements(total, class_weights, class_labels=None):
    """
    Validate the total sample weight plus one measured weight PER CLASS of the
    submission's commodity (dynamic — defaults + admin extras).

    total: Decimal | None
    class_weights: {class_value: Decimal | None}
    class_labels: {class_value: display label} for messages

    Returns (ok, errors[], needs_zero_confirmation[]).
    The class weights must sum to (approximately) the total: every particle on
    the plate belongs to exactly one class, so a gap larger than 2 % of the
    total is treated as a weighing error.
    """
    labels = class_labels or {}
    errors = []

    def check(name, v):
        if v is None:
            errors.append(f"{name} is mandatory.")
        elif v < 0:
            errors.append(f"{name} must be ≥ 0.")
        elif v > Decimal("999.99"):
            errors.append(f"{name} exceeds the 999.99 g maximum.")

    check("Total sample weight", total)
    for value, w in class_weights.items():
        check(labels.get(value, value), w)

    if not errors:
        wsum = sum(class_weights.values(), Decimal("0"))
        if wsum > total:
            errors.append(
                f"Class weights sum to {wsum} g, which exceeds the total "
                f"sample weight of {total} g.")
        elif total > 0 and (total - wsum) > total * Decimal("0.02"):
            errors.append(
                f"Class weights sum to {wsum} g but the total is {total} g "
                f"(gap {(total - wsum)} g > 2 %). Every particle belongs to a "
                f"class — re-check the balance readings.")

    needs_zero_confirmation = [
        v for v, w in class_weights.items() if w is not None and w == 0
    ]
    return (not errors), errors, needs_zero_confirmation


def segmentation_flags(particle_count, merge_flagged_count, dark_fraction, expected_min):
    flags = []
    if particle_count < max(20, expected_min):
        flags.append({
            "code": "LOW_PARTICLE_COUNT",
            "level": "warning",
            "message": "Fewer particles than expected. Check plate fill.",
        })
    if particle_count and merge_flagged_count / particle_count >= 0.10:
        flags.append({
            "code": "MERGE_SUSPECTED",
            "level": "orange",
            "message": "Some particles may be merged. Review flagged regions.",
        })
    if dark_fraction > 0.05:
        flags.append({
            "code": "DARK_REGION",
            "level": "warning",
            "message": "Shadowed region detected. Particles may not segment correctly.",
        })
    return flags


def cross_validate(submission):
    """
    Compare each class's measured weight-fraction against its labeled
    particle-fraction — for EVERY class of the submission's commodity
    (defaults + extras). A material mismatch is surfaced as an amber note
    for the QC reviewer (not a hard block).
    """
    warnings = []
    total = submission.particle_count
    if total == 0 or not submission.total_weight_g:
        return warnings

    dist = submission.label_distribution()

    for cls in submission.commodity.annotation_classes():
        value, name = cls["value"], cls["label"]
        weight_pct = submission.class_weight_pct(value)
        if weight_pct is None:
            continue
        lbl_pct = round(dist.get(value, 0) / total * 100, 1)
        # 5 percentage-point tolerance band.
        if abs(float(weight_pct) - lbl_pct) > 5.0:
            warnings.append({
                "code": "WEIGHT_LABEL_MISMATCH",
                "level": "amber",
                "message": (
                    f"Cross-validation: {name} weight is {float(weight_pct):.2f}% but "
                    f"{lbl_pct:.1f}% of particles are labeled {name}. "
                    f"Confirm labeling reflects actual composition."
                ),
            })
    return warnings


# ── §12.2 pipeline-eligibility gates ──────────────────────────────
def pipeline_eligibility(submission):
    """Return (eligible, reasons[]) for inclusion in a training export."""
    reasons = []
    if submission.status != SubmissionStatus.QC_APPROVED:
        reasons.append("Submission is not QC-approved.")
    if submission.capture_mode != CaptureMode.AUTO:
        reasons.append("Image was not auto-captured.")
    if submission.uncertain_count != 0:
        reasons.append("Uncertain particles remain unresolved.")
    for field in ("total_weight_g", "foreign_matter_g", "fungal_grains_g",
                  "immature_grains_g", "organic_matter_g"):
        if getattr(submission, field) is None:
            reasons.append(f"Missing physiochemical measurement: {field}.")
    n = submission.particle_count
    if not (20 <= n <= 500):
        reasons.append(f"Particle count {n} outside the 20–500 range.")
    if not submission.exif_json:
        reasons.append("EXIF metadata incomplete.")
    if submission.capture_quality_scores.get("glare"):
        reasons.append("Glare flag is set.")
    return (len(reasons) == 0, reasons)
