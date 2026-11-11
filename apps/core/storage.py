"""Private object-storage helpers.

User uploads must never be public. The S3 storage class below is used only for
media; static files stay on WhiteNoise. URLs are short-lived pre-signed links.
"""

from __future__ import annotations

from typing import Any, cast

from django.core.files.storage import default_storage
from storages.backends.s3 import S3Storage

try:  # boto3 is an optional runtime dependency for local filesystem tests.
    import boto3
except ImportError:  # pragma: no cover - production always installs boto3
    boto3 = None


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


def presigned_upload_url(
    name: str, *, content_type: str, expires_in: int = 900
) -> tuple[str, dict[str, str]]:
    """Return a short-lived PUT URL and the headers the client must send.

    S3-compatible storage returns a real pre-signed URL. The local
    FileSystemStorage fallback cannot enforce a signature; this is development
    only and documented in the deviations file.
    """

    storage = default_storage
    if hasattr(storage, "bucket_name") and boto3 is not None:
        client = boto3.client(
            "s3",
            region_name=getattr(storage, "region_name", None),
            endpoint_url=getattr(storage, "endpoint_url", None) or None,
            aws_access_key_id=getattr(storage, "access_key", None) or None,
            aws_secret_access_key=getattr(storage, "secret_key", None) or None,
            config=None,
        )
        url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": storage.bucket_name,
                "Key": name,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return cast(str, url), {"Content-Type": content_type}
    return storage.url(name), {"Content-Type": content_type}


def object_exists(name: str) -> bool:
    try:
        return bool(default_storage.exists(name))
    except Exception:
        return False


def object_size(name: str) -> int:
    try:
        return int(default_storage.size(name))
    except Exception:
        return 0


def read_object_head(name: str, length: int = 32) -> bytes:
    with default_storage.open(name, "rb") as handle:
        return bytes(handle.read(length))


def delete_object(name: str) -> None:
    try:
        default_storage.delete(name)
    except Exception:
        return
