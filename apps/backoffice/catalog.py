"""Products, programs, packages and evaluation fees (contract Part 5, 8.1-8.3 and 8.5)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils.text import slugify
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.models import User
from apps.assessments.models import Program
from apps.assessments.services import money
from apps.catalog.models import AssetPurpose, AssetStatus, AssetUpload
from apps.clinical.models import Specialty
from apps.core.audit import record_audit
from apps.core.exceptions import PriceMismatch
from apps.orders.models import CADENCE_MONTHS, BillingCadence, Package, PackageItem
from apps.treatments.models import Product


def _audit(actor: User, action: str, obj: Any, request: Any, fields: list[str]) -> None:
    record_audit(
        actor=actor,
        action=action,
        object_type=type(obj).__name__,
        object_id=obj.pk,
        request=request,
        changed_fields=fields,
    )


def _unique_slug(model: Any, field: str, base: str) -> str:
    candidate = slugify(base)[:70] or "item"
    stem, index = candidate, 2
    while model.objects.filter(**{field: candidate}).exists():
        candidate = f"{stem}-{index}"
        index += 1
    return candidate


def asset_url(asset: AssetUpload | None) -> str | None:
    if asset is None:
        return None
    from apps.backoffice.content import public_asset_url

    return public_asset_url(asset)


def ready_asset(asset_id: Any, purpose: str, field: str) -> AssetUpload:
    asset = AssetUpload.objects.filter(
        pk=asset_id, purpose=purpose, status=AssetStatus.READY
    ).first()
    if asset is None:
        raise ValidationError({field: ["Unknown or unconfirmed upload."]})
    return asset


# ---------------------------------------------------------------------------
# Products (8.1)
# ---------------------------------------------------------------------------


def _dose_labels(doses: Any) -> list[str]:
    return [str(dose.get("label", "")) for dose in doses or [] if isinstance(dose, dict)]


def product_payload(product: Product) -> dict[str, Any]:
    return {
        "id": product.pk,
        "name": product.name,
        "active_ingredient": product.active_ingredient,
        "form": product.form,
        "route": product.route or None,
        "description": product.description,
        "image_url": product.image_url or None,
        "allowed_doses": product.allowed_doses,
        "starting_dose_labels": _dose_labels(product.starting_doses),
        "default_frequency": product.default_frequency,
        "max_refills": product.max_refills,
        "safety_information_markdown": product.safety_information_markdown,
        "lifestyle_essentials": product.lifestyle_essentials,
        "program_ids": [program.pk for program in product.programs.all()],
        "is_active": product.is_active,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


def list_products(params: dict[str, Any]) -> QuerySet[Product]:
    queryset = Product.objects.prefetch_related("programs").order_by("name")
    if params.get("search"):
        term = params["search"]
        queryset = queryset.filter(Q(name__icontains=term) | Q(active_ingredient__icontains=term))
    if params.get("is_active") is not None:
        queryset = queryset.filter(is_active=params["is_active"])
    if params.get("program_id"):
        queryset = queryset.filter(programs=params["program_id"])
    return queryset


def get_product(product_id: Any) -> Product:
    product = Product.objects.prefetch_related("programs").filter(pk=product_id).first()
    if product is None:
        raise NotFound()
    return product


def save_product(
    *, product: Product | None, data: dict[str, Any], actor: User, request: Any
) -> Product:
    creating = product is None
    target = product or Product(code=_unique_slug(Product, "code", data["name"]))
    doses = data.get("allowed_doses", target.allowed_doses if not creating else None)
    labels = data.get("starting_dose_labels")
    if labels is not None or "allowed_doses" in data:
        labels = labels if labels is not None else _dose_labels(target.starting_doses)
        by_label = {str(dose["label"]): dose for dose in doses or []}
        missing = [label for label in labels if label not in by_label]
        if missing:
            raise ValidationError(
                {"starting_dose_labels": [f"Not in allowed_doses: {', '.join(missing)}."]}
            )
        target.starting_doses = [by_label[label] for label in labels]
    simple = (
        "name",
        "active_ingredient",
        "form",
        "route",
        "description",
        "default_frequency",
        "max_refills",
        "safety_information_markdown",
        "lifestyle_essentials",
        "allowed_doses",
        "is_active",
    )
    for field in simple:
        if field in data:
            setattr(target, field, data[field])
    if "image_upload_id" in data:
        asset = ready_asset(data["image_upload_id"], AssetPurpose.PRODUCT_IMAGE, "image_upload_id")
        target.image_url = asset_url(asset) or ""
    with transaction.atomic():
        target.save()
        _audit(
            actor,
            "product.created" if creating else "product.updated",
            target,
            request,
            sorted(data),
        )
    return get_product(target.pk)


def set_product_active(*, product: Product, active: bool, actor: User, request: Any) -> Product:
    product.is_active = active
    product.save(update_fields=["is_active", "updated_at"])
    _audit(actor, "product.updated", product, request, ["is_active"])
    return get_product(product.pk)


# ---------------------------------------------------------------------------
# Programs (8.2)
# ---------------------------------------------------------------------------


def program_payload(program: Program) -> dict[str, Any]:
    percents = program.projection_loss_percent_by_month
    has_override = (
        program.estimated_review_hours_min is not None
        and program.estimated_review_hours_max is not None
    )
    return {
        "id": program.pk,
        "slug": program.slug,
        "title": program.name,
        "category": program.category,
        "short_description": program.short_description,
        "description": program.description,
        "icon": program.icon,
        "product_ids": [product.pk for product in program.products.all()],
        "allowed_specialty_ids": [item.pk for item in program.allowed_specialties.all()],
        "eligibility": {
            "min_age": program.age_min,
            "max_age": program.age_max,
            "min_bmi": float(program.min_bmi) if program.min_bmi is not None else None,
            "max_bmi": float(program.max_bmi) if program.max_bmi is not None else None,
        },
        "projection": (
            {"months": len(percents), "loss_percent_by_month": percents}
            if isinstance(percents, list) and percents
            else None
        ),
        "estimated_review_hours": (
            {
                "min": program.estimated_review_hours_min,
                "max": program.estimated_review_hours_max,
            }
            if has_override
            else None
        ),
        "is_active": program.is_active,
        "position": program.position,
        "created_at": program.created_at,
        "updated_at": program.updated_at,
    }


def programs() -> QuerySet[Program]:
    return Program.objects.prefetch_related("products", "allowed_specialties").order_by(
        "position", "name"
    )


def get_program(program_id: Any) -> Program:
    program = programs().filter(pk=program_id).first()
    if program is None:
        raise NotFound()
    return program


def _resolve_ids(model: Any, ids: list[Any], field: str, **filters: Any) -> list[Any]:
    found = list(model.objects.filter(pk__in=ids, **filters))
    if len(found) != len(set(ids)):
        raise ValidationError({field: ["Unknown or inactive ids."]})
    return found


def save_program(
    *, program: Program | None, data: dict[str, Any], actor: User, request: Any
) -> Program:
    creating = program is None
    if "category" in data and data["category"] not in settings.PROGRAM_CATEGORIES:
        raise ValidationError({"category": ["Unknown category."]})
    target = program or Program(slug=_unique_slug(Program, "slug", data["title"]))
    if creating:
        # evaluation.reapply_cooldown_days is the default for new programs (deviation 127).
        from apps.catalog import runtime

        target.not_suitable_cooldown_days = int(
            runtime.group("evaluation")["reapply_cooldown_days"]
        )
    mapping = {
        "title": "name",
        "category": "category",
        "short_description": "short_description",
        "description": "description",
        "icon": "icon",
        "is_active": "is_active",
        "position": "position",
    }
    for key, field in mapping.items():
        if key in data:
            setattr(target, field, data[key])
    if "eligibility" in data:
        rules = data["eligibility"]
        if rules["min_age"] > rules["max_age"]:
            raise ValidationError({"eligibility": ["min_age must be <= max_age."]})
        low, high = rules.get("min_bmi"), rules.get("max_bmi")
        if low is not None and high is not None and low > high:
            raise ValidationError({"eligibility": ["min_bmi must be <= max_bmi."]})
        target.age_min = rules["min_age"]
        target.age_max = rules["max_age"]
        target.min_bmi = Decimal(str(low)) if low is not None else None
        target.max_bmi = Decimal(str(high)) if high is not None else None
    if "projection" in data:
        projection = data["projection"]
        if projection is None:
            target.projection_loss_percent_by_month = None
        else:
            percents = [float(item) for item in projection["loss_percent_by_month"]]
            if len(percents) != projection["months"]:
                raise ValidationError({"projection": ["One percentage per month is required."]})
            target.projection_loss_percent_by_month = percents
            target.projection_months = projection["months"]
    if "estimated_review_hours" in data:
        hours = data["estimated_review_hours"]
        if hours is not None and hours["min"] > hours["max"]:
            raise ValidationError({"estimated_review_hours": ["min must be <= max."]})
        target.estimated_review_hours_min = hours["min"] if hours else None
        target.estimated_review_hours_max = hours["max"] if hours else None
    products = specialties = None
    if "product_ids" in data:
        products = _resolve_ids(Product, data["product_ids"], "product_ids")
    if "allowed_specialty_ids" in data:
        specialties = _resolve_ids(
            Specialty, data["allowed_specialty_ids"], "allowed_specialty_ids"
        )
    if creating:
        if not products or not any(product.is_active for product in products):
            raise ValidationError({"product_ids": ["At least one active product is required."]})
        if not specialties:
            raise ValidationError(
                {"allowed_specialty_ids": ["At least one specialty is required."]}
            )
    with transaction.atomic():
        target.save()
        if products is not None:
            target.products.set(products)
        if specialties is not None:
            target.allowed_specialties.set(specialties)
        _audit(
            actor,
            "program.created" if creating else "program.updated",
            target,
            request,
            sorted(data),
        )
    return get_program(target.pk)


def set_program_active(*, program: Program, active: bool, actor: User, request: Any) -> Program:
    program.is_active = active
    program.save(update_fields=["is_active", "updated_at"])
    _audit(actor, "program.updated", program, request, ["is_active"])
    return get_program(program.pk)


def fee_payload(program: Program) -> dict[str, Any]:
    return {
        "program_id": program.pk,
        "amount": money(program.fee_amount, program.fee_currency),
        "is_waived": program.fee_is_waived,
        "inclusions": list(program.fee_inclusions or []),
    }


def save_fee(*, program: Program, data: dict[str, Any], actor: User, request: Any) -> Program:
    amount = Decimal(str(data["amount"]["amount"]))
    if amount < 0 or amount > 1000:
        raise ValidationError({"amount": ["Between 0 and 1000."]})
    program.fee_amount = amount
    program.fee_currency = data["amount"]["currency"]
    program.fee_is_waived = data["is_waived"]
    program.fee_inclusions = list(data.get("inclusions") or [])
    program.save(
        update_fields=[
            "fee_amount",
            "fee_currency",
            "fee_is_waived",
            "fee_inclusions",
            "updated_at",
        ]
    )
    _audit(actor, "program.fee_updated", program, request, sorted(data))
    return program


# ---------------------------------------------------------------------------
# Packages (8.3)
# ---------------------------------------------------------------------------


def package_payload(package: Package) -> dict[str, Any]:
    return {
        "id": package.pk,
        "program_id": package.program_id,
        "name": package.name,
        "badge": package.badge or None,
        "supply_duration_weeks": package.supply_duration_weeks,
        "billing_cadence": package.billing_cadence or None,
        "billing_type": package.billing_type,
        "billing_interval_months": package.billing_interval_months,
        "display_price_per_month": money(package.monthly_price, package.currency),
        "charge_per_cycle": money(package.price, package.currency),
        "included_services": list(package.included_services or []),
        "items": [
            {"product_id": item.product_id, "quantity": item.quantity}
            for item in package.items.all()
        ],
        "is_active": package.is_active,
        "position": package.position,
        "created_at": package.created_at,
        "updated_at": package.updated_at,
    }


def get_package(package_id: Any) -> Package:
    package = Package.objects.prefetch_related("items").filter(pk=package_id).first()
    if package is None:
        raise NotFound()
    return package


def _check_prices(
    cadence: str, monthly: dict[str, Any], charge: dict[str, Any], allow: bool
) -> None:
    if monthly["currency"] != charge["currency"]:
        raise ValidationError({"charge_per_cycle": ["Both prices must use the same currency."]})
    months = CADENCE_MONTHS[BillingCadence(cadence)]
    if not months or allow:
        return
    expected = Decimal(str(monthly["amount"])) * months
    if abs(expected - Decimal(str(charge["amount"]))) > 1:
        raise PriceMismatch(
            details={"expected_charge": str(expected), "currency": charge["currency"]}
        )


def save_package(
    *, package: Package | None, data: dict[str, Any], actor: User, request: Any
) -> Package:
    creating = package is None
    if creating:
        program = Program.objects.filter(pk=data["program_id"]).first()
        if program is None:
            raise ValidationError({"program_id": ["Unknown program."]})
        target = Package(program=program)
    else:
        assert package is not None
        target = package
    if "supply_duration_weeks" in data and data["supply_duration_weeks"] not in (
        settings.PACKAGE_SUPPLY_WEEKS
    ):
        raise ValidationError({"supply_duration_weeks": ["Unsupported duration."]})
    cadence = data.get("billing_cadence", target.billing_cadence or BillingCadence.MONTHLY)
    monthly = data.get("display_price_per_month") or (
        money(target.monthly_price, target.currency) if not creating else None
    )
    charge = data.get("charge_per_cycle") or (
        money(target.price, target.currency) if not creating else None
    )
    if (
        monthly
        and charge
        and ({"billing_cadence", "display_price_per_month", "charge_per_cycle"} & set(data))
    ):
        _check_prices(cadence, monthly, charge, bool(data.get("allow_price_mismatch")))
    badge = data.get("badge", target.badge or None)
    if badge:
        clash = Package.objects.filter(program_id=target.program_id).exclude(badge="")
        if target.pk:
            clash = clash.exclude(pk=target.pk)
        if clash.exists():
            raise ValidationError({"badge": ["Only one badged package per program."]})
    for field in ("name", "supply_duration_weeks", "is_active", "position"):
        if field in data:
            setattr(target, field, data[field])
    if "badge" in data:
        target.badge = data["badge"] or ""
    if "included_services" in data:
        target.included_services = list(data["included_services"])
    target.billing_cadence = cadence
    if monthly:
        target.display_price_per_month = Decimal(str(monthly["amount"]))
        target.currency = monthly["currency"]
    if charge:
        target.charge_per_cycle = Decimal(str(charge["amount"]))
    with transaction.atomic():
        target.save()
        if "items" in data:
            products = {
                product.pk: product
                for product in Product.objects.filter(
                    pk__in=[item["product_id"] for item in data["items"]]
                )
            }
            if len(products) != len({item["product_id"] for item in data["items"]}):
                raise ValidationError({"items": ["Unknown product."]})
            target.items.all().delete()
            PackageItem.objects.bulk_create(
                [
                    PackageItem(
                        package=target,
                        product=products[item["product_id"]],
                        quantity=item["quantity"],
                        position=index,
                    )
                    for index, item in enumerate(data["items"])
                ]
            )
        _audit(
            actor,
            "package.created" if creating else "package.updated",
            target,
            request,
            sorted(data),
        )
    return get_package(target.pk)


def set_package_active(*, package: Package, active: bool, actor: User, request: Any) -> Package:
    package.is_active = active
    package.save(update_fields=["is_active", "updated_at"])
    _audit(actor, "package.updated", package, request, ["is_active"])
    return get_package(package.pk)


def reorder_packages(*, program: Program, ids: list[Any], actor: User, request: Any) -> None:
    current = {str(pk) for pk in program.packages.values_list("pk", flat=True)}
    if {str(pk) for pk in ids} != current or len(ids) != len(current):
        raise ValidationError({"package_ids": ["List every package of the program exactly once."]})
    with transaction.atomic():
        for position, package_id in enumerate(ids):
            Package.objects.filter(pk=package_id).update(position=position)
        _audit(actor, "program.packages_reordered", program, request, ["position"])
