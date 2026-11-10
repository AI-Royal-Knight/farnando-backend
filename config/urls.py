"""Root URL configuration."""

from __future__ import annotations

from csp.decorators import csp_update
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from apps.core.docs import ApiRedocView, ApiSchemaView, ApiSwaggerView
from apps.core.views import liveness, readiness

handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"

admin.site.site_header = "Adelgaceya administration"
admin.site.site_title = "Adelgaceya"
admin.site.index_title = "Operations"

_DOCS_CSP = {
    "script-src": ["'unsafe-inline'"],
    "style-src": ["'unsafe-inline'"],
    "img-src": ["'self'", "data:"],
    "font-src": ["'self'", "data:"],
    "connect-src": ["'self'"],
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", liveness, name="healthz"),
    path("readyz", readiness, name="readyz"),
    path("api/schema/", ApiSchemaView.as_view(), name="schema"),
    path(
        "api/docs/",
        csp_update(_DOCS_CSP)(ApiSwaggerView.as_view(url_name="schema")),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        csp_update(_DOCS_CSP)(ApiRedocView.as_view(url_name="schema")),
        name="redoc",
    ),
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.catalog.urls")),
    path("api/v1/", include("apps.support.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
