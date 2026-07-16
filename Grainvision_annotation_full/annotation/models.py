import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models


# ── Enumerations ──────────────────────────────────────────────────
class SubmissionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_QC = "pending_qc", "Pending QC"
    QC_APPROVED = "qc_approved", "QC Approved"
    QC_REJECTED = "qc_rejected", "QC Rejected"
    REWORK_REQUESTED = "rework_requested", "Rework Requested"


class ParticleLabel(models.TextChoices):
    """Six quality classes (PRD §7.1). Hex colors mirror the design spec."""
    GOOD = "good", "Good grain"
    BROKEN = "broken", "Broken grain"
    FOREIGN = "foreign", "Foreign particle"
    IMMATURE = "immature", "Immature grain"
    FUNGAL = "fungal", "Fungal / infected"
    UNLABELED = "unlabeled", "Unlabeled"


# Colors for the five locked default classes. The authoritative, per-commodity
# class set (defaults + admin-defined extras with auto-assigned colors) lives
# on core.Commodity.annotation_classes(); this map remains as a fallback for
# the defaults only.
LABEL_COLORS = {
    ParticleLabel.GOOD: "#2ECC71",
    ParticleLabel.BROKEN: "#E67E22",
    ParticleLabel.FOREIGN: "#E74C3C",
    ParticleLabel.IMMATURE: "#9B59B6",
    ParticleLabel.FUNGAL: "#1ABC9C",
    ParticleLabel.UNLABELED: "#95A5A6",
}


class ParticleOrigin(models.TextChoices):
    AUTO = "auto", "Auto-detected"
    USER = "user", "User-added"


class CaptureMode(models.TextChoices):
    AUTO = "auto_capture", "Auto-capture"
    MANUAL = "manual", "Manual (not pipeline-eligible)"


def submission_raw_path(instance, filename):
    return f"submissions/{instance.id}/raw_{filename}"


def submission_crop_path(instance, filename):
    return f"submissions/{instance.id}/crop_{filename}"


