"""drf-spectacular extensions for custom authentication classes."""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.plumbing import build_bearer_security_scheme_object


class ContractJWTAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "apps.accounts.authentication.ContractJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return build_bearer_security_scheme_object(
            header_name="Authorization",
            token_prefix="Bearer",  # noqa: S106  # nosec B106
            bearer_format="JWT",
        )
