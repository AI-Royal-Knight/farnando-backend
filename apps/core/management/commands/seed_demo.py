"""Idempotent demo data for every role and (eventually) every screen.

Phase 1 creates the four identities and their consent records. Later phases
extend this command with assessments, labs, evaluations, orders and messages.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.accounts.enums import Role
from apps.accounts.models import ConsentRecord, User

DEMO_USERS = [
    {
        "email": "admin@adelgaceya.pe",
        "first_name": "Ada",
        "last_name": "Administradora",
        "role": Role.ADMIN,
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "email": "marcus.vance@adelgaceya.pe",
        "first_name": "Marcus",
        "last_name": "Vance",
        "role": Role.PHYSICIAN,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "email": "staff@adelgaceya.pe",
        "first_name": "Sofía",
        "last_name": "Soporte",
        "role": Role.STAFF,
        "is_staff": True,
        "is_superuser": False,
    },
    {
        "email": "paciente@adelgaceya.pe",
        "first_name": "Ana",
        "last_name": "Quispe",
        "role": Role.PATIENT,
        "is_staff": False,
        "is_superuser": False,
    },
]


class Command(BaseCommand):
    help = "Create idempotent demo users and consents for local/staging environments."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--password",
            default="Adelgaceya2026!",
            help="Password for every demo account (must satisfy validators).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        password: str = options["password"]
        for spec in DEMO_USERS:
            user, _created = User.objects.get_or_create(
                email=spec["email"],
                defaults={
                    "first_name": spec["first_name"],
                    "last_name": spec["last_name"],
                    "role": spec["role"],
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                    "is_email_verified": True,
                    "email_verified_at": timezone.now(),
                },
            )
            user.role = spec["role"]
            user.is_staff = spec["is_staff"]
            user.is_superuser = spec["is_superuser"]
            user.is_email_verified = True
            user.email_verified_at = user.email_verified_at or timezone.now()
            user.set_password(password)
            user.save()
            for document_type in (
                ConsentRecord.DocumentType.TERMS,
                ConsentRecord.DocumentType.PRIVACY,
                ConsentRecord.DocumentType.HEALTH_DATA,
            ):
                ConsentRecord.objects.get_or_create(
                    user=user,
                    document_type=document_type,
                    version=settings.CONSENT_VERSIONS[document_type],
                    defaults={"accepted_at": timezone.now()},
                )
            self.stdout.write(self.style.SUCCESS(f"ready: {user.email} ({user.role})"))

        self.stdout.write(
            self.style.WARNING(
                "Demo password is well-known. Never run seed_demo against production."
            )
        )
