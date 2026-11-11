"""Idempotent demo data for every role and (eventually) every screen.

Creates one identity per role (plus a nurse, who is a STAFF user with a NURSE
clinician profile), their consents, public content and the Part 3 care-team
data: clinician profiles, weekly availability, the patient's assigned nurse and
a deliverable address. Treatments and orders need a physician decision (Part 4).
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from apps.accounts.enums import Role
from apps.accounts.models import ConsentRecord, LegalDocument, User
from apps.catalog.models import FAQ, PublicStats, SiteSettings, Testimonial

DEMO_USERS = [
    {
        "email": "admin@adelgaceya.pe",
        "full_name": "Ada Administradora",
        "role": Role.ADMIN,
        "is_staff": True,
        "is_superuser": True,
    },
    {
        "email": "marcus.vance@adelgaceya.pe",
        "full_name": "Marcus Vance",
        "role": Role.PHYSICIAN,
        "is_staff": False,
        "is_superuser": False,
    },
    {
        "email": "staff@adelgaceya.pe",
        "full_name": "Sofía Soporte",
        "role": Role.STAFF,
        "is_staff": True,
        "is_superuser": False,
    },
    {
        "email": "sarah.nurse@adelgaceya.pe",
        "full_name": "Sarah Torres",
        "role": Role.STAFF,
        "is_staff": True,
        "is_superuser": False,
    },
    {
        "email": "paciente@adelgaceya.pe",
        "full_name": "Ana Quispe",
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
                    "full_name": spec["full_name"],
                    "role": spec["role"],
                    "is_staff": spec["is_staff"],
                    "is_superuser": spec["is_superuser"],
                    "is_email_verified": True,
                    "email_verified_at": timezone.now(),
                },
            )
            user.full_name = spec["full_name"]
            user.role = spec["role"]
            user.is_staff = spec["is_staff"]
            user.is_superuser = spec["is_superuser"]
            user.is_email_verified = True
            user.email_verified_at = user.email_verified_at or timezone.now()
            user.set_password(password)
            user.save()
            for document_type in ConsentRecord.DocumentType.values:
                if document_type == "CLINICIAN_AGREEMENT" and spec["role"] != Role.PHYSICIAN:
                    continue
                document = LegalDocument.objects.filter(
                    document_type=document_type, is_current=True
                ).first()
                version = document.version if document else settings.CONSENT_VERSIONS[document_type]
                ConsentRecord.objects.get_or_create(
                    user=user,
                    document_type=document_type,
                    version=version,
                    defaults={"accepted_at": timezone.now()},
                )
            self.stdout.write(self.style.SUCCESS(f"ready: {user.email} ({user.role})"))

        site = SiteSettings.load()
        # Contract Part 1 v1.2 demo values. Replace with real data before launch;
        # the API always reads these from the database, never from code.
        site.contact_email = "contact@adelgaceya.pe"
        site.contact_phone = "+51 1 555 0100"
        site.address = "Av. Larco 1234, Miraflores, Lima 15074, Perú"
        site.legal_entity_name = "Adelgaceya"
        site.social_links = [
            {"platform": "INSTAGRAM", "url": "https://instagram.com/adelgaceya"},
            {"platform": "FACEBOOK", "url": "https://facebook.com/adelgaceya"},
        ]
        site.save()

        stats = PublicStats.load()
        stats.sustained_weight_loss_percent = 94.0
        stats.consultations_delivered = 50000
        stats.average_reply_minutes = 14
        stats.save()

        faqs = [
            (
                "How does the online doctor consultation work?",
                "Book a consultation, complete your assessment and meet your physician by video.",
            ),
            (
                "Are the treatments authentic and regulated?",
                "Treatments are dispensed by licensed pharmacies; your physician explains your protocol.",  # noqa: E501
            ),
            (
                "What does the monthly price include?",
                "Your programme includes physician follow-up and the medication protocol at checkout.",  # noqa: E501
            ),
            (
                "How is my medication delivered?",
                "Medication is delivered to the address you confirm during onboarding.",
            ),
            (
                "Can I pause or cancel my subscription?",
                "You can manage or cancel your subscription from your patient portal.",
            ),
        ]
        for position, (question, answer) in enumerate(faqs):
            FAQ.objects.update_or_create(
                question=question,
                defaults={"answer_markdown": answer, "position": position},
            )
        testimonials = [
            ("Kendra L.", 5, "The process was clear and my physician was excellent."),
            ("María G.", 5, "I finally have a plan that fits my life."),
            ("Diego R.", 4, "The portal and follow-up made the difference."),
        ]
        for author_name, rating, body in testimonials:
            Testimonial.objects.update_or_create(
                author_name=author_name,
                defaults={
                    "rating": rating,
                    "body": body,
                    "published_at": timezone.now(),
                    "is_published": True,
                    "patient_consent_recorded": True,
                },
            )

        self._seed_care_team()

        self.stdout.write(
            self.style.WARNING(
                "Demo password is well-known. Never run seed_demo against production."
            )
        )

    def _seed_care_team(self) -> None:
        from datetime import time

        from apps.clinical.models import Specialty
        from apps.consultations.models import AvailabilityRule, CareRole, ClinicianProfile
        from apps.patients.models import Address, Ubigeo
        from apps.patients.services import create_address, get_or_create_profile

        physician = User.objects.get(email="marcus.vance@adelgaceya.pe")
        nurse = User.objects.get(email="sarah.nurse@adelgaceya.pe")
        patient = User.objects.get(email="paciente@adelgaceya.pe")
        ClinicianProfile.objects.update_or_create(
            user=physician,
            defaults={
                "care_role": CareRole.PHYSICIAN,
                "display_name": "Dr. Marcus Vance, MD",
                "headline": "Board-Certified Endocrinology & Metabolic Care",
                "specialty": "Endocrinology",
                "specialty_ref": Specialty.objects.filter(name="Endocrinology").first(),
                "license_number": "12345",
                "specialty_registration_number": "RNE6789",
                "license_label": "CMP 12345",
                "verification_status": "VERIFIED",
                "phone": "+51987650000",
                "languages": ["es", "en"],
                "accepts_new_consultations": True,
                "timezone": "America/Lima",
            },
        )
        ClinicianProfile.objects.update_or_create(
            user=nurse,
            defaults={
                "care_role": CareRole.NURSE,
                "display_name": "Nurse Sarah",
                "headline": "Patient care coordinator",
                "accepts_new_consultations": False,
            },
        )
        # Monday-Friday 09:00-18:00 Lima time with a recurring BLOCKED lunch break.
        for weekday in range(1, 6):
            AvailabilityRule.objects.get_or_create(
                physician=physician,
                weekday=weekday,
                kind="AVAILABLE",
                start_time=time(9),
                end_time=time(18),
            )
            AvailabilityRule.objects.get_or_create(
                physician=physician,
                weekday=weekday,
                kind="BLOCKED",
                start_time=time(13),
                end_time=time(14),
                defaults={"label": "Lunch break"},
            )
        profile = get_or_create_profile(patient)
        profile.assigned_nurse = nurse
        profile.phone = "+51987654321"
        profile.save(update_fields=["assigned_nurse", "phone", "updated_at"])
        miraflores = Ubigeo.objects.filter(code="150122").first()
        if miraflores is not None and not Address.objects.filter(user=patient).exists():
            create_address(
                patient,
                {
                    "label": "Home",
                    "recipient_name": "Ana Quispe",
                    "phone": "+51987654321",
                    "line1": "Av. Larco 1234",
                    "line2": "Dpto. 502",
                    "reference": "Frente al parque Kennedy",
                    "ubigeo": miraflores,
                },
            )