class Submission(models.Model):
    """One annotated sample (PRD §11.1 `submissions`)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sample_number = models.PositiveIntegerField(help_text="Sequential per assayer/session")

    assayer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="submissions"
    )
    commodity = models.ForeignKey("core.Commodity", on_delete=models.PROTECT)
    mandi = models.ForeignKey("core.Mandi", on_delete=models.PROTECT)

    status = models.CharField(
        max_length=20, choices=SubmissionStatus.choices, default=SubmissionStatus.DRAFT
    )

    # ── Physiochemical measurements (PRD §3) ──────────────────────
    # Nullable: the photo (step 1) is captured before measurements (step 2),
    # so a fresh draft has no weights yet.
    g = dict(max_digits=6, decimal_places=2, null=True, blank=True,
             validators=[MinValueValidator(0), MaxValueValidator(999.99)])
    total_weight_g = models.DecimalField(**g)
    foreign_matter_g = models.DecimalField(**g)
    fungal_grains_g = models.DecimalField(**g)
    immature_grains_g = models.DecimalField(**g)
    organic_matter_g = models.DecimalField(**g)
    measurements_done = models.BooleanField(default=False)

    # ── Image storage (PRD §5.3) ──────────────────────────────────
    raw_image = models.ImageField(upload_to=submission_raw_path, null=True, blank=True)
    crop_image = models.ImageField(upload_to=submission_crop_path, null=True, blank=True)
    capture_mode = models.CharField(
        max_length=16, choices=CaptureMode.choices, default=CaptureMode.AUTO
    )
    exif_json = models.JSONField(default=dict, blank=True)
    capture_quality_scores = models.JSONField(default=dict, blank=True)

    # ── QC bookkeeping ────────────────────────────────────────────
    warnings = models.JSONField(default=list, blank=True)
    qc_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="reviews",
    )
    qc_notes = models.TextField(blank=True)
    rework_instructions = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Null while the assayer is still in the measure→capture→annotate flow.
    # Set on final "Confirm & submit"; the QC queue only shows submitted samples.
    submitted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    @property
    def is_draft(self):
        return self.submitted_at is None

    @property
    def needs_rework(self):
        return self.status == SubmissionStatus.REWORK_REQUESTED

    @property
    def has_capture(self):
        return bool(self.crop_image)

    @property
    def annotation_complete(self):
        return self.has_capture and self.measurements_done and self.unlabeled_count == 0 and self.particle_count > 0

    @property
    def resume_step(self):
        """Which step an in-progress sample should resume at: 1..4."""
        if not self.has_capture:
            return 1            # capture
        if not self.measurements_done:
            return 2            # measurements
        if self.unlabeled_count > 0 or self.particle_count == 0:
            return 3            # annotate
        return 4               # review & submit

    @property
    def stage_label(self):
        if self.status == SubmissionStatus.REWORK_REQUESTED:
            return "Rework requested"
        return {1: "Photo captured", 2: "Measurements", 3: "Annotation", 4: "Ready to submit"}[self.resume_step]

    def __str__(self):
        return f"{self.public_id} · {self.commodity.name}"

    # ── derived display helpers ───────────────────────────────────
    @property
    def public_id(self):
        return f"GV-{self.created_at:%Y}-{str(self.id)[:5].upper()}" if self.created_at else f"GV-{str(self.id)[:5].upper()}"

    @property
    def short_id(self):
        return f"GV-{str(self.id)[:5].upper()}"

    def _pct(self, value):
        if not self.total_weight_g or value is None:
            return 0.0
        return round(float(value) / float(self.total_weight_g) * 100, 2)

    @property
    def foreign_pct(self): return self._pct(self.foreign_matter_g)
    @property
    def fungal_pct(self): return self._pct(self.fungal_grains_g)
    @property
    def immature_pct(self): return self._pct(self.immature_grains_g)
    @property
    def organic_pct(self): return self._pct(self.organic_matter_g)

    @property
    def defect_sum_g(self):
        vals = [self.foreign_matter_g, self.fungal_grains_g, self.immature_grains_g, self.organic_matter_g]
        return sum((v for v in vals if v is not None), Decimal("0"))

    @property
    def good_grain_estimate_g(self):
        if self.total_weight_g is None:
            return Decimal("0")
        return self.total_weight_g - self.defect_sum_g

    # ── particle aggregates ───────────────────────────────────────
    @property
    def particle_count(self):
        return self.particles.count()

    @property
    def uncertain_count(self):
        return self.particles.filter(uncertain=True).count()

    @property
    def unlabeled_count(self):
        return self.particles.filter(label=ParticleLabel.UNLABELED).count()

    def label_distribution(self):
        """{label: count} across this submission's particles."""
        dist = {lbl.value: 0 for lbl in ParticleLabel}
        for row in self.particles.values("label").annotate(n=models.Count("id")):
            dist[row["label"]] = row["n"]
        return dist


class Particle(models.Model):
    """A single segmented grain particle (PRD §11.1 `particles`)."""

    submission = models.ForeignKey(Submission, on_delete=models.CASCADE, related_name="particles")
    particle_id = models.PositiveIntegerField(help_text="Index within the submission")

    # No fixed `choices`: valid values are the submission's commodity classes
    # (five locked defaults + admin-defined extras) — validated in the views.
    label = models.CharField(max_length=32, default=ParticleLabel.UNLABELED)
    polygon = models.JSONField(help_text="List of [x, y] image-space vertices")
    origin = models.CharField(max_length=8, choices=ParticleOrigin.choices, default=ParticleOrigin.AUTO)

    boundary_edited = models.BooleanField(default=False)
    uncertain = models.BooleanField(default=False)
    flagged_by_seg = models.BooleanField(default=False)

    # Feature vector (PRD §6.1 Stage 5) — kept from the assayer to avoid anchoring.
    features = models.JSONField(default=dict, blank=True)

    # QC override bookkeeping (PRD §13.3 — logged).
    qc_label_override = models.CharField(max_length=32, null=True, blank=True)
    qc_overrider = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="particle_overrides",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["particle_id"]
        unique_together = ("submission", "particle_id")

    def __str__(self):
        return f"{self.submission.short_id} · #{self.particle_id} ({self.effective_label})"

    @property
    def effective_label(self):
        """QC override wins over the assayer's label (PRD §9.2)."""
        return self.qc_label_override or self.label

    @property
    def color(self):
        """Resolve through the submission's commodity so extras get their color."""
        return self.submission.commodity.class_color_map().get(
            self.effective_label, LABEL_COLORS[ParticleLabel.UNLABELED]
        )
