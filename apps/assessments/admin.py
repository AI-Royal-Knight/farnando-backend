"""Admin configuration for programmes, questionnaires and evaluations.

Screening effects are visible and editable only here. They are never exposed by
a patient-facing serializer.
"""

from __future__ import annotations

from django.contrib import admin

from apps.assessments.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentMeasurement,
    Evaluation,
    EvaluationFee,
    EvaluationReviewRequest,
    EvaluationStatusHistory,
    LabUpload,
    Payment,
    Prescription,
    Program,
    QuestionnaireOption,
    QuestionnaireQuestion,
    QuestionnaireStep,
    QuestionnaireVersion,
)


class QuestionnaireVersionInline(admin.TabularInline):  # type: ignore[type-arg]
    model = QuestionnaireVersion
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("name", "slug", "position", "is_active", "fee_amount", "fee_currency")
    list_filter = ("is_active", "fee_currency")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [QuestionnaireVersionInline]
    fieldsets = (
        (
            None,
            {"fields": ("slug", "name", "description", "icon", "position", "is_active")},
        ),
        (
            "Assessment rules (medical review required)",
            {
                "fields": (
                    "requires_target_lower",
                    "min_bmi",
                    "min_bmi_effect",
                    "age_min",
                    "age_max",
                    "not_suitable_cooldown_days",
                )
            },
        ),
        (
            "Evaluation fee",
            {
                "fields": (
                    "fee_amount",
                    "fee_currency",
                    "fee_is_waived",
                    "fee_title",
                    "fee_inclusions",
                    "payment_methods",
                )
            },
        ),
        (
            "Projection and messages",
            {
                "fields": (
                    "projection_months",
                    "projection_weekly_loss_kg",
                    "projection_disclaimer",
                    "estimated_review_hours_min",
                    "estimated_review_hours_max",
                    "screening_message_eligible",
                    "screening_message_needs_review",
                    "screening_message_not_eligible",
                    "evaluation_copy",
                    "not_suitable_title",
                    "not_suitable_message",
                    "support_contact_label",
                )
            },
        ),
        (
            "Labs",
            {
                "fields": (
                    "lab_recommendations_intro",
                    "lab_group_label",
                    "lab_panels",
                )
            },
        ),
        (
            "Patient portal content",
            {"fields": ("health_summary_subheadline", "lifestyle_essentials")},
        ),
    )


class QuestionnaireStepInline(admin.TabularInline):  # type: ignore[type-arg]
    model = QuestionnaireStep
    extra = 0
    ordering = ("position",)


@admin.register(QuestionnaireVersion)
class QuestionnaireVersionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("program", "version", "is_published", "published_at", "created_at")
    list_filter = ("is_published", "program")
    inlines = [QuestionnaireStepInline]


class QuestionnaireQuestionInline(admin.TabularInline):  # type: ignore[type-arg]
    model = QuestionnaireQuestion
    extra = 0
    ordering = ("position",)
    fields = (
        "key",
        "text",
        "type",
        "required",
        "position",
        "max_length",
        "placeholder",
        "show_if",
        "show_if_equals",
        "screening_effect",
        "yes_effect",
        "no_effect",
    )


@admin.register(QuestionnaireStep)
class QuestionnaireStepAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("key", "questionnaire", "type", "position")
    list_filter = ("type", "questionnaire__program")
    inlines = [QuestionnaireQuestionInline]


class QuestionnaireOptionInline(admin.TabularInline):  # type: ignore[type-arg]
    model = QuestionnaireOption
    extra = 0
    ordering = ("position",)


@admin.register(QuestionnaireQuestion)
class QuestionnaireQuestionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("key", "step", "type", "required", "position")
    list_filter = ("type", "step__questionnaire__program")
    search_fields = ("key", "text")
    inlines = [QuestionnaireOptionInline]


class AssessmentMeasurementInline(admin.StackedInline):  # type: ignore[type-arg]
    model = AssessmentMeasurement
    extra = 0
    can_delete = False
    readonly_fields = (
        "current_weight_kg",
        "target_weight_kg",
        "height_cm",
        "sex",
        "date_of_birth",
        "bmi",
        "bmi_category",
        "target_bmi",
        "weight_goal_kg",
    )


class AssessmentAnswerInline(admin.TabularInline):  # type: ignore[type-arg]
    model = AssessmentAnswer
    extra = 0
    readonly_fields = ("question", "option_ids", "answer", "text")


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "user", "program", "status", "screening_result", "created_at")
    list_filter = ("status", "program", "screening_result", "labs_status")
    search_fields = ("user__email", "id")
    readonly_fields = ("id", "created_at", "updated_at", "submitted_at", "cancelled_at")
    inlines = [AssessmentMeasurementInline, AssessmentAnswerInline]


class EvaluationStatusHistoryInline(admin.TabularInline):  # type: ignore[type-arg]
    model = EvaluationStatusHistory
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "actor", "reason", "created_at")


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("id", "assessment", "status", "physician", "submitted_at", "decided_at")
    list_filter = ("status", "physician")
    search_fields = ("assessment__user__email", "id")
    readonly_fields = ("id", "created_at", "updated_at", "submitted_at", "decided_at")
    inlines = [EvaluationStatusHistoryInline]


@admin.register(EvaluationFee)
class EvaluationFeeAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("evaluation", "status", "amount", "currency", "is_waived")
    list_filter = ("status", "currency", "is_waived")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "id",
        "purpose",
        "user",
        "status",
        "amount",
        "refunded_amount",
        "currency",
        "provider",
        "created_at",
    )
    list_filter = ("purpose", "status", "provider", "currency")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(EvaluationReviewRequest)
class EvaluationReviewRequestAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("evaluation", "requested_at", "resolved_at")
    readonly_fields = ("requested_at",)


@admin.register(LabUpload)
class LabUploadAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = (
        "file_name",
        "assessment",
        "status",
        "rejection_reason",
        "size_bytes",
        "created_at",
    )
    list_filter = ("status", "rejection_reason")
    search_fields = ("file_name", "assessment__user__email")
    readonly_fields = (
        "id",
        "storage_key",
        "created_at",
        "updated_at",
        "scanned_at",
        "deleted_at",
    )


@admin.register(Prescription)
class PrescriptionAdmin(admin.ModelAdmin):  # type: ignore[type-arg]
    list_display = ("evaluation", "medication_name", "dosage_label", "can_download", "can_order")
    readonly_fields = ("id", "created_at", "updated_at")
