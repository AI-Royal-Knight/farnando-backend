"""Live slot computation (Part 3, 4.3; Part 4, section 7). Never cached.

Bookable time = weekly AVAILABLE rules (for the consultation type) plus
EXTRA_AVAILABLE time blocks, minus BLOCKED rules, BLOCKED time blocks and open
consultations, in the physician's timezone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from apps.catalog import runtime
from apps.consultations.clinicians import profile_for
from apps.consultations.models import (
    OPEN_CONSULTATION_STATUSES,
    AvailabilityBlock,
    AvailabilityKind,
    AvailabilityRule,
    Consultation,
    ConsultationType,
    TimeBlockKind,
)

Interval = tuple[datetime, datetime]


@dataclass(frozen=True)
class Slot:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BlockedInterval:
    start: datetime
    end: datetime
    source: str  # RULE | TIME_BLOCK
    id: Any
    label: str


def physician_timezone(physician: Any) -> str:
    profile = profile_for(physician)
    return profile.timezone if profile is not None else settings.PORTAL_DEFAULT_TIMEZONE


def _overlaps(start: datetime, end: datetime, intervals: list[Interval]) -> bool:
    return any(start < other_end and other_start < end for other_start, other_end in intervals)


def _rule_applies(rule: AvailabilityRule, day: date) -> bool:
    if rule.weekday != day.isoweekday():
        return False
    if rule.valid_from is not None and day < rule.valid_from:
        return False
    return not (rule.valid_until is not None and day > rule.valid_until)


def _days(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=offset) for offset in range((date_to - date_from).days + 1)]


def _window(day: date, start: time, end: time, tz: ZoneInfo) -> Interval:
    return (
        datetime.combine(day, start, tzinfo=tz).astimezone(UTC),
        datetime.combine(day, end, tzinfo=tz).astimezone(UTC),
    )


def _bounds(date_from: date, date_to: date, tz: ZoneInfo) -> Interval:
    return (
        datetime.combine(date_from, time.min, tzinfo=tz),
        datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz),
    )


def available_windows(
    physician: Any,
    date_from: date,
    date_to: date,
    *,
    consultation_type: ConsultationType | None = None,
    rules: list[AvailabilityRule] | None = None,
    blocks: list[AvailabilityBlock] | None = None,
) -> list[Interval]:
    tz = ZoneInfo(physician_timezone(physician))
    if rules is None:
        rules = list(
            AvailabilityRule.objects.filter(physician=physician).prefetch_related(
                "consultation_types"
            )
        )
    windows: list[Interval] = []
    for day in _days(date_from, date_to):
        for rule in rules:
            if rule.kind != AvailabilityKind.AVAILABLE or not _rule_applies(rule, day):
                continue
            if consultation_type is not None:
                type_ids = {item.pk for item in rule.consultation_types.all()}
                if type_ids and consultation_type.pk not in type_ids:
                    continue
            windows.append(_window(day, rule.start_time, rule.end_time, tz))
    start, end = _bounds(date_from, date_to, tz)
    if blocks is None:
        blocks = list(
            AvailabilityBlock.objects.filter(physician=physician, start__lt=end, end__gt=start)
        )
    windows.extend(
        (block.start, block.end) for block in blocks if block.kind == TimeBlockKind.EXTRA_AVAILABLE
    )
    return sorted(windows)


def blocked_intervals(
    physician: Any,
    date_from: date,
    date_to: date,
    *,
    rules: list[AvailabilityRule] | None = None,
    blocks: list[AvailabilityBlock] | None = None,
) -> list[BlockedInterval]:
    tz = ZoneInfo(physician_timezone(physician))
    if rules is None:
        rules = list(AvailabilityRule.objects.filter(physician=physician))
    result: list[BlockedInterval] = []
    for day in _days(date_from, date_to):
        for rule in rules:
            if rule.kind == AvailabilityKind.BLOCKED and _rule_applies(rule, day):
                start, end = _window(day, rule.start_time, rule.end_time, tz)
                result.append(BlockedInterval(start, end, "RULE", rule.pk, rule.label))
    start, end = _bounds(date_from, date_to, tz)
    if blocks is None:
        blocks = list(
            AvailabilityBlock.objects.filter(physician=physician, start__lt=end, end__gt=start)
        )
    for block in blocks:
        if block.kind == TimeBlockKind.BLOCKED:
            result.append(
                BlockedInterval(block.start, block.end, "TIME_BLOCK", block.pk, block.reason)
            )
    return sorted(result, key=lambda item: item.start)


def busy_intervals(
    physician: Any, date_from: date, date_to: date, *, exclude_consultation_id: Any = None
) -> list[Interval]:
    tz = ZoneInfo(physician_timezone(physician))
    start, end = _bounds(date_from, date_to, tz)
    queryset = Consultation.objects.filter(
        physician=physician,
        status__in=OPEN_CONSULTATION_STATUSES,
        start__lt=end,
        end__gt=start,
    )
    if exclude_consultation_id is not None:
        queryset = queryset.exclude(pk=exclude_consultation_id)
    return list(queryset.values_list("start", "end"))


def compute_slots(
    physician: Any,
    consultation_type: ConsultationType,
    date_from: date,
    date_to: date,
    *,
    now: datetime | None = None,
    exclude_consultation_id: Any = None,
    apply_lead_time: bool = True,
) -> list[Slot]:
    now = now or timezone.now()
    earliest = (
        now + timedelta(hours=runtime.hours("booking", "min_booking_lead_hours"))
        if apply_lead_time
        else now
    )
    length = timedelta(minutes=consultation_type.duration_minutes)
    blocked: list[Interval] = [
        (item.start, item.end) for item in blocked_intervals(physician, date_from, date_to)
    ]
    blocked.extend(
        busy_intervals(
            physician, date_from, date_to, exclude_consultation_id=exclude_consultation_id
        )
    )
    slots: dict[datetime, Slot] = {}
    for window_start, window_end in available_windows(
        physician, date_from, date_to, consultation_type=consultation_type
    ):
        cursor = window_start
        while cursor + length <= window_end:
            start, end = cursor, cursor + length
            if start >= earliest and not _overlaps(start, end, blocked):
                slots[start] = Slot(start=start, end=end)
            cursor = end
    return [slots[key] for key in sorted(slots)]


def find_slot(
    physician: Any,
    consultation_type: ConsultationType,
    start: datetime,
    *,
    exclude_consultation_id: Any = None,
    apply_lead_time: bool = True,
) -> Slot | None:
    tz = ZoneInfo(physician_timezone(physician))
    local_day = start.astimezone(tz).date()
    for slot in compute_slots(
        physician,
        consultation_type,
        local_day - timedelta(days=1),
        local_day,
        exclude_consultation_id=exclude_consultation_id,
        apply_lead_time=apply_lead_time,
    ):
        if slot.start == start:
            return slot
    return None


def next_available_at(
    physician: Any, consultation_type: ConsultationType, *, within_days: int = 30
) -> datetime | None:
    tz = ZoneInfo(physician_timezone(physician))
    today = timezone.now().astimezone(tz).date()
    slots = compute_slots(physician, consultation_type, today, today + timedelta(days=within_days))
    return slots[0].start if slots else None


def is_covered(
    physician: Any,
    start: datetime,
    end: datetime,
    *,
    rules: list[AvailabilityRule],
    blocks: list[AvailabilityBlock],
) -> bool:
    """Whether [start, end) sits inside availability and outside every block."""

    tz = ZoneInfo(physician_timezone(physician))
    day = start.astimezone(tz).date()
    windows = available_windows(
        physician, day - timedelta(days=1), day + timedelta(days=1), rules=rules, blocks=blocks
    )
    inside = any(
        window_start <= start and end <= window_end for window_start, window_end in windows
    )
    if not inside:
        return False
    blocked = [
        (item.start, item.end)
        for item in blocked_intervals(
            physician, day - timedelta(days=1), day + timedelta(days=1), rules=rules, blocks=blocks
        )
    ]
    return not _overlaps(start, end, blocked)


def subtract(windows: list[Interval], holes: list[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for start, end in sorted(windows):
        pieces = [(start, end)]
        for hole_start, hole_end in holes:
            next_pieces: list[Interval] = []
            for piece_start, piece_end in pieces:
                if hole_end <= piece_start or hole_start >= piece_end:
                    next_pieces.append((piece_start, piece_end))
                    continue
                if piece_start < hole_start:
                    next_pieces.append((piece_start, hole_start))
                if hole_end < piece_end:
                    next_pieces.append((hole_end, piece_end))
            pieces = next_pieces
        result.extend(pieces)
    merged: list[Interval] = []
    for start, end in sorted(result):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


__all__ = [
    "BlockedInterval",
    "Slot",
    "available_windows",
    "blocked_intervals",
    "busy_intervals",
    "compute_slots",
    "find_slot",
    "is_covered",
    "next_available_at",
    "physician_timezone",
    "subtract",
]
