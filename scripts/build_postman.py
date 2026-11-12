"""Build a Postman collection (v2.1) from the generated OpenAPI schema.

Usage:
    make postman            # regenerates openapi.yaml, then postman.json
    uv run python scripts/build_postman.py [openapi.yaml] [postman.json]

The collection is grouped by OpenAPI tag (large tags are split by resource),
uses collection variables for the base URL, tokens and path ids, and stores
tokens automatically after login, MFA verify, Google sign-in and refresh.
"""

from __future__ import annotations

import json
import re
import sys
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

import yaml

METHODS = ("get", "post", "put", "patch", "delete")
SPLIT_THRESHOLD = 15
PATH_PARAM = re.compile(r"\{([^}]+)\}")
API_PREFIX = "/api/v1/"

DEMO_ACCOUNTS = {
    "patient_email": "paciente@adelgaceya.pe",
    "physician_email": "marcus.vance@adelgaceya.pe",
    "staff_email": "staff@adelgaceya.pe",
    "admin_email": "admin@adelgaceya.pe",
}

SAVE_TOKENS = """
let body = {};
try { body = pm.response.json(); } catch (e) { return; }
const tokens = body.tokens || body;
if (tokens.access) { pm.collectionVariables.set("access_token", tokens.access); }
if (tokens.refresh) { pm.collectionVariables.set("refresh_token", tokens.refresh); }
if (body.mfa_token) { pm.collectionVariables.set("mfa_token", body.mfa_token); }
pm.test("tokens or MFA challenge returned", function () {
    pm.expect(Boolean(tokens.access || body.mfa_token)).to.be.true;
});
""".strip()

SAVE_CREATED_ID = """
if (pm.response.code === 201) {
    try {
        const body = pm.response.json();
        if (body && body.id) { pm.collectionVariables.set("last_created_id", body.id); }
    } catch (e) {}
}
""".strip()

BODY_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("post", "/api/v1/auth/login/"): {"email": "{{email}}", "password": "{{password}}"},
    ("post", "/api/v1/auth/token/refresh/"): {"refresh": "{{refresh_token}}"},
    ("post", "/api/v1/auth/logout/"): {"refresh": "{{refresh_token}}"},
    ("post", "/api/v1/auth/mfa/verify/"): {"mfa_token": "{{mfa_token}}", "code": "123456"},
}
# Request bodies documented as a plain JSON object in the schema.
REFERENCE_BODIES: dict[str, dict[str, Any]] = {
    "faqs": {
        "question": "Is the treatment safe?",
        "answer_markdown": "Every plan is supervised by a licensed physician.",
        "position": 1,
        "is_published": True,
    },
    "testimonials": {
        "author_name": "Ana",
        "rating": 5,
        "body": "Great experience with the whole team.",
        "patient_consent_recorded": True,
        "is_published": True,
    },
    "complaint-categories": {
        "code": "BILLING_ISSUE",
        "label": "Billing issue",
        "position": 9,
        "is_active": True,
    },
    "support-ticket-categories": {
        "code": "SHIPPING_DELAY",
        "label": "Shipping delay",
        "requires_clinical_staff": False,
        "position": 9,
        "is_active": True,
    },
    "specialties": {
        "name": "Sports Medicine",
        "requires_registration_number": True,
        "is_active": True,
    },
    "consultation-types": {
        "code": "NUTRITION_FOLLOW_UP",
        "name": "Nutrition follow-up",
        "description": "",
        "duration_minutes": 30,
        "is_initial": False,
        "is_active": True,
    },
    "lab-panels": {
        "code": "LIPID_PANEL",
        "name": "Lipid panel",
        "description": "Total cholesterol, HDL, LDL and triglycerides.",
        "is_active": True,
    },
}
for _resource, _body in REFERENCE_BODIES.items():
    BODY_OVERRIDES[("post", f"/api/v1/admin/{_resource}/")] = _body
    BODY_OVERRIDES[("patch", f"/api/v1/admin/{_resource}/{{id}}/")] = dict(list(_body.items())[1:2])
