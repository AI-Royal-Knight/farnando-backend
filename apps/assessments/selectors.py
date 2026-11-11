"""Scoped read queries for assessments and lab files."""

from __future__ import annotations

from typing import Any

from django.db.models import Q, QuerySet
from rest_framework.exceptions import NotFound

from apps.accounts.enums import Role
from apps.assessments.models import Assessment, LabUpload


def assessments_visible_to(user: Any) -> QuerySet[Assessment]:
    queryset = Assessment.objects.select_related(
        "program", "questionnaire_version", "measurement", "evaluation"
    ).prefetch_related("answers__question")
    if user.role == Role.PATIENT:
        return queryset.filter(user=user)
    if user.role == Role.PHYSICIAN:
        return queryset.filter(
            Q(evaluation__physician=user)
            | Q(evaluation__status="SUBMITTED", evaluation__physician__isnull=True)
        ).distinct()
    if user.role in (Role.STAFF, Role.ADMIN):
        return queryset
    return queryset.none()


def get_assessment_for_read(*, user: Any, assessment_id: Any) -> Assessment:
    assessment = assessments_visible_to(user).filter(pk=assessment_id).first()
    if assessment is None:
        raise NotFound()
    return assessment


def get_own_assessment(*, user: Any, assessment_id: Any) -> Assessment:
    assessment = (
        Assessment.objects.select_related(
            "program", "questionnaire_version", "measurement", "evaluation"
        )
        .prefetch_related("answers__question")
        .filter(pk=assessment_id, user=user)
        .first()
    )
    if assessment is None:
        raise NotFound()
    return assessment


def get_own_lab_upload(*, user: Any, upload_id: Any) -> LabUpload:
    upload = (
        LabUpload.objects.select_related("assessment", "assessment__evaluation")
        .filter(pk=upload_id, assessment__user=user, is_deleted=False)
        .first()
    )
    if upload is None:
        raise NotFound()
    return upload


def get_lab_upload_for_download(*, user: Any, upload_id: Any) -> LabUpload:
    queryset = LabUpload.objects.select_related("assessment", "assessment__evaluation").filter(
        pk=upload_id
    )
    if user.role == Role.PATIENT:
        queryset = queryset.filter(assessment__user=user)
    elif user.role == Role.PHYSICIAN:
        queryset = queryset.filter(assessment__evaluation__physician=user)
    elif user.role == Role.ADMIN:
        pass
    else:
        queryset = queryset.none()
    upload = queryset.first()
    if upload is None:
        raise NotFound()
    return upload
