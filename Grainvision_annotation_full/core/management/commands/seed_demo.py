"""
Seed the platform with demo reference data and one account per role.

    python manage.py seed_demo

Idempotent: safe to run repeatedly. Demo passwords are printed at the end and
should be rotated immediately in any non-throwaway environment.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role, User
from core.models import Mandi, Commodity

MANDIS = [
    ("Indore Mandi", "Indore", "Madhya Pradesh"),
    ("Bhopal Mandi", "Bhopal", "Madhya Pradesh"),
    ("Chitradurga Mandi", "Chitradurga", "Karnataka"),
    ("Nagpur Mandi", "Nagpur", "Maharashtra"),
]

COMMODITIES = [
    ("WHEAT", "Wheat", 60, 60000, 20, 400),
    ("RICE", "Rice (Basmati)", 40, 40000, 30, 500),
    ("CHICKPEA", "Chickpea (Chana)", 120, 90000, 20, 300),
    ("LENTIL", "Lentil (Masoor)", 30, 30000, 30, 500),
]

# username, full name, role, password, mandi indices
USERS = [
    ("admin", "Arjun Kumar", Role.ADMIN, "admin12345", []),
    ("qc", "Sujata Joshi", Role.QC_REVIEWER, "qc12345678", []),
    ("ml", "Vedansh Rao", Role.ML_ENGINEER, "ml12345678", []),
    ("ravi", "Ravi Kumar", Role.ASSAYER, "assay12345", [0]),
    ("priya", "Priya Nair", Role.ASSAYER, "assay12345", [1]),
    ("sashi", "Sashi Gowda", Role.ASSAYER, "assay12345", [2]),
]


class Command(BaseCommand):
    help = "Seed demo mandis, commodities, and one user per role."

    @transaction.atomic
    def handle(self, *args, **opts):
        mandis = []
        for name, district, state in MANDIS:
            m, _ = Mandi.objects.get_or_create(
                name=name, defaults={"district": district, "state": state}
            )
            mandis.append(m)
        self.stdout.write(self.style.SUCCESS(f"Mandis: {len(mandis)}"))

        for code, name, amin, amax, cmin, cmax in COMMODITIES:
            Commodity.objects.update_or_create(
                code=code,
                defaults=dict(
                    name=name, min_particle_area_px=amin, max_particle_area_px=amax,
                    expected_min_count=cmin, expected_max_count=cmax, target_samples=500,
                ),
            )
        self.stdout.write(self.style.SUCCESS(f"Commodities: {len(COMMODITIES)}"))

        for username, full, role, pw, mandi_idx in USERS:
            first, _, last = full.partition(" ")
            u, created = User.objects.get_or_create(
                username=username,
                defaults=dict(
                    email=f"{username}@grainvision.local",
                    first_name=first, last_name=last, role=role,
                    is_staff=(role == Role.ADMIN), is_superuser=(role == Role.ADMIN),
                ),
            )
            if created:
                u.set_password(pw)
                u.save()
            u.mandis.set([mandis[i] for i in mandi_idx])
        self.stdout.write(self.style.SUCCESS(f"Users: {len(USERS)}"))

        self.stdout.write("")
        self.stdout.write(self.style.WARNING("Demo credentials (rotate before real use):"))
        for username, full, role, pw, _ in USERS:
            self.stdout.write(f"  {username:8s} / {pw:12s}  [{role}]")