BODY_OVERRIDES[("put", "/api/v1/admin/settings/{group}/")] = {
    "first_response_sla_minutes": 60,
    "auto_close_resolved_days": 7,
    "reason": "Adjust the support SLA",
}
# Path variables with a sensible default (the settings body above is the support group).
PATH_DEFAULTS = {"group": "support"}

TOKEN_ENDPOINTS = {
    "/api/v1/auth/login/",
    "/api/v1/auth/google/",
    "/api/v1/auth/mfa/verify/",
    "/api/v1/auth/token/refresh/",
}


class Schema:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document
        self.path_variables: set[str] = set()

    def resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        seen = 0
        while "$ref" in node and seen < 20:
            parts = node["$ref"].lstrip("#/").split("/")
            target: Any = self.document
            for part in parts:
                target = target[part]
            node = target
            seen += 1
        return node

    def example(self, node: dict[str, Any], name: str = "", depth: int = 0) -> Any:
        node = self.resolve(node)
        if depth > 6:
            return None
        if "example" in node:
            return node["example"]
        if "default" in node and node["default"] is not None:
            return node["default"]
        if "allOf" in node:
            merged: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for part in node["allOf"]:
                part = self.resolve(part)
                if "enum" in part or part.get("type") not in (None, "object"):
                    return self.example(part, name, depth + 1)
                merged["properties"].update(part.get("properties", {}))
            return self.example(merged, name, depth + 1)
        for key in ("oneOf", "anyOf"):
            if key in node:
                options = [self.resolve(o) for o in node[key]]
                options = [o for o in options if o.get("type") != "null"] or options
                return self.example(options[0], name, depth + 1)
        if "enum" in node:
            values = [value for value in node["enum"] if value not in (None, "")]
            return values[0] if values else None
        kind = node.get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        if kind == "object" or "properties" in node:
            body: dict[str, Any] = {}
            for prop, sub in node.get("properties", {}).items():
                sub_resolved = self.resolve(sub)
                if sub_resolved.get("readOnly") or sub.get("readOnly"):
                    continue
                body[prop] = self.example(sub, prop, depth + 1)
            if not body and isinstance(node.get("additionalProperties"), dict):
                return {}
            return body
        if kind == "array":
            item = self.example(node.get("items", {}), name.rstrip("s"), depth + 1)
            return [] if item is None else [item]
        if kind == "integer":
            return max(int(node.get("minimum", 1) or 1), 1)
        if kind == "number":
            return max(float(node.get("minimum", 1) or 1), 1.0)
        if kind == "boolean":
            return False
        if kind == "string":
            return self.string_example(node, name)
        return None

    def string_example(self, node: dict[str, Any], name: str) -> str:
        fmt = node.get("format", "")
        if name.endswith("_id"):
            # Ids become collection variables so they can be filled in once.
            self.path_variables.add(name)
            return "{{" + name + "}}"
        if fmt == "uuid":
            return str(uuid.UUID(int=0))
        if fmt == "email" or name == "email":
            return "user@example.com"
        if fmt == "date-time":
            return "2026-12-01T15:00:00Z"
        if fmt == "date":
            return "2026-12-01"
        if fmt in ("uri", "url"):
            return "https://example.com"
        if "phone" in name:
            return "+51987654321"
        if "password" in name:
            return "{{password}}"
        if name in ("amount",):
            return "100.00"
        if name == "currency":
            return "PEN"
        min_length = int(node.get("minLength", 0) or 0)
        text = name.replace("_", " ").strip() or "text"
        if len(text) < min_length:
            text = (text + " ") * (min_length // max(len(text), 1) + 1)
        max_length = node.get("maxLength")
        return text.strip()[: int(max_length)] if max_length else text.strip()


def request_body(schema: Schema, method: str, path: str, operation: dict[str, Any]) -> Any:
    if (method, path) in BODY_OVERRIDES:
        return BODY_OVERRIDES[(method, path)]
    content = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if not content:
        return None
    examples = content.get("examples") or {}
    for example in examples.values():
        if "value" in example:
            return example["value"]
    if "example" in content:
        return content["example"]
    return schema.example(content.get("schema", {}))


def is_public(operation: dict[str, Any]) -> bool:
    security = operation.get("security")
    if not security:
        return True
    return all(not entry for entry in security)


def needs_idempotency(operation: dict[str, Any]) -> bool:
    declared = any(
        param.get("in") == "header" and param.get("name") == "Idempotency-Key"
        for param in operation.get("parameters", [])
    )
    text = f"{operation.get('description', '')} {operation.get('summary', '')}"
    return declared or "Idempotency-Key" in text


def folder_for(path: str, tag: str, tag_sizes: dict[str, int]) -> tuple[str, str | None]:
    title = tag.replace("-", " ").replace("_", " ").title()
    if tag_sizes[tag] <= SPLIT_THRESHOLD:
        return title, None
    segments = [s for s in path[len(API_PREFIX) :].split("/") if s and not s.startswith("{")]
    if segments and segments[0] in ("admin", "staff", "clinical", "physicians", "public"):
        resource = segments[1] if len(segments) > 1 else segments[0]
    else:
        resource = segments[0] if segments else tag
    return title, resource.replace("-", " ").title()


def describe(operation: dict[str, Any]) -> str:
    lines = [operation.get("description") or operation.get("summary") or ""]
    query = [p for p in operation.get("parameters", []) if p.get("in") == "query"]
    if query:
        lines.append("\nQuery parameters:")
        for param in query:
            lines.append(
                f"- `{param['name']}`: {param.get('description', '').strip()}".rstrip(": ")
            )
    return "\n".join(line for line in lines if line is not None).strip()


def build_item(schema: Schema, method: str, path: str, operation: dict[str, Any]) -> dict[str, Any]:
    postman_path = PATH_PARAM.sub(lambda m: "{{" + m.group(1) + "}}", path)
    segments = [segment for segment in postman_path.strip("/").split("/") if segment]
    headers = [{"key": "Accept", "value": "application/json"}]
    body = request_body(schema, method, path, operation)
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json"})
    if method != "get" and needs_idempotency(operation):
        headers.append(
            {"key": "Idempotency-Key", "value": "{{$guid}}", "description": "Unique per request."}
        )
    for param in operation.get("parameters", []):
        if param.get("in") == "header" and param.get("name") == "If-Match":
            headers.append(
                {
                    "key": "If-Match",
                    "value": "{{if_match}}",
                    "description": "Current `version` of the resource (from its GET).",
                }
            )
    query = [
        {
            "key": param["name"],
            "value": "",
            "description": param.get("description", ""),
            "disabled": True,
        }
        for param in operation.get("parameters", [])
        if param.get("in") == "query"
    ]
    url: dict[str, Any] = {
        "raw": "{{baseUrl}}/" + "/".join(segments) + "/",
        "host": ["{{baseUrl}}"],
        "path": [*segments, ""],
    }
    if query:
        url["query"] = query
    request: dict[str, Any] = {
        "method": method.upper(),
        "header": headers,
        "url": url,
        "description": describe(operation),
    }
    if body is not None:
        request["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2, ensure_ascii=False),
            "options": {"raw": {"language": "json"}},
        }
    if is_public(operation):
        request["auth"] = {"type": "noauth"}
    item: dict[str, Any] = {
        "name": operation.get("summary") or operation.get("operationId") or f"{method} {path}",
        "request": request,
    }
    if path in TOKEN_ENDPOINTS and method == "post":
        item["event"] = [
            {
                "listen": "test",
                "script": {"type": "text/javascript", "exec": SAVE_TOKENS.split("\n")},
            }
        ]
    return item


def _dedupe_names(requests: list[dict[str, Any]]) -> None:
    """Make request names unique inside a folder (e.g. activate vs deactivate)."""

    counts: dict[str, int] = {}
    for item in requests:
        counts[item["name"]] = counts.get(item["name"], 0) + 1
    for item in requests:
        if counts[item["name"]] > 1:
            path = [s for s in item["request"]["url"]["path"] if s and not s.startswith("{{")]
            item["name"] = f"{item['name']} ({item['request']['method']} {path[-1]})"


def build(document: dict[str, Any]) -> dict[str, Any]:
    schema = Schema(document)
    operations: list[tuple[str, str, dict[str, Any]]] = []
    for path, entry in document["paths"].items():
        schema.path_variables.update(PATH_PARAM.findall(path))
        for method in METHODS:
            if method in entry:
                operations.append((method, path, entry[method]))

    tag_sizes: dict[str, int] = {}
    for _, _, operation in operations:
        tag = (operation.get("tags") or ["other"])[0]
        tag_sizes[tag] = tag_sizes.get(tag, 0) + 1

    folders: OrderedDict[str, OrderedDict[str | None, list[dict[str, Any]]]] = OrderedDict()
    for method, path, operation in operations:
        tag = (operation.get("tags") or ["other"])[0]
        top, sub = folder_for(path, tag, tag_sizes)
        folders.setdefault(top, OrderedDict()).setdefault(sub, []).append(
            build_item(schema, method, path, operation)
        )

    def ordered(name: str) -> tuple[int, str]:
        first = ["Auth", "Public", "Legal", "Consents", "Accounts"]
        return (first.index(name) if name in first else len(first), name)

    for groups in folders.values():
        for requests in groups.values():
            _dedupe_names(requests)

    items: list[dict[str, Any]] = []
    for top in sorted(folders, key=ordered):
        children: list[dict[str, Any]] = []
        for sub, requests in folders[top].items():
            if sub is None:
                children.extend(requests)
            else:
                children.append({"name": sub, "item": requests})
        items.append({"name": top, "item": children})

    variables = [
        {"key": "baseUrl", "value": "http://localhost:8000"},
        {"key": "email", "value": DEMO_ACCOUNTS["patient_email"]},
        {"key": "password", "value": ""},
        *({"key": key, "value": value} for key, value in DEMO_ACCOUNTS.items()),
        {"key": "access_token", "value": ""},
        {"key": "refresh_token", "value": ""},
        {"key": "mfa_token", "value": ""},
        {"key": "if_match", "value": "1"},
        {"key": "last_created_id", "value": ""},
        *(
            {"key": name, "value": PATH_DEFAULTS.get(name, "")}
            for name in sorted(schema.path_variables)
        ),
    ]
    info = document["info"]
    description = (
        f"{info.get('description', '')}\n\n"
        "## Using this collection\n"
        "1. Set `baseUrl` (default `http://localhost:8000`).\n"
        "2. Set `email` and `password` (demo emails per role are in the `*_email` variables; "
        "the password is the one passed to `make seed`).\n"
        "3. Run **Auth → Log in**. Tokens are saved automatically. For MFA accounts, "
        "run **Auth → MFA verify** with the TOTP code; it also saves tokens.\n"
        "4. Fill the id variables (`assessment_id`, `order_id`, ...) before calling detail "
        "endpoints. Every 201 response stores its `id` in `last_created_id`.\n"
        "5. `Idempotency-Key` is a fresh GUID per send; `If-Match` uses `if_match`.\n"
        "6. Webhook requests need a valid provider signature and will be rejected otherwise.\n\n"
        f"Generated from OpenAPI {info.get('version', '')} by `scripts/build_postman.py`."
    )
    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "adelgaceya-api")),
            "name": info.get("title", "API"),
            "description": description,
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{access_token}}", "type": "string"}],
        },
        "event": [
            {
                "listen": "test",
                "script": {"type": "text/javascript", "exec": SAVE_CREATED_ID.split("\n")},
            }
        ],
        "variable": variables,
        "item": items,
    }


def main() -> None:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.yaml")
    target = Path(sys.argv[2] if len(sys.argv) > 2 else "postman.json")
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    collection = build(document)
    target.write_text(json.dumps(collection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    count = sum(1 for path in document["paths"].values() for method in METHODS if method in path)
    print(f"Wrote {target} with {count} requests.")


if __name__ == "__main__":
    main()
