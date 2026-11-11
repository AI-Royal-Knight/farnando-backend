"""Invitations, specialties, avatars and the physician profile (Part 4, section 2)."""

from __future__ import annotations

import hashlib
import io
import secrets
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.accounts.models import ConsentRecord, User
from apps.accounts.services import ConsentService
from apps.clinical.models import AvatarUpload, InvitationStatus, PhysicianInvitation, Specialty
from apps.clinical.models import UploadStatus as Status
from apps.consultations.models import CareRole, ClinicianProfile, PhysicianVerificationStatus
from apps.core.audit import record_audit
from apps.core.exceptions import (
    ConsentRequiredInput,
    FileTooLarge,
    InvalidStateTransition,
    InvitationAlreadyUsed,
    InvitationExpired,
    InvitationRevoked,
    ReadOnlyField,
    UnknownField,
)
from apps.core.storage import delete_object, object_exists, presigned_upload_url, presigned_url
from apps.patients.services import valid_timezone, validate_e164

AVATAR_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
AVATAR_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
UPLOAD_TTL = 15 * 60


def hash_token(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def create_invitation(
    *, email: str, invited_by: User | None = None
) -> tuple[PhysicianInvitation, str]:
    """Used by the admin and Part 5. Returns the raw token exactly once."""

    token = secrets.token_urlsafe(32)
    invitation = PhysicianInvitation.objects.create(
        email=email.strip().lower(),
        token_hash=hash_token(token),
        expires_at=timezone.now() + timedelta(days=settings.PHYSICIAN_INVITATION_TTL_DAYS),
        invited_by=invited_by,
    )
    return invitation, token


def resolve_invitation(token: str, *, lock: bool = False) -> PhysicianInvitation:
    from rest_framework.exceptions import NotFound

    queryset = PhysicianInvitation.objects.select_related("invited_by")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    invitation = queryset.filter(token_hash=hash_token(token)).first()
    if invitation is None:
        raise NotFound()
    if invitation.status == InvitationStatus.REVOKED:
        raise InvitationRevoked()
    if invitation.status == InvitationStatus.ACCEPTED:
        raise InvitationAlreadyUsed()
    if invitation.status == InvitationStatus.EXPIRED or invitation.expires_at <= timezone.now():
        if invitation.status != InvitationStatus.EXPIRED:
            invitation.status = InvitationStatus.EXPIRED
            invitation.save(update_fields=["status", "updated_at"])
        raise InvitationExpired()
    return invitation


def _validate_consents(items: list[dict[str, Any]]) -> dict[str, Any]:
    documents = ConsentService.validate_items(items, require_required=True)
    if ConsentRecord.DocumentType.CLINICIAN_AGREEMENT not in documents:
        raise ConsentRequiredInput()
    return documents


def accept_invitation(*, token: str, data: dict[str, Any], request: Any) -> User:
    from django.contrib.auth.password_validation import validate_password

    with transaction.atomic():
        invitation = resolve_invitation(token, lock=True)
        email = invitation.email
        if User.objects.filter(email=email).exists():
            raise ValidationError({"email": ["An account already exists for this email."]})
        specialty = Specialty.objects.filter(pk=data["specialty_id"], is_active=True).first()
        if specialty is None:
            raise ValidationError({"specialty_id": ["Unknown specialty."]})
        registration = (data.get("specialty_registration_number") or "").strip().upper()
        if specialty.requires_registration_number and not registration:
            raise ValidationError(
                {"specialty_registration_number": ["Required for this specialty."]}
            )
        license_number = data["license_number"].strip().upper()
        if ClinicianProfile.objects.filter(license_number__iexact=license_number).exists():
            raise ValidationError(
                {"license_number": ["This license number is already registered."]}
            )
        if data["password"] != data["confirm_password"]:
            raise ValidationError({"confirm_password": ["Passwords do not match."]})
        candidate = User(email=email, full_name=data["full_name"])
        try:
            validate_password(data["password"], candidate)
        except Exception as exc:
            messages = list(getattr(exc, "messages", [str(exc)]))
            raise ValidationError({"password": messages}) from exc
        _validate_consents(data["consents"])
        avatar = None
        if data.get("avatar_upload_id"):
            avatar = AvatarUpload.objects.filter(
                pk=data["avatar_upload_id"], invitation=invitation, status=Status.READY
            ).first()
            if avatar is None:
                raise ValidationError({"avatar_upload_id": ["Unknown or unconfirmed upload."]})

        user = User.objects.create_user(
            email=email,
            password=data["password"],
            full_name=data["full_name"].strip(),
            role="PHYSICIAN",
            is_email_verified=True,
            email_verified_at=timezone.now(),
        )
        ClinicianProfile.objects.create(
            user=user,
            care_role=CareRole.PHYSICIAN,
            display_name=data["full_name"].strip(),
            specialty=specialty.name,
            specialty_ref=specialty,
            specialty_registration_number=registration,
            license_number=license_number,
            license_label=f"CMP {license_number}",
            phone=data["phone"],
            verification_status=PhysicianVerificationStatus.PENDING,
            avatar_key=avatar.storage_key if avatar else "",
            languages=["es"],
        )
        if avatar is not None:
            avatar.owner = user
            avatar.save(update_fields=["owner", "updated_at"])
        django_request = getattr(request, "_request", request)
        ConsentService.record_many(
            user=user, items=data["consents"], request=django_request, require_required=True
        )
        invitation.status = InvitationStatus.ACCEPTED
        invitation.accepted_user = user
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["status", "accepted_user", "accepted_at", "updated_at"])
        record_audit(
            actor=user,
            action="physician.account_created",
            object_type="User",
            object_id=user.pk,
            request=request,
        )
        from apps.clinical.tasks import send_staff_alert

        transaction.on_commit(lambda: send_staff_alert.delay("physician_pending_verification"))
    return user


