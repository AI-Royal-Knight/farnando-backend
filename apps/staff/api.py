"""Shared view helpers for the staff and admin portals."""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any, cast

from drf_spectacular.openapi import AutoSchema
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.utils.encoders import JSONEncoder

from apps.accounts.models import User
from apps.core.idempotency import execute
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.schema import COMMON_ERRORS, error_response

IF_MATCH = OpenApiParameter(
    "If-Match", OpenApiTypes.STR, location=OpenApiParameter.HEADER, required=True
)
IDEMPOTENCY = OpenApiParameter(
    "Idempotency-Key", OpenApiTypes.UUID, location=OpenApiParameter.HEADER, required=True
)
CONCURRENCY_ERRORS = {
    412: error_response("Stale If-Match version."),
    428: error_response("If-Match header is required."),
}

Spec = dict[str, Any]


class PortalSchema(AutoSchema):
    """Operation ids from the view class name, so list and detail never collide."""

    def get_operation_id(self) -> str:
        name = type(self.view).__name__.removesuffix("View")
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        return f"{snake}_{self.method.lower()}"


def user_of(request: Request) -> User:
    return cast(User, request.user)


def doc(
    tag: str,
    summary: str,
    *,
    request: Any = None,
    responses: dict[int, Any] | None = None,
    parameters: list[OpenApiParameter] | None = None,
    auth: list[Any] | None = None,
) -> Callable[[Any], Any]:
    body = responses or {200: OpenApiTypes.OBJECT}
    kwargs: dict[str, Any] = {
        "tags": [tag],
        "summary": summary,
        "responses": {**body, **COMMON_ERRORS},
        "parameters": parameters or [],
    }
    kwargs["request"] = request
    if auth is not None:
        kwargs["auth"] = auth
    return extend_schema(**kwargs)


def query_params(names: Iterable[str]) -> list[OpenApiParameter]:
    return [OpenApiParameter(name, OpenApiTypes.STR, required=False) for name in names]


def params(request: Request, spec: Spec) -> dict[str, Any]:
    """Parse query parameters. ``spec`` maps name -> "str" | "uuid" | "date" | "bool" | tuple."""

    values: dict[str, Any] = {}
    errors: dict[str, list[str]] = {}
    for name, kind in spec.items():
        raw = request.query_params.get(name)
        if raw in (None, ""):
            continue
        assert raw is not None
        if kind == "str":
            values[name] = raw[:200]
        elif kind == "uuid":
            try:
                values[name] = uuid.UUID(raw)
            except ValueError:
                errors[name] = ["Must be a valid UUID."]
        elif kind == "date":
            try:
                values[name] = date.fromisoformat(raw)
            except ValueError:
                errors[name] = ["Use YYYY-MM-DD."]
        elif kind == "bool":
            if raw.lower() not in ("true", "false", "1", "0"):
                errors[name] = ["Use true or false."]
            else:
                values[name] = raw.lower() in ("true", "1")
        else:
            allowed = tuple(kind)
            if raw not in allowed:
                errors[name] = [f"Use one of: {', '.join(allowed)}."]
            else:
                values[name] = raw
    if errors:
        raise ValidationError(errors)
    return values


def page(
    request: Request,
    view: Any,
    queryset: Any,
    mapper: Callable[[list[Any]], list[dict[str, Any]]],
    **extra: Any,
) -> Response:
    paginator = DefaultPageNumberPagination()
    items = list(paginator.paginate_queryset(queryset, request, view=view) or [])
    body: dict[str, Any] = dict(extra)
    body.update(
        {
            "count": paginator.page.paginator.count,  # type: ignore[union-attr]
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": mapper(items),
        }
    )
    return Response(body)


def each(fn: Callable[[Any], dict[str, Any]]) -> Callable[[list[Any]], list[dict[str, Any]]]:
    return lambda items: [fn(item) for item in items]


def validated(serializer_class: Any, request: Request, *, partial: bool = False) -> dict[str, Any]:
    serializer = serializer_class(data=request.data, partial=partial)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def idempotent(
    request: Request,
    endpoint: str,
    fn: Callable[[], dict[str, Any]],
    *,
    status_code: int = status.HTTP_200_OK,
) -> Response:
    def callback() -> tuple[int, dict[str, Any]]:
        return status_code, json_safe(fn())

    result = execute(
        key=request.headers.get("Idempotency-Key"),
        endpoint=endpoint,
        payload=request.data,
        scope=str(request.user.pk),
        callback=callback,
    )
    return Response(result.body, status=result.status_code)


def json_safe(value: Any) -> Any:
    """Render payload dicts with DRF's encoder rules (UUIDs, dates, decimals)."""

    return json.loads(json.dumps(value, cls=JSONEncoder))
