"""Private object-storage helpers.

User uploads must never be public. The S3 storage class below is used only for
media; static files stay on WhiteNoise. URLs are short-lived pre-signed links.
"""

from __future__ import annotations

from typing import Any, cast

from django.core.files.storage import default_storage
from storages.backends.s3 import S3Storage


class PrivateS3Storage(S3Storage):  # type: ignore[misc]
    default_acl = None
    file_overwrite = False
    querystring_auth = True
    querystring_expire = 300

    def url(self, name: str, parameters: dict[str, Any] | None = None, expire: int = 300) -> str:
        params = dict(parameters or {})
        params.setdefault("ResponseContentDisposition", "attachment")
        return cast(str, super().url(name, parameters=params, expire=expire))


def presigned_url(name: str, *, expires_in: int = 300) -> str:
    """Return a short-lived download URL from the configured media storage."""

    if hasattr(default_storage, "url"):
        try:
            return default_storage.url(name, expire=expires_in)  # type: ignore[call-arg]
        except TypeError:
            return default_storage.url(name)
    raise RuntimeError("Configured storage does not support URLs")
