from __future__ import annotations

from django.urls import path

from apps.payments import views

app_name = "payments"

urlpatterns = [
    path("payments/<uuid:payment_id>/", views.PaymentDetailView.as_view(), name="payment"),
    path(
        "payment-methods/",
        views.PaymentMethodListCreateView.as_view(),
        name="payment-methods",
    ),
    path(
        "payment-methods/<uuid:method_id>/",
        views.PaymentMethodDetailView.as_view(),
        name="payment-method",
    ),
    path(
        "payment-methods/<uuid:method_id>/default/",
        views.PaymentMethodDefaultView.as_view(),
        name="payment-method-default",
    ),
    path("webhooks/culqi/", views.CulqiWebhookView.as_view(), name="webhook-culqi"),
    path("webhooks/video/daily/", views.DailyWebhookView.as_view(), name="webhook-daily"),
]
