"""Identity, roles and legally required consent records."""

from __future__ import annotations

import uuid
from typing import Any, ClassVar, cast

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.accounts.enums import Role


class UserManager(BaseUserManager):  # type: ignore[type-arg]
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        return (email or "").strip().lower()

    def create_user(self, email: str, password: str | None = None, **extra_fields: Any) -> User:
        email = self.normalize_email(email)
        if not email:
            raise ValueError("An email address is required.")
        extra_fields.setdefault("role", Role.PATIENT)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return cast("User", user)

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: Any
    ) -> User:
        extra_fields["role"] = Role.ADMIN
        extra_fields["is_staff"] = True
        extra_fields["is_superuser"] = True
        extra_fields["is_email_verified"] = True
        extra_fields.setdefault("email_verified_at", timezone.now())
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=120, blank=True)
    # Retained for admin imports/migrations; the API exposes only full_name.
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    role = models.CharField(
        max_length=16, choices=Role.choices, default=Role.PATIENT, db_index=True
    )
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(null=True, blank=True)
    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["role", "is_active"], name="accounts_user_role_active_idx"),
        ]

    def __str__(self) -> str:
        return self.email

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.email = self.__class__.objects.normalize_email(self.email)
        if not self.full_name:
            self.full_name = f"{self.first_name} {self.last_name}".strip()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)
        if not self.email:
            raise ValidationError({"email": "An email address is required."})

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ", 1)[0] if self.full_name else self.email

    @property
    def is_physician(self) -> bool:
        return self.role == Role.PHYSICIAN

    @property
    def is_patient(self) -> bool:
        return self.role == Role.PATIENT


class ConsentRecord(models.Model):
    class DocumentType(models.TextChoices):
        TERMS_OF_SERVICE = "TERMS_OF_SERVICE", "Terms of Service"
        PRIVACY_POLICY = "PRIVACY_POLICY", "Privacy Policy"
        HEALTH_DATA_PROCESSING = "HEALTH_DATA_PROCESSING", "Health data processing"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="consents")
    document_type = models.CharField(max_length=32, choices=DocumentType.choices)
    version = models.CharField(max_length=32)
    accepted_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-accepted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "document_type", "version"],
                name="accounts_consent_unique_version",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "document_type", "-accepted_at"],
                name="accounts_consent_user_type_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.document_type}:{self.version}"


class LegalDocument(models.Model):
    """Versioned legal copy. Published versions are immutable.

    Part 5 provides the content-management workflow. Part 1 only reads the
    current version and references its version in consent records.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document_type = models.CharField(max_length=32, choices=ConsentRecord.DocumentType.choices)
    version = models.CharField(max_length=32)
    title = models.CharField(max_length=200)
    sections = models.JSONField(default=list)
    published_at = models.DateTimeField(default=timezone.now)
    is_current = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["document_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["document_type", "version"],
                name="accounts_legal_document_unique_version",
            ),
            models.UniqueConstraint(
                fields=["document_type"],
                condition=models.Q(is_current=True),
                name="accounts_legal_document_one_current",
            ),
        ]
        indexes = [
            models.Index(
                fields=["document_type", "is_current"],
                name="accounts_legal_current_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.document_type}@{self.version}"
