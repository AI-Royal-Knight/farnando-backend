"""Email port and Django/AnyMail adapter.

Task arguments never contain recipient addresses or message bodies; tasks load
the user by id and render the template inside the worker.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string


class EmailService(Protocol):
    def send(self, *, to: str, subject: str, template: str, context: dict[str, object]) -> None: ...


class DjangoEmailService:
    """Renders ``emails/<template>.txt`` and optional sibling ``.html``."""

    def send(self, *, to: str, subject: str, template: str, context: dict[str, object]) -> None:
        text_body = render_to_string(f"emails/{template}.txt", context)
        message = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
        )
        try:
            html_body = render_to_string(f"emails/{template}.html", context)
        except TemplateDoesNotExist:
            pass
        else:
            message.attach_alternative(html_body, "text/html")
        message.send(fail_silently=False)


_email_service: EmailService | None = None


def get_email_service() -> EmailService:
    global _email_service
    if _email_service is None:
        module_path, _, class_name = settings.EMAIL_SERVICE.rpartition(".")
        module = import_module(module_path)
        service_class = getattr(module, class_name)
        _email_service = service_class()
    return _email_service
