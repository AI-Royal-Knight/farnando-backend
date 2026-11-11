from __future__ import annotations

from django.urls import path

from apps.messaging import views

app_name = "messaging"

urlpatterns = [
    path("message-threads/", views.ThreadListCreateView.as_view(), name="threads"),
    path("message-threads/<uuid:thread_id>/", views.ThreadDetailView.as_view(), name="thread"),
    path(
        "message-threads/<uuid:thread_id>/messages/",
        views.ThreadMessagesView.as_view(),
        name="thread-messages",
    ),
    path(
        "message-threads/<uuid:thread_id>/read/",
        views.ThreadReadView.as_view(),
        name="thread-read",
    ),
    path(
        "messages/unread-count/",
        views.MessageUnreadCountView.as_view(),
        name="unread-count",
    ),
]
