from __future__ import annotations

from django.urls import path

from apps.orders import views

app_name = "orders"

urlpatterns = [
    path(
        "programs/<uuid:program_id>/packages/",
        views.ProgramPackagesView.as_view(),
        name="program-packages",
    ),
    path("orders/quote/", views.OrderQuoteView.as_view(), name="order-quote"),
    path("orders/", views.OrderListCreateView.as_view(), name="orders"),
    path("orders/<uuid:order_id>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<uuid:order_id>/pay/", views.OrderPayView.as_view(), name="order-pay"),
    path("orders/<uuid:order_id>/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path("subscriptions/", views.SubscriptionListView.as_view(), name="subscriptions"),
    path(
        "subscriptions/<uuid:subscription_id>/",
        views.SubscriptionDetailView.as_view(),
        name="subscription-detail",
    ),
    path(
        "subscriptions/<uuid:subscription_id>/pause/",
        views.SubscriptionPauseView.as_view(),
        name="subscription-pause",
    ),
    path(
        "subscriptions/<uuid:subscription_id>/resume/",
        views.SubscriptionResumeView.as_view(),
        name="subscription-resume",
    ),
    path(
        "subscriptions/<uuid:subscription_id>/cancel/",
        views.SubscriptionCancelView.as_view(),
        name="subscription-cancel",
    ),
]
