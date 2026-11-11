from __future__ import annotations

from django.urls import path

from apps.notifications import views

app_name = "notifications"

urlpatterns = [
    path("notifications/", views.NotificationListView.as_view(), name="list"),
    path("notifications/read-all/", views.NotificationReadAllView.as_view(), name="read-all"),
    path(
        "notifications/unread-count/",
        views.NotificationUnreadCountView.as_view(),
        name="unread-count",
    ),
    path(
        "notifications/<uuid:notification_id>/read/",
        views.NotificationReadView.as_view(),
        name="read",
    ),
]
