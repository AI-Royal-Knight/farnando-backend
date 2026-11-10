"""Pluggable captcha verification for public forms."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from importlib import import_module
from typing import Protocol, cast

from django.conf import settings
from rest_framework.exceptions import ValidationError


class CaptchaVerifier(Protocol):
    def verify(self, *, token: str, remote_ip: str | None) -> bool: ...


class NoopCaptchaVerifier:
    """Local/test only. Production must configure a real verifier."""

    def verify(self, *, token: str, remote_ip: str | None) -> bool:
        return bool(token)


class TurnstileCaptchaVerifier:
    verify_url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    def verify(self, *, token: str, remote_ip: str | None) -> bool:
        payload = {"secret": settings.CAPTCHA_SECRET_KEY, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip
        request = urllib.request.Request(  # noqa: S310 - fixed HTTPS endpoint
            self.verify_url,
            data=urllib.parse.urlencode(payload).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310  # nosec B310
                body = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False
        return bool(body.get("success"))


def get_captcha_verifier() -> CaptchaVerifier:
    module_path, _, class_name = settings.CAPTCHA_VERIFIER.rpartition(".")
    module = import_module(module_path)
    return cast(CaptchaVerifier, getattr(module, class_name)())


def verify_captcha(*, token: str | None, remote_ip: str | None) -> None:
    if not settings.CAPTCHA_REQUIRED and not token:
        return
    if not token:
        raise ValidationError({"captcha_token": ["This field is required."]})
    if not get_captcha_verifier().verify(token=token, remote_ip=remote_ip):
        raise ValidationError({"captcha_token": ["Captcha verification failed."]})
