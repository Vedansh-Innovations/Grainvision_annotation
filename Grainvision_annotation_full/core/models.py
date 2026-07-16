import re

from django.conf import settings
from django.db import models


# ── Annotation classes ─────────────────────────────────────────────
# Every commodity gets these five LOCKED default classes (PRD §7.1 spec
# colors). Admins can add extra, commodity-specific classes on top; extras
# get colors auto-assigned from EXTRA_CLASS_PALETTE (names only — per the
# product decision, admins never pick colors).
DEFAULT_ANNOTATION_CLASSES = [
    {"value": "good",     "label": "Good grain",        "color": "#2ECC71"},
    {"value": "broken",   "label": "Broken grain",      "color": "#E67E22"},
    {"value": "foreign",  "label": "Foreign particle",  "color": "#E74C3C"},
    {"value": "immature", "label": "Immature grain",    "color": "#9B59B6"},
    {"value": "fungal",   "label": "Fungal / infected", "color": "#1ABC9C"},
]
UNLABELED_COLOR = "#95A5A6"

# Auto-assign colors for admin-defined extra classes. Chosen to stay visually
# distinct from the five defaults, unlabeled grey (#95A5A6), the uncertain
# yellow (#F1C40F), the selection cyan (#22d3ee) and the QC purple (#8e44ad).
EXTRA_CLASS_PALETTE = [
    "#3498DB",  # blue
    "#E84393",  # pink
    "#8D6E63",  # brown
    "#B8860B",  # dark gold
    "#34495E",  # slate navy
    "#FF7F50",  # coral
    "#6C5CE7",  # indigo
    "#00838F",  # deep cyan
]

# Values that can never be used for a custom class: the locked defaults,
# plus internal pseudo-labels used by the annotation flow.
RESERVED_CLASS_VALUES = {c["value"] for c in DEFAULT_ANNOTATION_CLASSES} | {
    "unlabeled", "uncertain",
}


def class_value_from_name(name):
    """Slug a display name into a stable class value: 'Weevil damaged' → 'weevil_damaged'."""
    v = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return v[:32]


class Mandi(models.Model):
    """Market yard / inspection location (PRD §11.1 `mandis`)."""

    name = models.CharField(max_length=120)
    district = models.CharField(max_length=120)
    state = models.CharField(max_length=120)
    active = models.BooleanField(default=True)
    # Which commodities are handled at this mandi (assayers here see these).
    commodities = models.ManyToManyField(
        "core.Commodity", blank=True, related_name="mandis"
    )

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Mandis"

    def __str__(self):
        return f"{self.name} — {self.state}"


class Commodity(models.Model):
    """
    A crop type plus its segmentation tuning parameters (PRD §11.1
    `commodities`). The min/max particle-area bounds feed the segmentation
    pipeline; the expected count range drives the LOW_PARTICLE_COUNT flag.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=80)
    active = models.BooleanField(default=True)

    min_particle_area_px = models.PositiveIntegerField(default=50)
    max_particle_area_px = models.PositiveIntegerField(default=200000)
    expected_min_count = models.PositiveIntegerField(default=20)
    expected_max_count = models.PositiveIntegerField(default=500)

    # Dataset target for the admin progress bars (PRD §10.1 / §12.1).
    target_samples = models.PositiveIntegerField(default=500)

    # Admin-defined extra annotation classes for this commodity, on top of the
    # five locked defaults. Stored as [{"value": "weevil_damaged",
    # "label": "Weevil damaged"}, ...] — colors are auto-assigned by position
    # from EXTRA_CLASS_PALETTE, never stored.
    extra_classes = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Commodities"

    def __str__(self):
        return self.name

    # ── Dynamic annotation classes ─────────────────────────────────
    @property
    def extra_class_list(self):
        """Normalized list of this commodity's extra classes (may be empty)."""
        out = []
        for item in self.extra_classes or []:
            if isinstance(item, dict) and item.get("value") and item.get("label"):
                out.append({"value": str(item["value"]), "label": str(item["label"])})
        return out

    def annotation_classes(self):
        """
        Full ordered class set for this commodity: the five locked defaults
        first (stable positions → stable keyboard shortcuts 1–5), then any
        admin-defined extras with palette-assigned colors.
        Each entry: {"value", "label", "color", "locked"}.
        """
        classes = [{**c, "locked": True} for c in DEFAULT_ANNOTATION_CLASSES]
        for i, extra in enumerate(self.extra_class_list):
            classes.append({
                **extra,
                "color": EXTRA_CLASS_PALETTE[i % len(EXTRA_CLASS_PALETTE)],
                "locked": False,
            })
        return classes

    def class_color_map(self):
        """value → color for every class of this commodity, incl. 'unlabeled'."""
        m = {c["value"]: c["color"] for c in self.annotation_classes()}
        m["unlabeled"] = UNLABELED_COLOR
        return m

    def class_label_map(self):
        """value → display label for every class, incl. 'unlabeled'."""
        m = {c["value"]: c["label"] for c in self.annotation_classes()}
        m["unlabeled"] = "Unlabeled"
        return m

    def is_valid_label(self, value):
        """True if `value` is an assignable class for this commodity."""
        return any(c["value"] == value for c in self.annotation_classes())


class AuditAction(models.TextChoices):
    SUBMIT = "submit", "Submission created"
    QC_APPROVE = "qc_approve", "QC approved"
    QC_REJECT = "qc_reject", "QC rejected"
    QC_REWORK = "qc_rework", "Rework requested"
    LABEL_OVERRIDE = "label_override", "Particle label overridden"
    USER_CREATE = "user_create", "User created"
    USER_UPDATE = "user_update", "User updated"
    EXPORT = "export", "Dataset exported"
    LOGIN = "login", "User login"


class AuditLog(models.Model):
    """Append-only audit trail (PRD §11.1 `audit_log`, §13.3 logged actions)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    action = models.CharField(max_length=32, choices=AuditAction.choices)
    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}"

    @classmethod
    def record(cls, *, user, action, entity_type, entity_id, payload=None):
        from accounts.middleware import get_audit_context

        ctx = get_audit_context()
        return cls.objects.create(
            user=user if (user and user.is_authenticated) else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            payload=payload or {},
            ip_address=ctx["ip_address"],
            user_agent=ctx["user_agent"],
        )
