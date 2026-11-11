"""Authentication, legal-document and consent routes under /api/v1/."""

from __future__ import annotations

from django.urls import path

from apps.accounts import views

app_name = "accounts"

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/google/", views.GoogleLoginView.as_view(), name="google-login"),
    path("auth/token/refresh/", views.RefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/verify-email/", views.VerifyEmailView.as_view(), name="verify-email"),
    path(
        "auth/verify-email/resend/",
        views.ResendVerificationView.as_view(),
        name="resend-verification",
    ),
    path("auth/password-reset/", views.PasswordResetRequestView.as_view(), name="password-reset"),
    path(
        "auth/password-reset/confirm/",
        views.PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
    ),
    path("auth/me/", views.MeView.as_view(), name="me"),
    path("auth/mfa/verify/", views.MfaVerifyView.as_view(), name="mfa-verify"),
    path("auth/mfa/totp/setup/", views.MfaSetupView.as_view(), name="mfa-setup"),
    path("auth/mfa/totp/confirm/", views.MfaConfirmView.as_view(), name="mfa-confirm"),
    path("auth/mfa/totp/disable/", views.MfaDisableView.as_view(), name="mfa-disable"),
    path("auth/change-password/", views.ChangePasswordView.as_view(), name="change-password"),
    path("legal-documents/", views.LegalDocumentListView.as_view(), name="legal-documents"),
    path(
        "legal-documents/<str:document_type>/",
        views.LegalDocumentDetailView.as_view(),
        name="legal-document-detail",
    ),
    path("consents/me/", views.ConsentMeView.as_view(), name="consents-me"),
    path("consents/", views.ConsentCreateView.as_view(), name="consents"),
]