# ---------------------------------------------------------------------------
# Avatars
# ---------------------------------------------------------------------------


def avatar_url_for(profile: ClinicianProfile | None) -> str | None:
    if profile is None:
        return None
    if profile.avatar_key:
        return presigned_url(profile.avatar_key, expires_in=settings.AVATAR_URL_TTL_SECONDS)
    return profile.avatar_url or None


def start_avatar_upload(
    *, user: User | None, invitation: PhysicianInvitation | None, data: dict[str, Any]
) -> tuple[AvatarUpload, dict[str, Any]]:
    content_type = data["content_type"]
    size = int(data["size_bytes"])
    if size < 1 or size > settings.AVATAR_MAX_BYTES:
        raise FileTooLarge()
    key = f"avatars/{uuid.uuid4().hex}.{AVATAR_EXT[content_type]}"
    upload = AvatarUpload.objects.create(
        owner=user,
        invitation=invitation,
        storage_key=key,
        content_type=content_type,
        size_bytes=size,
    )
    url, headers = presigned_upload_url(key, content_type=content_type, expires_in=UPLOAD_TTL)
    return upload, {
        "method": "PUT",
        "url": url,
        "headers": headers,
        "expires_at": timezone.now() + timedelta(seconds=UPLOAD_TTL),
    }


def confirm_avatar(upload: AvatarUpload) -> AvatarUpload:
    """Decode, cap dimensions, drop metadata and re-encode the image in place."""

    from PIL import Image, UnidentifiedImageError

    if upload.status != Status.PENDING_UPLOAD:
        raise InvalidStateTransition()
    if not object_exists(upload.storage_key):
        raise ValidationError({"upload": ["The file was not uploaded."]})
    with default_storage.open(upload.storage_key, "rb") as handle:
        raw = handle.read(settings.AVATAR_MAX_BYTES + 1)
    if len(raw) > settings.AVATAR_MAX_BYTES:
        upload.status = Status.REJECTED
        upload.save(update_fields=["status", "updated_at"])
        raise FileTooLarge()
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        image: Image.Image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        upload.status = Status.REJECTED
        upload.save(update_fields=["status", "updated_at"])
        raise ValidationError({"upload": ["The file is not a valid image."]}) from exc
    if image.format not in AVATAR_TYPES.values():
        upload.status = Status.REJECTED
        upload.save(update_fields=["status", "updated_at"])
        raise ValidationError({"upload": ["Unsupported image type."]})
    limit = settings.AVATAR_MAX_PIXELS
    if image.width > limit or image.height > limit:
        upload.status = Status.REJECTED
        upload.save(update_fields=["status", "updated_at"])
        raise ValidationError({"upload": [f"Images must be at most {limit} px per side."]})
    target_format = image.format
    if target_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    # Copy pixels only: EXIF, ICC, XMP and text chunks are not carried over.
    clean = Image.new(image.mode, image.size)
    clean.putdata(list(image.getdata()))
    if image.mode == "P" and image.getpalette():
        clean.putpalette(image.getpalette() or [])
    buffer = io.BytesIO()
    clean.save(buffer, format=target_format)
    delete_object(upload.storage_key)
    default_storage.save(upload.storage_key, ContentFile(buffer.getvalue()))
    content_type = next(key for key, value in AVATAR_TYPES.items() if value == target_format)
    upload.status = Status.READY
    upload.content_type = content_type
    upload.size_bytes = len(buffer.getvalue())
    upload.save(update_fields=["status", "content_type", "size_bytes", "updated_at"])
    return upload


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

