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
def validate_measurements(total, foreign, fungal, immature, organic):
    """Return (ok, errors[], needs_zero_confirmation[])."""
    errors = []
    values = {
        "total_weight_g": total,
        "foreign_matter_g": foreign,
        "fungal_grains_g": fungal,
        "immature_grains_g": immature,
        "organic_matter_g": organic,
    }

    for field, v in values.items():
        if v is None:
            errors.append(f"{field} is mandatory.")
        elif v < 0:
            errors.append(f"{field} must be ≥ 0.")
        elif v > Decimal("999.99"):
            errors.append(f"{field} exceeds the 999.99 g maximum.")

    if not errors:
        defect_sum = foreign + fungal + immature + organic
        if defect_sum > total:
            errors.append(
                f"Defect categories sum to {defect_sum} g, which exceeds the "
                f"total sample weight of {total} g."
            )

    # Zero values are allowed but require an explicit confirmation modal.
    needs_zero_confirmation = [
        f for f in ("foreign_matter_g", "fungal_grains_g", "immature_grains_g", "organic_matter_g")
        if values[f] is not None and values[f] == 0
    ]

    return (len(errors) == 0, errors, needs_zero_confirmation)


# ── §6.2 segmentation quality flags ───────────────────────────────
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


# ── §8.1 weight-vs-label cross-validation ─────────────────────────
def cross_validate(submission):
    """
    Compare each measured defect weight-fraction against the corresponding
    labeled particle-fraction. A material mismatch is surfaced as an amber
    note for the QC reviewer (not a hard block).
    """
    warnings = []
    total = submission.particle_count
    if total == 0:
        return warnings

    dist = submission.label_distribution()

    def label_pct(label):
        return round(dist.get(label, 0) / total * 100, 1)

    checks = [
        ("foreign matter", float(submission.foreign_pct), label_pct(ParticleLabel.FOREIGN)),
        ("fungal / infected", float(submission.fungal_pct), label_pct(ParticleLabel.FUNGAL)),
        ("immature", float(submission.immature_pct), label_pct(ParticleLabel.IMMATURE)),
    ]

    for name, weight_pct, lbl_pct in checks:
        # 5 percentage-point tolerance band.
        if abs(weight_pct - lbl_pct) > 5.0:
            warnings.append({
                "code": "WEIGHT_LABEL_MISMATCH",
                "level": "amber",
                "message": (
                    f"Cross-validation: {name} weight is {weight_pct:.2f}% but "
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
