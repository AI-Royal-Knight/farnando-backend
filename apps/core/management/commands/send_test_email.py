"""Send a test email through the configured backend (SMTP/Zoho, console, etc.)."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a test email to an address using the current EMAIL_* settings."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("to", help="Recipient address, e.g. contacto@adelgaceya.pe")

    def handle(self, *args: Any, **options: Any) -> None:
        to: str = options["to"]
        self.stdout.write(
            f"backend={settings.EMAIL_BACKEND} host={settings.EMAIL_HOST}:{settings.EMAIL_PORT} "
            f"from={settings.DEFAULT_FROM_EMAIL}"
        )
        sent = send_mail(
            subject="Adelgaceya email test",
            message=(
                "This is a test from the Adelgaceya backend. "
                "If you received it, outbound email is configured correctly."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} email(s) to {to}"))
