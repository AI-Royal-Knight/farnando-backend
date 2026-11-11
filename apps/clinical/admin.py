"""Admin for Part 4 data that Part 5 will expose through the staff API."""

from __future__ import annotations

from typing import Any

from django.contrib import admin, messages

from apps.clinical.models import (
    AvatarUpload,
    ClinicalNote,
    PhysicianInvitation,
    PrescriptionDocument,
    Specialty,
)


@admin.register(Specialty)
class SpecialtyAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "requires_registration_number", "is_active", "position")
    list_editable = ("requires_registration_number", "is_active", "position")


@admin.register(PhysicianInvitation)
class PhysicianInvitationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Saving a new invitation shows the link token once; only its hash is stored."""

    list_display = ("email", "status", "expires_at", "invited_by", "accepted_at")
    list_filter = ("status",)
    fields = ("email", "status", "expires_at", "invited_by", "accepted_user", "accepted_at")
    readonly_fields = ("expires_at", "invited_by", "accepted_user", "accepted_at")

    def save_model(self, request: Any, obj: PhysicianInvitation, form: Any, change: bool) -> None:
        if change:
            super().save_model(request, obj, form, change)
            return
        from apps.clinical.onboarding import create_invitation

        invitation, token = create_invitation(email=obj.email, invited_by=request.user)
        obj.pk = invitation.pk
        messages.warning(
            request,
            f"Invitation token (shown once): {token}. Link: /physician-invitations/{token}",
        )


@admin.register(PrescriptionDocument)
class PrescriptionDocumentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("evaluation", "uploaded_by", "status", "created_at")
    raw_id_fields = ("evaluation", "uploaded_by")


@admin.register(AvatarUpload)
class AvatarUploadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("storage_key", "owner", "status", "created_at")
    raw_id_fields = ("owner", "invitation")


@admin.register(ClinicalNote)
class ClinicalNoteAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    """Notes are append-only; the admin shows metadata only."""

    list_display = ("patient", "author", "kind", "created_at")
    exclude = ("body",)
    raw_id_fields = ("patient", "author", "consultation")

    def has_change_permission(self, request: Any, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: Any, obj: Any = None) -> bool:
        return False
