"""Create the admin user from DJANGO_SUPERUSER_* env vars if it doesn't exist.

Safe to run on every startup — does nothing if the user already exists or if
the env vars aren't set.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create the admin superuser from env vars if missing (idempotent)."

    def handle(self, *args, **options):
        User = get_user_model()
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")

        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_* not set; skipping admin creation.")
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Admin user '{username}' already exists; skipping.")
            return

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created admin user '{username}'."))
