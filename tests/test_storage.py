"""Private storage URL helper (filesystem fallback for tests)."""

from __future__ import annotations

from django.test import override_settings

from apps.core.storage import presigned_url


def test_filesystem_storage_returns_media_url() -> None:
    with override_settings(
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        },
        MEDIA_URL="/media/",
    ):
        assert presigned_url("labs/report.pdf") == "/media/labs/report.pdf"
