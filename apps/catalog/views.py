"""Anonymous public-content endpoints with explicit cache headers."""

from __future__ import annotations

from django.db.models import Avg, Count
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.catalog.models import FAQ, ComplaintCategory, PublicStats, SiteSettings, Testimonial
from apps.catalog.serializers import (
    ComplaintCategorySerializer,
    FAQSerializer,
    PublicStatsSerializer,
    SiteSettingsSerializer,
    TestimonialSerializer,
    TestimonialsPageSerializer,
)
from apps.core.pagination import DefaultPageNumberPagination
from apps.core.schema import COMMON_ERRORS


def _public_cache(response: Response) -> Response:
    response["Cache-Control"] = "public, max-age=300"
    return response


class PublicSiteSettingsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["public"],
        summary="Public site settings",
        auth=[],
        responses={200: SiteSettingsSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return _public_cache(Response(SiteSettingsSerializer(SiteSettings.load()).data))


class PublicFAQView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["public"],
        summary="Public FAQs",
        auth=[],
        responses={200: FAQSerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return _public_cache(
            Response(FAQSerializer(FAQ.objects.filter(is_published=True), many=True).data)
        )


class PublicTestimonialsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["public"],
        summary="Published testimonials with rating summary",
        auth=[],
        responses={200: TestimonialsPageSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = Testimonial.objects.filter(is_published=True)
        aggregate = queryset.aggregate(average_rating=Avg("rating"), review_count=Count("id"))
        paginator = DefaultPageNumberPagination()
        paginator.page_size = 12
        page = paginator.paginate_queryset(queryset, request, view=self) or []
        data = {
            "summary": {
                "average_rating": round(float(aggregate["average_rating"] or 0), 2),
                "review_count": aggregate["review_count"] or 0,
            },
            "count": queryset.count(),
            "next": paginator.get_next_link(),
            "previous": paginator.get_previous_link(),
            "results": TestimonialSerializer(page, many=True).data,
        }
        return _public_cache(Response(data))


class PublicStatsView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["public"],
        summary="Admin-editable marketing statistics",
        auth=[],
        responses={200: PublicStatsSerializer, **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        return _public_cache(Response(PublicStatsSerializer(PublicStats.load()).data))


class PublicComplaintCategoriesView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["public"],
        summary="Active complaint categories",
        auth=[],
        responses={200: ComplaintCategorySerializer(many=True), **COMMON_ERRORS},
    )
    def get(self, request: Request) -> Response:
        queryset = ComplaintCategory.objects.filter(is_active=True)
        return _public_cache(Response(ComplaintCategorySerializer(queryset, many=True).data))
