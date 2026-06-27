from django.conf import settings
from django.db import models


class Mandi(models.Model):
    """Market yard / inspection location (PRD §11.1 `mandis`)."""

    name = models.CharField(max_length=120)
    district = models.CharField(max_length=120)
    state = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

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

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Commodities"

    def __str__(self):
        return self.name


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
