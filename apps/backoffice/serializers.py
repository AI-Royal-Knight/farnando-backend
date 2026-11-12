"""Request serializers for the Part 5 staff and admin endpoints.

Response bodies for composite read models are documented as JSON objects
(deviation 133); every request body is typed here.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from apps.accounts.models import ConsentRecord
from apps.accounts.serializers import ConsentInputSerializer
from apps.assessments.enums import QuestionType, ReviewGroup, ScreeningEffect
from apps.backoffice.models import PrivacyRequestStatus, PrivacyRequestType, ReportKind
from apps.catalog.models import AssetPurpose, ContentCategory, ContentStatus
from apps.core.serializers import StrictSerializer
from apps.orders.models import BillingCadence, OrderStatus
from apps.payments.models import RefundReason
from apps.staff.models import AccountStatus, Permission, StaffType
from apps.support.models import TicketPriority, TicketStatus
from apps.treatments.models import AdministrationRoute, DoseFrequency

KEBAB = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


def _e164(value: str) -> str:
    from apps.patients.services import validate_e164

    return validate_e164(value, "phone")


class MoneyInputSerializer(StrictSerializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"))
    currency = serializers.RegexField(r"^[A-Z]{3}$")

    def to_internal_value(self, data: Any) -> Any:
        value = super().to_internal_value(data)
        return {"amount": f"{value['amount']:.2f}", "currency": value["currency"]}


class StaffReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500)


class StaffOptionalReasonSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Staff and physicians (2)
# ---------------------------------------------------------------------------


class StaffCreateSerializer(StrictSerializer):
    full_name = serializers.CharField(min_length=2, max_length=120)
    email = serializers.EmailField()
    staff_type = serializers.ChoiceField(choices=StaffType.choices)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=Permission.choices), required=False
    )
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)

    def validate_phone(self, value: str) -> str:
        return _e164(value) if value else value


class StaffUpdateSerializer(StrictSerializer):
    full_name = serializers.CharField(min_length=2, max_length=120, required=False)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    staff_type = serializers.ChoiceField(choices=StaffType.choices, required=False)
    permissions = serializers.ListField(
        child=serializers.ChoiceField(choices=Permission.choices), required=False
    )
    reason = serializers.CharField(min_length=5, max_length=300, required=False)

    def validate_phone(self, value: str | None) -> str | None:
        return _e164(value) if value else value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if "permissions" in attrs and not attrs.get("reason"):
            raise serializers.ValidationError({"reason": ["Required when changing permissions."]})
        return attrs


class StaffInvitationAcceptSerializer(StrictSerializer):
    password = serializers.CharField(min_length=8, write_only=True, trim_whitespace=False)
    confirm_password = serializers.CharField(write_only=True, trim_whitespace=False)
    phone = serializers.CharField(max_length=16, required=False, allow_blank=True)
    consents = ConsentInputSerializer(many=True)

    def validate_phone(self, value: str) -> str:
        return _e164(value) if value else value


class StaffInvitationInfoSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField()
    full_name = serializers.CharField()
    staff_type = serializers.ChoiceField(choices=StaffType.choices)
    expires_at = serializers.DateTimeField()


class StaffMemberSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    full_name = serializers.CharField()
    email = serializers.EmailField()
    phone = serializers.CharField(allow_null=True)
    staff_type = serializers.ChoiceField(choices=StaffType.choices)
    permissions = serializers.ListField(child=serializers.ChoiceField(choices=Permission.choices))
    status = serializers.ChoiceField(choices=AccountStatus.choices)
    avatar_url = serializers.CharField(allow_null=True)
    last_login_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class PhysicianInviteSerializer(StrictSerializer):
    email = serializers.EmailField()


class PhysicianVerifySerializer(StrictSerializer):
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)
    checked_registry = serializers.BooleanField()

    def validate_checked_registry(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError("Confirm the license was checked in the registry.")
        return value


class PhysicianSuspendSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=500)
    reassign_to_physician_id = serializers.UUIDField(required=False, allow_null=True)


class CredentialsSerializer(StrictSerializer):
    full_name = serializers.CharField(min_length=2, max_length=120, required=False)
    license_number = serializers.RegexField(r"^[A-Za-z0-9]{3,20}$", required=False)
    specialty_id = serializers.UUIDField(required=False)
    specialty_registration_number = serializers.RegexField(
        r"^[A-Za-z0-9]{3,20}$", required=False, allow_blank=True, allow_null=True
    )
    reason = serializers.CharField(min_length=5, max_length=300)


# ---------------------------------------------------------------------------
# Patients and evaluations (4, 5)
# ---------------------------------------------------------------------------


class AssignPhysicianSerializer(StrictSerializer):
    physician_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True)


class EvaluationAssignSerializer(StrictSerializer):
    physician_id = serializers.UUIDField()
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)


class EvaluationPrioritySerializer(StrictSerializer):
    priority = serializers.ChoiceField(choices=[("NORMAL", "Normal"), ("URGENT", "Urgent")])


class StaffRequestLabsSerializer(StrictSerializer):
    panels = serializers.ListField(
        child=serializers.CharField(max_length=40), min_length=1, max_length=10
    )
    message = serializers.CharField(max_length=500, required=False, allow_blank=True)


class ReopenSerializer(StrictSerializer):
    note = serializers.CharField(max_length=500)


# ---------------------------------------------------------------------------
# Tickets and complaints (6)
# ---------------------------------------------------------------------------


class PatientTicketCreateSerializer(StrictSerializer):
    subject = serializers.CharField(min_length=3, max_length=120)
    category_code = serializers.CharField(max_length=32)
    message = serializers.CharField(min_length=10, max_length=4000)
    order_id = serializers.UUIDField(required=False, allow_null=True)


class TicketMessageCreateSerializer(StrictSerializer):
    body = serializers.CharField(min_length=1, max_length=4000)


class TicketReplySerializer(StrictSerializer):
    body = serializers.CharField(min_length=1, max_length=4000)
    is_internal = serializers.BooleanField(default=False)


class TicketAssignSerializer(StrictSerializer):
    staff_id = serializers.UUIDField(allow_null=True)


class TicketStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=TicketStatus.choices)


class TicketPrioritySerializer(StrictSerializer):
    priority = serializers.ChoiceField(choices=TicketPriority.choices)


class ComplaintRespondSerializer(StrictSerializer):
    body = serializers.CharField(min_length=10, max_length=4000)
    resolution = serializers.ChoiceField(
        choices=[("RESOLVED", "Resolved"), ("IN_REVIEW", "In review")]
    )


class ComplaintStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(
        choices=[("IN_REVIEW", "In review"), ("RESOLVED", "Resolved"), ("CLOSED", "Closed")]
    )
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


# ---------------------------------------------------------------------------
# Orders, subscriptions, refunds (7)
# ---------------------------------------------------------------------------


class TrackingSerializer(StrictSerializer):
    carrier = serializers.CharField(max_length=80)
    tracking_number = serializers.CharField(max_length=80)
    tracking_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)


class OrderTransitionSerializer(StrictSerializer):
    to_status = serializers.ChoiceField(choices=OrderStatus.choices)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)
    tracking = TrackingSerializer(required=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if attrs["to_status"] == OrderStatus.IN_TRANSIT and not attrs.get("tracking"):
            raise serializers.ValidationError({"tracking": ["Required for IN_TRANSIT."]})
        return attrs


class OrderNoteSerializer(StrictSerializer):
    body = serializers.CharField(min_length=1, max_length=1000)


class RefundSerializer(StrictSerializer):
    amount = MoneyInputSerializer(required=False)
    reason_code = serializers.ChoiceField(choices=RefundReason.choices)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class RefundResponseSerializer(serializers.Serializer[Any]):
    payment_id = serializers.UUIDField()
    status = serializers.CharField()
    amount = serializers.DictField(child=serializers.CharField())


# ---------------------------------------------------------------------------
# Catalog (8)
# ---------------------------------------------------------------------------


class ProductDoseSerializer(StrictSerializer):
    value = serializers.DecimalField(max_digits=10, decimal_places=3)
    unit = serializers.CharField(max_length=20)
    label = serializers.CharField(max_length=60)  # type: ignore[assignment]

    def to_internal_value(self, data: Any) -> Any:
        value = super().to_internal_value(data)
        return {"value": float(value["value"]), "unit": value["unit"], "label": value["label"]}


class EssentialSerializer(StrictSerializer):
    title = serializers.CharField(max_length=80)
    body = serializers.CharField(max_length=500)


class ProductInputSerializer(StrictSerializer):
    name = serializers.CharField(min_length=2, max_length=120)
    active_ingredient = serializers.CharField(max_length=120)
    form = serializers.CharField(max_length=60)
    route = serializers.ChoiceField(choices=AdministrationRoute.choices)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    image_upload_id = serializers.UUIDField(required=False)
    allowed_doses = ProductDoseSerializer(many=True, min_length=1, max_length=20)  # type: ignore[call-arg]
    starting_dose_labels = serializers.ListField(child=serializers.CharField(max_length=60))
    default_frequency = serializers.ChoiceField(choices=DoseFrequency.choices)
    max_refills = serializers.IntegerField(min_value=0, max_value=12)
    safety_information_markdown = serializers.CharField(required=False, allow_blank=True)
    lifestyle_essentials = EssentialSerializer(many=True, required=False, max_length=6)  # type: ignore[call-arg]
    is_active = serializers.BooleanField(required=False)


class EligibilitySerializer(StrictSerializer):
    min_age = serializers.IntegerField(min_value=0, max_value=120)
    max_age = serializers.IntegerField(min_value=0, max_value=120)
    min_bmi = serializers.FloatField(allow_null=True, required=False)
    max_bmi = serializers.FloatField(allow_null=True, required=False)


class ProjectionSerializer(StrictSerializer):
    months = serializers.IntegerField(min_value=1, max_value=24)
    loss_percent_by_month = serializers.ListField(
        child=serializers.FloatField(min_value=0, max_value=100)
    )


class HoursRangeSerializer(StrictSerializer):
    min = serializers.IntegerField(min_value=1, max_value=720)
    max = serializers.IntegerField(min_value=1, max_value=720)


class ProgramInputSerializer(StrictSerializer):
    title = serializers.CharField(min_length=2, max_length=120)
    category = serializers.CharField(max_length=40)
    short_description = serializers.CharField(max_length=200)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)
    icon = serializers.CharField(max_length=60, required=False, allow_blank=True)
    product_ids = serializers.ListField(child=serializers.UUIDField())
    allowed_specialty_ids = serializers.ListField(child=serializers.UUIDField())
    eligibility = EligibilitySerializer()
    projection = ProjectionSerializer(required=False, allow_null=True)
    estimated_review_hours = HoursRangeSerializer(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, required=False)


class PackageItemInputSerializer(StrictSerializer):
    product_id = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1, max_value=12)


class PackageInputSerializer(StrictSerializer):
    program_id = serializers.UUIDField()
    name = serializers.CharField(min_length=2, max_length=80)
    badge = serializers.CharField(max_length=30, required=False, allow_null=True, allow_blank=True)
    supply_duration_weeks = serializers.IntegerField(min_value=1)
    billing_cadence = serializers.ChoiceField(choices=BillingCadence.choices)
    display_price_per_month = MoneyInputSerializer()
    charge_per_cycle = MoneyInputSerializer()
    included_services = serializers.ListField(
        child=serializers.CharField(max_length=80), max_length=10, required=False
    )
    items = PackageItemInputSerializer(many=True, min_length=1)  # type: ignore[call-arg]
    is_active = serializers.BooleanField(required=False)
    position = serializers.IntegerField(min_value=0, required=False)
    allow_price_mismatch = serializers.BooleanField(required=False, default=False)


class ReorderPackagesSerializer(StrictSerializer):
    package_ids = serializers.ListField(child=serializers.UUIDField())


class FeeSerializer(StrictSerializer):
    amount = MoneyInputSerializer()
    is_waived = serializers.BooleanField()
    inclusions = serializers.ListField(
        child=serializers.CharField(max_length=120), max_length=10, required=False
    )

    def validate_amount(self, value: dict[str, str]) -> dict[str, str]:
        if Decimal(value["amount"]) > Decimal("1000"):
            raise serializers.ValidationError("Must be between 0 and 1000.")
        return value


class QuestionnaireCreateSerializer(StrictSerializer):
    program_id = serializers.UUIDField()
    title = serializers.CharField(min_length=2, max_length=120)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)


class QuestionnaireUpdateSerializer(StrictSerializer):
    title = serializers.CharField(min_length=2, max_length=120, required=False)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)


class StepInputSerializer(StrictSerializer):
    title = serializers.CharField(min_length=1, max_length=120)
    subtitle = serializers.CharField(max_length=200, required=False, allow_blank=True)
    key = serializers.RegexField(KEBAB, max_length=40)


class OptionInputSerializer(StrictSerializer):
    label = serializers.CharField(min_length=1, max_length=120)  # type: ignore[assignment]
    is_exclusive = serializers.BooleanField(default=False)
    screening_effect = serializers.ChoiceField(
        choices=ScreeningEffect.choices, default=ScreeningEffect.NONE
    )


class ShowIfSerializer(StrictSerializer):
    question_id = serializers.UUIDField()
    equals = serializers.ListField(child=serializers.CharField(max_length=120), min_length=1)


class QuestionInputSerializer(StrictSerializer):
    text = serializers.CharField(min_length=3, max_length=300)
    help_text = serializers.CharField(max_length=300, required=False, allow_blank=True)  # type: ignore[assignment]
    type = serializers.ChoiceField(choices=QuestionType.choices)
    required = serializers.BooleanField()  # type: ignore[assignment]
    options = OptionInputSerializer(many=True, required=False)
    show_if = ShowIfSerializer(required=False, allow_null=True)
    review_group = serializers.ChoiceField(choices=ReviewGroup.choices)
    placeholder = serializers.CharField(max_length=120, required=False, allow_blank=True)
    max_length = serializers.IntegerField(
        min_value=1, max_value=2000, required=False, allow_null=True
    )


class ReorderStepSerializer(StrictSerializer):
    id = serializers.UUIDField()
    question_ids = serializers.ListField(child=serializers.UUIDField())


class QuestionnaireReorderSerializer(StrictSerializer):
    steps = ReorderStepSerializer(many=True)


class PublishSerializer(StrictSerializer):
    confirm = serializers.BooleanField()
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True)

    def validate_confirm(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError("Set confirm to true to publish.")
        return value


class ValidationResultSerializer(serializers.Serializer[Any]):
    valid = serializers.BooleanField()
    errors = serializers.ListField(child=serializers.DictField(child=serializers.CharField()))  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Content, reference data, legal, uploads, settings (9)
# ---------------------------------------------------------------------------


class PricingInputSerializer(StrictSerializer):
    price_from = MoneyInputSerializer()
    price_to = MoneyInputSerializer()
    period = serializers.ChoiceField(
        choices=[("MONTH", "Month"), ("WEEK", "Week"), ("ONE_TIME", "One time")]
    )
    note = serializers.CharField(max_length=160, required=False, allow_blank=True)


class ContentItemInputSerializer(StrictSerializer):
    title = serializers.CharField(min_length=2, max_length=120)
    accent_phrase = serializers.CharField(
        max_length=120, required=False, allow_blank=True, allow_null=True
    )
    description = serializers.CharField(min_length=10, max_length=1000)
    category = serializers.ChoiceField(choices=ContentCategory.choices)
    status = serializers.ChoiceField(choices=ContentStatus.choices, default=ContentStatus.DRAFT)
    product_image_upload_id = serializers.UUIDField(required=False, allow_null=True)
    lifestyle_image_upload_ids = serializers.ListField(
        child=serializers.UUIDField(), max_length=2, required=False
    )
    benefits = serializers.ListField(
        child=serializers.CharField(min_length=3, max_length=160), max_length=10, required=False
    )
    pricing = PricingInputSerializer(required=False, allow_null=True)
    cta_text = serializers.CharField(
        max_length=40, required=False, allow_blank=True, allow_null=True
    )
    program_id = serializers.UUIDField(required=False, allow_null=True)
    position = serializers.IntegerField(min_value=0, required=False)


class AssetUploadCreateSerializer(StrictSerializer):
    purpose = serializers.ChoiceField(choices=AssetPurpose.choices)
    content_type = serializers.ChoiceField(
        choices=[("image/png", "PNG"), ("image/jpeg", "JPEG"), ("image/webp", "WebP")]
    )
    size_bytes = serializers.IntegerField(min_value=1)


class AssetReadySerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()
    url = serializers.CharField()


CODE_UPPER = r"^[A-Z][A-Z_]{1,29}$"
CODE_PANEL = r"^[A-Z0-9_]{2,30}$"


class FAQInputSerializer(StrictSerializer):
    question = serializers.CharField(min_length=5, max_length=200)
    answer_markdown = serializers.CharField(min_length=5, max_length=4000)
    position = serializers.IntegerField(min_value=0)
    is_published = serializers.BooleanField()


class TestimonialInputSerializer(StrictSerializer):
    author_name = serializers.CharField(min_length=2, max_length=60)
    rating = serializers.IntegerField(min_value=1, max_value=5)
    body = serializers.CharField(min_length=10, max_length=600)
    patient_consent_recorded = serializers.BooleanField()
    is_published = serializers.BooleanField()


class ComplaintCategoryInputSerializer(StrictSerializer):
    code = serializers.RegexField(CODE_UPPER)
    label = serializers.CharField(min_length=2, max_length=80)  # type: ignore[assignment]
    position = serializers.IntegerField(min_value=0)
    is_active = serializers.BooleanField()


class TicketCategoryInputSerializer(StrictSerializer):
    code = serializers.RegexField(CODE_UPPER)
    label = serializers.CharField(min_length=2, max_length=80)  # type: ignore[assignment]
    requires_clinical_staff = serializers.BooleanField()
    position = serializers.IntegerField(min_value=0)
    is_active = serializers.BooleanField()


class SpecialtyInputSerializer(StrictSerializer):
    name = serializers.CharField(min_length=2, max_length=80)
    requires_registration_number = serializers.BooleanField()
    is_active = serializers.BooleanField()


class ConsultationTypeInputSerializer(StrictSerializer):
    code = serializers.RegexField(CODE_UPPER)
    name = serializers.CharField(min_length=2, max_length=80)
    description = serializers.CharField(max_length=300, allow_blank=True)
    duration_minutes = serializers.IntegerField(min_value=10, max_value=120)
    is_initial = serializers.BooleanField()
    is_active = serializers.BooleanField()

    def validate_duration_minutes(self, value: int) -> int:
        if value % 5:
            raise serializers.ValidationError("Use steps of 5 minutes.")
        return value


class LabPanelInputSerializer(StrictSerializer):
    code = serializers.RegexField(CODE_PANEL)
    name = serializers.CharField(min_length=2, max_length=120)
    description = serializers.CharField(max_length=300, allow_blank=True)
    is_active = serializers.BooleanField()


REFERENCE_SERIALIZERS: dict[str, type[StrictSerializer]] = {
    "faqs": FAQInputSerializer,
    "testimonials": TestimonialInputSerializer,
    "complaint-categories": ComplaintCategoryInputSerializer,
    "support-ticket-categories": TicketCategoryInputSerializer,
    "specialties": SpecialtyInputSerializer,
    "consultation-types": ConsultationTypeInputSerializer,
    "lab-panels": LabPanelInputSerializer,
}


class LegalSectionSerializer(StrictSerializer):
    heading = serializers.CharField(max_length=200)
    body_markdown = serializers.CharField(max_length=50000)


class LegalDocumentInputSerializer(StrictSerializer):
    document_type = serializers.ChoiceField(choices=ConsentRecord.DocumentType.choices)
    title = serializers.CharField(min_length=2, max_length=200)
    sections = LegalSectionSerializer(many=True, min_length=1)  # type: ignore[call-arg]
    effective_at = serializers.DateTimeField(required=False, allow_null=True)


class SocialLinkInputSerializer(StrictSerializer):
    network = serializers.CharField(max_length=40)
    url = serializers.URLField()


class SiteGroupSerializer(StrictSerializer):
    contact_email = serializers.EmailField(allow_blank=True)
    contact_phone = serializers.CharField(max_length=40, allow_blank=True)
    address = serializers.CharField(max_length=500, allow_blank=True)
    social_links = SocialLinkInputSerializer(many=True)
    legal_entity_name = serializers.CharField(max_length=200, allow_blank=True)


class StatsGroupSerializer(StrictSerializer):
    sustained_weight_loss_percent = serializers.FloatField(
        allow_null=True, min_value=0, max_value=100
    )
    consultations_delivered = serializers.IntegerField(allow_null=True, min_value=0)
    average_reply_minutes = serializers.IntegerField(allow_null=True, min_value=0)


class EvaluationGroupSerializer(StrictSerializer):
    estimated_review_hours = HoursRangeSerializer()
    claim_timeout_hours = serializers.IntegerField(min_value=1, max_value=72)
    urgent_after_hours = serializers.IntegerField(min_value=1, max_value=720)
    reapply_cooldown_days = serializers.IntegerField(min_value=0, max_value=365)


class BookingGroupSerializer(StrictSerializer):
    min_booking_lead_hours = serializers.IntegerField(min_value=0, max_value=168)
    reschedule_min_hours_before = serializers.IntegerField(min_value=0, max_value=168)
    cancel_min_hours_before = serializers.IntegerField(min_value=0, max_value=168)
    join_window_before_minutes = serializers.IntegerField(min_value=0, max_value=120)
    join_window_after_minutes = serializers.IntegerField(min_value=0, max_value=120)


class ComplaintsGroupSerializer(StrictSerializer):
    response_sla_hours = serializers.IntegerField(min_value=1, max_value=720)


class SupportGroupSerializer(StrictSerializer):
    first_response_sla_minutes = serializers.IntegerField(min_value=1, max_value=10080)
    auto_close_resolved_days = serializers.IntegerField(min_value=1, max_value=90)


class MessagingGroupSerializer(StrictSerializer):
    safety_notice = serializers.CharField(max_length=4000)


class PaymentsGroupSerializer(StrictSerializer):
    refund_approval_limit = MoneyInputSerializer()
    refund_fee_on_not_suitable = serializers.BooleanField()


SETTINGS_SERIALIZERS: dict[str, type[StrictSerializer]] = {
    "site": SiteGroupSerializer,
    "stats": StatsGroupSerializer,
    "evaluation": EvaluationGroupSerializer,
    "booking": BookingGroupSerializer,
    "complaints": ComplaintsGroupSerializer,
    "support": SupportGroupSerializer,
    "messaging": MessagingGroupSerializer,
    "payments": PaymentsGroupSerializer,
}


# ---------------------------------------------------------------------------
# Reports, privacy (10, 11)
# ---------------------------------------------------------------------------


class ExportCreateSerializer(StrictSerializer):
    report = serializers.ChoiceField(choices=ReportKind.choices)
    format = serializers.ChoiceField(choices=[("CSV", "CSV")], default="CSV")

    def to_internal_value(self, data: Any) -> Any:
        value = super().to_internal_value(
            {key: item for key, item in data.items() if key not in ("from", "to")}
        )
        errors: dict[str, Any] = {}
        for name in ("from", "to"):
            field = serializers.DateField()
            try:
                value[name] = field.run_validation(data.get(name, serializers.empty))
            except serializers.ValidationError as exc:
                errors[name] = exc.detail
        if errors:
            raise serializers.ValidationError(errors)
        return value


class ExportCreatedSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    status = serializers.CharField()


class ExportStatusSerializer(serializers.Serializer[Any]):
    status = serializers.CharField()
    download_url = serializers.CharField(allow_null=True)
    expires_at = serializers.DateTimeField(allow_null=True)


class PrivacyRequestCreateSerializer(StrictSerializer):
    type = serializers.ChoiceField(choices=PrivacyRequestType.choices)
    details = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class PrivacyRequestSerializer(serializers.Serializer[Any]):
    id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=PrivacyRequestType.choices)
    status = serializers.ChoiceField(choices=PrivacyRequestStatus.choices)
    details = serializers.CharField(allow_null=True)
    response_note = serializers.CharField(allow_null=True)
    due_at = serializers.DateTimeField()
    download_url = serializers.CharField(allow_null=True)
    download_expires_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class PrivacyStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=PrivacyRequestStatus.choices)
    note = serializers.CharField(max_length=1000, required=False, allow_blank=True)


class TicketCategoryPublicSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    label = serializers.CharField()  # type: ignore[assignment]
