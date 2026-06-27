from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    ASSAYER = "assayer", "Assayer"
    QC_REVIEWER = "qc_reviewer", "QC Reviewer"
    ADMIN = "admin", "Admin"
    ML_ENGINEER = "ml_engineer", "ML Engineer"


class GrainUserManager(UserManager):
    """Superusers created via `createsuperuser` get the Admin role by default."""

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """
    Custom user (PRD §2 / §11.1 `users`).

    The four roles map directly to the authorisation matrix in PRD §13.3.
    `mandis` is the set of locations an assayer is permitted to submit from.
    """

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ASSAYER)
    mandis = models.ManyToManyField("core.Mandi", blank=True, related_name="users")
    phone = models.CharField(max_length=20, blank=True)

    objects = GrainUserManager()

    # Lockout bookkeeping (PRD §13.1 — 5 failures -> 30 min lockout).
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    def __str__(self):
        return self.get_full_name() or self.username

    # ── role helpers ──────────────────────────────────────────────
    @property
    def is_assayer(self):
        return self.role == Role.ASSAYER and not self.is_superuser

    @property
    def is_qc(self):
        return self.role == Role.QC_REVIEWER

    @property
    def is_platform_admin(self):
        return self.role == Role.ADMIN or self.is_superuser

    @property
    def is_ml_engineer(self):
        return self.role == Role.ML_ENGINEER

    @property
    def initials(self):
        parts = (self.get_full_name() or self.username).split()
        return "".join(p[0] for p in parts[:2]).upper() or "U"

    # ── lockout helpers ───────────────────────────────────────────
    @property
    def is_locked(self):
        return self.locked_until is not None and self.locked_until > timezone.now()

    def register_failed_login(self):
        from django.conf import settings
        self.failed_login_count += 1
        if self.failed_login_count >= getattr(settings, "AXES_FAILURE_LIMIT", 5):
            minutes = getattr(settings, "AXES_LOCKOUT_MINUTES", 30)
            self.locked_until = timezone.now() + timezone.timedelta(minutes=minutes)
        self.save(update_fields=["failed_login_count", "locked_until"])

    def register_successful_login(self):
        self.failed_login_count = 0
        self.locked_until = None
        self.last_login_at = timezone.now()
        self.save(update_fields=["failed_login_count", "locked_until", "last_login_at"])
