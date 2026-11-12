"""Reports, analytics and CSV exports (Part 5, section 10)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError

from apps.accounts.enums import Role
from apps.accounts.models import User
from apps.assessments.enums import EvaluationStatus, PaymentStatus
from apps.assessments.models import Assessment, Evaluation, EvaluationStatusHistory, Payment
from apps.backoffice.models import ExportStatus, ReportExport, ReportKind
from apps.core.audit import record_audit
from apps.core.storage import presigned_url
from apps.orders.models import (
    Order,
    OrderStatus,
    OrderStatusHistory,
    Subscription,
    SubscriptionStatus,
)
from apps.payments.models import Refund, RefundStatus
from apps.treatments.models import Treatment, TreatmentStatus

CACHE_SECONDS = 300
MAX_MONTHS = 24
SETTLED = (PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
COUNTED_ORDER_STATES = (
    OrderStatus.PRESCRIPTION_VERIFIED,
    OrderStatus.ON_HOLD,
    OrderStatus.COMPOUNDING,
    OrderStatus.IN_TRANSIT,
    OrderStatus.OUT_FOR_DELIVERY,
    OrderStatus.DELIVERED,
)
ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=12, decimal_places=2))

DEFINITIONS = {
    "total_revenue": (
        "Succeeded payments in the reporting currency minus completed refunds, in the period."
    ),
    "monthly_recurring_revenue": (
        "Sum of the monthly display price of subscriptions that are ACTIVE at the end of "
        "the period."
    ),
    "total_orders": (
        "Orders that reached PRESCRIPTION_VERIFIED or later in the period, excluding cancelled "
        "and refunded orders."
    ),
    "total_users": "All active accounts of every role at the end of the period.",
    "total_registered": "Patient accounts created up to the end of the period.",
    "active_in_treatment": "Patients with an ACTIVE treatment at the end of the period.",
    "evaluation_rate_percent": (
        "Patients registered in the period who submitted an assessment, divided by patients "
        "registered in the period."
    ),
    "churn_rate_percent": (
        "Subscriptions cancelled in the period divided by subscriptions active at the start of "
        "the period."
    ),
    "evaluation_outcomes": (
        "Evaluations decided in the period (approved or not suitable) plus evaluations moved to "
        "pending labs in the period. Percentages add up to 100."
    ),
    "currency": ("Amounts use the reporting currency; payments in other currencies are excluded."),
}


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    tz: ZoneInfo

    @property
    def start_at(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=self.tz)

    @property
    def end_at(self) -> datetime:
        """Exclusive upper bound: midnight after ``end``."""

        return datetime.combine(self.end + timedelta(days=1), time.min, tzinfo=self.tz)

    def key(self) -> str:
        return f"{self.start.isoformat()}:{self.end.isoformat()}:{self.tz.key}"


def _months_back(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    return date(index // 12, index % 12 + 1, 1)


def parse_period(params: Any) -> Period:
    try:
        tz = ZoneInfo(params.get("timezone") or "America/Lima")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError({"timezone": ["Unknown time zone."]}) from exc
    today = timezone.now().astimezone(tz).date()

    def _date(name: str) -> date | None:
        raw = params.get(name)
        if not raw:
            return None
        try:
            return date.fromisoformat(str(raw))
        except ValueError as exc:
            raise ValidationError({name: ["Use YYYY-MM-DD."]}) from exc

    end = _date("to") or today
    start = _date("from") or _months_back(end, 5)
    if start > end:
        raise ValidationError({"from": ["Must be on or before 'to'."]})
    if (end.year - start.year) * 12 + end.month - start.month > MAX_MONTHS:
        raise ValidationError({"from": [f"The range is limited to {MAX_MONTHS} months."]})
    return Period(start=start, end=end, tz=tz)


def _currency() -> str:
    return str(settings.REPORTING_CURRENCY)


def _money(amount: Decimal | None) -> dict[str, str]:
    value = amount or Decimal("0")
    return {"amount": f"{value:.2f}", "currency": _currency()}


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part * 100.0 / whole, 1)


def _revenue(start_at: datetime, end_at: datetime) -> Decimal:
    currency = _currency()
    gross = Payment.objects.filter(
        status__in=SETTLED, currency=currency, created_at__gte=start_at, created_at__lt=end_at
    ).aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    refunds = Refund.objects.filter(
        status=RefundStatus.SUCCEEDED,
        currency=currency,
        updated_at__gte=start_at,
        updated_at__lt=end_at,
    ).aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return Decimal(gross) - Decimal(refunds)


def _counted_orders(start_at: datetime, end_at: datetime) -> Any:
    reached = OrderStatusHistory.objects.filter(
        to_status=OrderStatus.PRESCRIPTION_VERIFIED,
        created_at__gte=start_at,
        created_at__lt=end_at,
    ).values("order_id")
    return Order.objects.filter(pk__in=reached).exclude(
        status__in=(OrderStatus.CANCELLED, OrderStatus.REFUNDED)
    )


def _monthly_price(subscription: Subscription) -> Decimal:
    package = subscription.package
    if package.display_price_per_month is not None:
        return Decimal(package.display_price_per_month)
    months = package.billing_interval_months or 1
    charge = subscription.charge_per_cycle or package.price
    return Decimal(charge) / Decimal(months)


def _mrr(end_at: datetime) -> Decimal:
    """Subscriptions ACTIVE at ``end_at``: still active now, or cancelled after it.

    Pauses are not historised, so a subscription paused today is left out of
    past periods too (deviation 136).
    """

    subscriptions = (
        Subscription.objects.filter(created_at__lt=end_at)
        .filter(
            Q(status=SubscriptionStatus.ACTIVE)
            | Q(status=SubscriptionStatus.CANCELLED, cancelled_at__gte=end_at)
        )
        .select_related("package")
    )
    total = Decimal("0")
    for subscription in subscriptions:
        if subscription.package.currency == _currency():
            total += _monthly_price(subscription)
    return total.quantize(Decimal("0.01"))


def _outcomes(period: Period) -> dict[str, Any]:
    decided = Evaluation.objects.filter(
        decided_at__gte=period.start_at, decided_at__lt=period.end_at
    )
    approved = decided.filter(status=EvaluationStatus.APPROVED).count()
    not_suitable = decided.filter(status=EvaluationStatus.NOT_SUITABLE).count()
    pending_labs = (
        EvaluationStatusHistory.objects.filter(
            to_status=EvaluationStatus.PENDING_LABS,
            created_at__gte=period.start_at,
            created_at__lt=period.end_at,
        )
        .values("evaluation_id")
        .distinct()
        .count()
    )
    total = approved + not_suitable + pending_labs
    counts = [approved, not_suitable, pending_labs]
    percents = _largest_remainder(counts, total)
    return {
        "total_reviews": total,
        "approved": {"count": approved, "percent": percents[0]},
        "not_suitable": {"count": not_suitable, "percent": percents[1]},
        "pending_labs": {"count": pending_labs, "percent": percents[2]},
    }


def _largest_remainder(counts: list[int], total: int) -> list[float]:
    """Percentages with one decimal that always add up to exactly 100."""

    if total <= 0:
        return [0.0 for _ in counts]
    raw = [count * 1000 / total for count in counts]
    floors = [int(value) for value in raw]
    remaining = 1000 - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floors[i], reverse=True)
    for index in order[:remaining]:
        floors[index] += 1
    return [value / 10 for value in floors]


def _overview_uncached(period: Period) -> dict[str, Any]:
    start_at, end_at = period.start_at, period.end_at
    patients = User.objects.filter(role=Role.PATIENT)
    registered_in_period = patients.filter(date_joined__gte=start_at, date_joined__lt=end_at)
    registered_count = registered_in_period.count()
    submitted = (
        Assessment.objects.filter(user__in=registered_in_period, submitted_at__isnull=False)
        .values("user_id")
        .distinct()
        .count()
    )
    active_start = (
        Subscription.objects.filter(created_at__lt=start_at)
        .filter(Q(cancelled_at__isnull=True) | Q(cancelled_at__gte=start_at))
        .count()
    )
    cancelled = Subscription.objects.filter(
        cancelled_at__gte=start_at, cancelled_at__lt=end_at
    ).count()
    return {
        "period": {"from": period.start, "to": period.end},
        "kpis": {
            "total_revenue": _money(_revenue(start_at, end_at)),
            "monthly_recurring_revenue": _money(_mrr(end_at)),
            "total_orders": _counted_orders(start_at, end_at).count(),
            "total_users": User.objects.filter(is_active=True, date_joined__lt=end_at).count(),
        },
        "patient_overview": {
            "total_registered": patients.filter(date_joined__lt=end_at).count(),
            "active_in_treatment": Treatment.objects.filter(
                status=TreatmentStatus.ACTIVE, started_at__lt=period.end + timedelta(days=1)
            )
            .values("patient_id")
            .distinct()
            .count(),
            "evaluation_rate_percent": _percent(submitted, registered_count),
            "churn_rate_percent": _percent(cancelled, active_start),
        },
        "evaluation_outcomes": _outcomes(period),
        "definitions": DEFINITIONS,
    }


def _cached(name: str, params: dict[str, Any], builder: Any) -> dict[str, Any]:
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    key = f"reports:{name}:{digest[:32]}"
    cached = cache.get(key)
    if cached is not None:
        return dict(cached)
    data = builder()
    cache.set(key, data, CACHE_SECONDS)
    return dict(data)


def overview(period: Period) -> dict[str, Any]:
    return _cached("overview", {"p": period.key()}, lambda: _overview_uncached(period))


def last_30_days_kpis() -> dict[str, Any]:
    tz = ZoneInfo("America/Lima")
    today = timezone.now().astimezone(tz).date()
    period = Period(start=today - timedelta(days=29), end=today, tz=tz)
    return dict(overview(period)["kpis"])


# ---------------------------------------------------------------------------
# Revenue series (10.2)
# ---------------------------------------------------------------------------

GRANULARITIES = ("DAY", "WEEK", "MONTH")


def _buckets(period: Period, granularity: str) -> list[tuple[date, date, str, str]]:
    """Return (start, end_inclusive, key, label) buckets covering the period."""

    buckets: list[tuple[date, date, str, str]] = []
    if granularity == "DAY":
        day = period.start
        while day <= period.end:
            buckets.append((day, day, day.isoformat(), day.strftime("%b %d, %Y")))
            day += timedelta(days=1)
    elif granularity == "WEEK":
        day = period.start - timedelta(days=period.start.weekday())
        while day <= period.end:
            year, week, _ = day.isocalendar()
            end = day + timedelta(days=6)
            buckets.append(
                (day, end, f"{year}-W{week:02d}", f"Week of {day.strftime('%b %d, %Y')}")
            )
            day = end + timedelta(days=1)
    else:
        day = period.start.replace(day=1)
        while day <= period.end:
            following = _months_back(day, -1)
            buckets.append(
                (day, following - timedelta(days=1), day.strftime("%Y-%m"), day.strftime("%b %Y"))
            )
            day = following
    return buckets


def _series(period: Period, granularity: str) -> list[dict[str, Any]]:
    today = timezone.now().astimezone(period.tz).date()
    series = []
    for start, end, key, label in _buckets(period, granularity):
        sub = Period(start=max(start, period.start), end=min(end, period.end), tz=period.tz)
        series.append(
            {
                "period": key,
                "label": label,
                "revenue": _money(_revenue(sub.start_at, sub.end_at)),
                "orders_count": _counted_orders(sub.start_at, sub.end_at).count(),
                "is_current": start <= today <= end,
            }
        )
    return series


def revenue(period: Period, *, granularity: str, compare: str) -> dict[str, Any]:
    if granularity not in GRANULARITIES:
        raise ValidationError({"granularity": ["Use DAY, WEEK or MONTH."]})
    if compare not in ("NONE", "PREVIOUS_PERIOD"):
        raise ValidationError({"compare": ["Use NONE or PREVIOUS_PERIOD."]})

    def build() -> dict[str, Any]:
        comparison = None
        if compare == "PREVIOUS_PERIOD":
            length = (period.end - period.start).days + 1
            previous = Period(
                start=period.start - timedelta(days=length),
                end=period.start - timedelta(days=1),
                tz=period.tz,
            )
            comparison = _series(previous, granularity)
        return {
            "granularity": granularity,
            "currency": _currency(),
            "series": _series(period, granularity),
            "comparison": comparison,
        }

    return _cached("revenue", {"p": period.key(), "g": granularity, "c": compare}, build)


# ---------------------------------------------------------------------------
# Exports (10.3)
# ---------------------------------------------------------------------------


def request_export(*, data: dict[str, Any], actor: User, request: Any) -> ReportExport:
    if data["from"] > data["to"]:
        raise ValidationError({"from": ["Must be on or before 'to'."]})
    export = ReportExport.objects.create(
        report=data["report"],
        date_from=data["from"],
        date_to=data["to"],
        format=data.get("format", "CSV"),
        requested_by=actor,
    )
    record_audit(
        actor=actor,
        action="report.exported",
        object_type="ReportExport",
        object_id=export.pk,
        request=request,
        metadata={"report": export.report},
    )
    from django.db import transaction

    from apps.backoffice.tasks import build_report_export

    transaction.on_commit(lambda: build_report_export.delay(str(export.pk)))
    return export


def get_export(export_id: Any) -> ReportExport:
    export = ReportExport.objects.filter(pk=export_id).first()
    if export is None:
        raise NotFound()
    return export


def export_payload(export: ReportExport) -> dict[str, Any]:
    url = None
    expires_at = None
    if export.status == ExportStatus.READY and export.storage_key:
        ttl = int(settings.REPORT_EXPORT_URL_TTL_SECONDS)
        url = presigned_url(export.storage_key, expires_in=ttl)
        expires_at = timezone.now() + timedelta(seconds=ttl)
    return {"id": export.pk, "status": export.status, "download_url": url, "expires_at": expires_at}


def _rows(export: ReportExport) -> tuple[list[str], list[list[Any]]]:
    tz = ZoneInfo("America/Lima")
    period = Period(start=export.date_from, end=export.date_to, tz=tz)
    start_at, end_at = period.start_at, period.end_at
    if export.report == ReportKind.OVERVIEW:
        data = _overview_uncached(period)
        rows: list[list[Any]] = []
        for section in ("kpis", "patient_overview"):
            for key, value in data[section].items():
                rows.append([key, value["amount"] if isinstance(value, dict) else value])
        for key in ("approved", "not_suitable", "pending_labs"):
            rows.append([f"evaluations_{key}", data["evaluation_outcomes"][key]["count"]])
        return ["metric", "value"], rows
    if export.report == ReportKind.REVENUE:
        series = _series(period, "MONTH")
        return ["period", "revenue", "currency", "orders_count"], [
            [item["period"], item["revenue"]["amount"], _currency(), item["orders_count"]]
            for item in series
        ]
    if export.report == ReportKind.EVALUATIONS:
        evaluations = Evaluation.objects.filter(
            submitted_at__gte=start_at, submitted_at__lt=end_at
        ).select_related("assessment__program")
        return ["id", "program", "status", "submitted_at", "decided_at"], [
            [e.pk, e.assessment.program.name, e.status, e.submitted_at, e.decided_at]
            for e in evaluations.order_by("submitted_at")
        ]
    if export.report == ReportKind.ORDERS:
        orders = Order.objects.filter(placed_at__gte=start_at, placed_at__lt=end_at)
        return ["number", "status", "placed_at", "total", "currency"], [
            [o.number, o.status, o.placed_at, f"{o.total:.2f}", o.currency]
            for o in orders.order_by("placed_at")
        ]
    subscriptions = Subscription.objects.filter(
        created_at__gte=start_at, created_at__lt=end_at
    ).select_related("package")
    return ["id", "package", "status", "created_at", "cancelled_at"], [
        [s.pk, s.package.name, s.status, s.created_at, s.cancelled_at]
        for s in subscriptions.order_by("created_at")
    ]


def build_export(export_id: Any) -> None:
    export = ReportExport.objects.filter(pk=export_id).first()
    if export is None or export.status != ExportStatus.PENDING:
        return
    try:
        header, rows = _rows(export)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerows(rows)
        key = f"exports/reports/{export.pk}.csv"
        export.storage_key = default_storage.save(key, ContentFile(buffer.getvalue().encode()))
        export.status = ExportStatus.READY
    except Exception:  # pragma: no cover - defensive, surfaced as FAILED
        export.status = ExportStatus.FAILED
    export.save(update_fields=["storage_key", "status", "updated_at"])
