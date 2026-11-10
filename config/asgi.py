"""ASGI entrypoint.

The current messaging design is REST + polling. Django Channels can be added
later without schema changes; this module already exposes the ASGI callable so
the deployment target does not need to change.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_asgi_application()
