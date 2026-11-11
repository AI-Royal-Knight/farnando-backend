"""Video provider port. ``DAILY`` is [OPEN]; the real adapter needs credentials."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Protocol

from django.conf import settings
from django.utils.module_loading import import_string

from apps.core.exceptions import ServiceUnavailable


class VideoProvider(Protocol):
    name: str

    def ensure_room(self, *, room_name: str, expires_at: datetime, recording: bool) -> str: ...

    def create_token(
        self,
        *,
        room_name: str,
        user_id: str,
        display_name: str,
        expires_at: datetime,
        is_owner: bool = False,
    ) -> str: ...

    def end_room(self, *, room_name: str) -> None: ...


class FakeVideoProvider:
    """Local/test provider: deterministic room URL, opaque random token."""

    name = "DAILY"

    def ensure_room(self, *, room_name: str, expires_at: datetime, recording: bool) -> str:
        _ = expires_at, recording
        return f"https://{settings.DAILY_DOMAIN}/{room_name}"

    def create_token(
        self,
        *,
        room_name: str,
        user_id: str,
        display_name: str,
        expires_at: datetime,
        is_owner: bool = False,
    ) -> str:
        _ = room_name, user_id, display_name, expires_at
        prefix = "fake_owner_" if is_owner else "fake_"
        return f"{prefix}{secrets.token_urlsafe(24)}"

    def end_room(self, *, room_name: str) -> None:
        _ = room_name


class DailyVideoProvider:
    """Production adapter placeholder until the Daily account is configured."""

    name = "DAILY"

    def ensure_room(self, *, room_name: str, expires_at: datetime, recording: bool) -> str:
        _ = room_name, expires_at, recording
        raise ServiceUnavailable("Video is not configured in this environment.")

    def create_token(
        self,
        *,
        room_name: str,
        user_id: str,
        display_name: str,
        expires_at: datetime,
        is_owner: bool = False,
    ) -> str:
        _ = room_name, user_id, display_name, expires_at, is_owner
        raise ServiceUnavailable("Video is not configured in this environment.")

    def end_room(self, *, room_name: str) -> None:
        _ = room_name
        raise ServiceUnavailable("Video is not configured in this environment.")


def get_video_provider() -> VideoProvider:
    path = str(settings.VIDEO_PROVIDER)
    if path.endswith("FakeVideoProvider"):
        return FakeVideoProvider()
    if path.endswith("DailyVideoProvider"):
        return DailyVideoProvider()
    return import_string(path)()  # type: ignore[no-any-return]


__all__ = ["DailyVideoProvider", "FakeVideoProvider", "VideoProvider", "get_video_provider"]
