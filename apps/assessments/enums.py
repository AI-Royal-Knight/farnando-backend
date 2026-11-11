"""Enumerations from Contract Part 2.

Values are part of the public contract and must not be renamed. ``ScreeningEffect``
is explicitly admin-only and must never be serialized to a patient.
"""

from __future__ import annotations

from django.db import models


class AssessmentStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    CANCELLED = "CANCELLED", "Cancelled"


class EvaluationStatus(models.TextChoices):
    SCREENED_OUT = "SCREENED_OUT", "Screened out"
    AWAITING_PAYMENT = "AWAITING_PAYMENT", "Awaiting payment"
    SUBMITTED = "SUBMITTED", "Submitted"
    IN_REVIEW = "IN_REVIEW", "In review"
    PENDING_LABS = "PENDING_LABS", "Pending labs"
    APPROVED = "APPROVED", "Approved"
    NOT_SUITABLE = "NOT_SUITABLE", "Not suitable"
    CANCELLED = "CANCELLED", "Cancelled"


class ScreeningResult(models.TextChoices):
    ELIGIBLE = "ELIGIBLE", "Eligible"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs review"
    NOT_ELIGIBLE = "NOT_ELIGIBLE", "Not eligible"


class ScreeningEffect(models.TextChoices):
    """Admin-only configuration; never returned by a patient endpoint."""

    NONE = "NONE", "None"
    NEEDS_REVIEW = "NEEDS_REVIEW", "Needs review"
    NOT_ELIGIBLE = "NOT_ELIGIBLE", "Not eligible"


class Sex(models.TextChoices):
    MALE = "MALE", "Male"
    FEMALE = "FEMALE", "Female"
    OTHER = "OTHER", "Other"


class WeightUnit(models.TextChoices):
    KG = "KG", "Kilograms"
    LB = "LB", "Pounds"


class HeightUnit(models.TextChoices):
    CM = "CM", "Centimetres"
    IN = "IN", "Inches"


class BmiCategory(models.TextChoices):
    UNDERWEIGHT = "UNDERWEIGHT", "Underweight"
    HEALTHY = "HEALTHY", "Healthy"
    OVERWEIGHT = "OVERWEIGHT", "Overweight"
    OBESE = "OBESE", "Obese"


class StepType(models.TextChoices):
    MEASUREMENTS = "MEASUREMENTS", "Measurements"
    QUESTIONS = "QUESTIONS", "Questions"


class QuestionType(models.TextChoices):
    SINGLE_CHOICE = "SINGLE_CHOICE", "Single choice"
    MULTI_CHOICE = "MULTI_CHOICE", "Multiple choice"
    YES_NO = "YES_NO", "Yes / no"
    TEXT = "TEXT", "Text"


class YesNo(models.TextChoices):
    YES = "YES", "Yes"
    NO = "NO", "No"


class LabUploadStatus(models.TextChoices):
    PENDING_UPLOAD = "PENDING_UPLOAD", "Pending upload"
    SCANNING = "SCANNING", "Scanning"
    CLEAN = "CLEAN", "Clean"
    REJECTED = "REJECTED", "Rejected"


class LabRejectionReason(models.TextChoices):
    VIRUS_DETECTED = "VIRUS_DETECTED", "Virus detected"
    INVALID_FILE_TYPE = "INVALID_FILE_TYPE", "Invalid file type"
    FILE_TOO_LARGE = "FILE_TOO_LARGE", "File too large"
    UPLOAD_MISSING = "UPLOAD_MISSING", "Upload missing"
    UNREADABLE = "UNREADABLE", "Unreadable"


class LabsStatus(models.TextChoices):
    NOT_STARTED = "NOT_STARTED", "Not started"
    DEFERRED = "DEFERRED", "Deferred"
    SUBMITTED = "SUBMITTED", "Submitted"


class FeeStatus(models.TextChoices):
    NOT_REQUIRED = "NOT_REQUIRED", "Not required"
    WAIVED = "WAIVED", "Waived"
    PENDING = "PENDING", "Pending"
    PAID = "PAID", "Paid"
    REFUNDED = "REFUNDED", "Refunded"


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    REQUIRES_ACTION = "REQUIRES_ACTION", "Requires action"
    SUCCEEDED = "SUCCEEDED", "Succeeded"
    FAILED = "FAILED", "Failed"
    REFUNDED = "REFUNDED", "Refunded"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"


class PaymentPurpose(models.TextChoices):
    EVALUATION_FEE = "EVALUATION_FEE", "Evaluation fee"
    ORDER = "ORDER", "Order"
    SUBSCRIPTION_RENEWAL = "SUBSCRIPTION_RENEWAL", "Subscription renewal"


class ChecklistState(models.TextChoices):
    DONE = "DONE", "Done"
    ACTIVE = "ACTIVE", "Active"
    PENDING = "PENDING", "Pending"
    BLOCKED = "BLOCKED", "Blocked"


class JourneyStep(models.TextChoices):
    FIRST_ASSESSMENT = "FIRST_ASSESSMENT", "First assessment"
    SECOND_ASSESSMENT = "SECOND_ASSESSMENT", "Second assessment"
    ELIGIBILITY_REVIEW = "ELIGIBILITY_REVIEW", "Eligibility and review"
    PRESCRIPTION = "PRESCRIPTION", "Prescription"
    ORDER = "ORDER", "Order"


class PaymentMethod(models.TextChoices):
    CULQI = "CULQI", "Culqi"


class ReviewGroup(models.TextChoices):
    """Admin-only grouping for the physician review packet (Part 4, A1)."""

    PRIMARY_CONDITIONS = "PRIMARY_CONDITIONS", "Primary Conditions"
    CONTRAINDICATION_SCREENING = "CONTRAINDICATION_SCREENING", "Contraindication Screening"
    CURRENT_MEDICATIONS = "CURRENT_MEDICATIONS", "Current Medications"
    WEIGHT_LOSS_HISTORY = "WEIGHT_LOSS_HISTORY", "Weight Loss History"
    OTHER = "OTHER", "Other"


class RejectionReasonCode(models.TextChoices):
    CONTRAINDICATION_FOUND = "CONTRAINDICATION_FOUND", "Contraindication found"
    CLINICAL_CRITERIA_NOT_MET = "CLINICAL_CRITERIA_NOT_MET", "Clinical criteria not met"
    INCOMPLETE_OR_INCONSISTENT_INFORMATION = (
        "INCOMPLETE_OR_INCONSISTENT_INFORMATION",
        "Incomplete or inconsistent information",
    )
    IN_PERSON_CARE_RECOMMENDED = "IN_PERSON_CARE_RECOMMENDED", "In-person care recommended"
    OTHER = "OTHER", "Other"