PROFILE_EDITABLE = {
    "avatar_upload_id",
    "headline",
    "bio",
    "phone",
    "accepting_new_consultations",
    "timezone",
    "languages",
}
PROFILE_READ_ONLY = {
    "id",
    "user",
    "full_name",
    "license_number",
    "specialty",
    "specialty_id",
    "specialty_registration_number",
    "verification_status",
    "verification_note",
    "avatar_url",
    "created_at",
}


def update_profile(user: User, profile: ClinicianProfile, data: dict[str, Any]) -> ClinicianProfile:
    for key in data:
        if key in PROFILE_READ_ONLY:
            raise ReadOnlyField(key)
        if key not in PROFILE_EDITABLE:
            raise UnknownField(key)
    fields: list[str] = []
    if "headline" in data:
        headline = str(data["headline"] or "").strip()
        if len(headline) > 120:
            raise ValidationError({"headline": ["Max 120 characters."]})
        profile.headline = headline
        fields.append("headline")
    if "bio" in data:
        bio = str(data["bio"] or "").strip()
        if len(bio) > 1000:
            raise ValidationError({"bio": ["Max 1000 characters."]})
        profile.bio = bio
        fields.append("bio")
    if "phone" in data:
        profile.phone = validate_e164(str(data["phone"] or ""), "phone")
        fields.append("phone")
    if "accepting_new_consultations" in data:
        if not isinstance(data["accepting_new_consultations"], bool):
            raise ValidationError({"accepting_new_consultations": ["Must be a boolean."]})
        profile.accepts_new_consultations = data["accepting_new_consultations"]
        fields.append("accepts_new_consultations")
    if "timezone" in data:
        if not isinstance(data["timezone"], str) or not valid_timezone(data["timezone"]):
            raise ValidationError({"timezone": ["Unknown IANA timezone."]})
        profile.timezone = data["timezone"]
        fields.append("timezone")
    if "languages" in data:
        languages = data["languages"]
        if not isinstance(languages, list) or any(item not in ("es", "en") for item in languages):
            raise ValidationError({"languages": ["Use es and/or en."]})
        profile.languages = list(dict.fromkeys(languages))
        fields.append("languages")
    if "avatar_upload_id" in data:
        avatar = AvatarUpload.objects.filter(
            pk=data["avatar_upload_id"], owner=user, status=Status.READY
        ).first()
        if avatar is None:
            raise ValidationError({"avatar_upload_id": ["Unknown or unconfirmed upload."]})
        profile.avatar_key = avatar.storage_key
        fields.append("avatar_key")
    if fields:
        profile.save(update_fields=[*fields, "updated_at"])
    return profile


def notify_verification_change(profile: ClinicianProfile) -> None:
    from apps.notifications.models import NotificationType
    from apps.notifications.services import notify

    mapping: dict[str, str] = {
        PhysicianVerificationStatus.VERIFIED: NotificationType.CREDENTIALS_VERIFIED,
        PhysicianVerificationStatus.REJECTED: NotificationType.CREDENTIALS_REJECTED,
    }
    notification_type = mapping.get(profile.verification_status)
    if notification_type is not None:
        notify(
            profile.user, notification_type, target_kind="physician_profile", target_id=profile.pk
        )


__all__ = [
    "accept_invitation",
    "avatar_url_for",
    "confirm_avatar",
    "create_invitation",
    "hash_token",
    "notify_verification_change",
    "resolve_invitation",
    "start_avatar_upload",
    "update_profile",
]
