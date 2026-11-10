"""Public-content contracts."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from apps.catalog.models import FAQ, ComplaintCategory, PublicStats, SiteSettings, Testimonial


class SocialLinkSerializer(serializers.Serializer[Any]):
    platform = serializers.CharField(max_length=32)
    url = serializers.URLField()


class SiteSettingsSerializer(serializers.ModelSerializer[SiteSettings]):
    social_links = SocialLinkSerializer(many=True)

    class Meta:
        model = SiteSettings
        fields = [
            "contact_email",
            "contact_phone",
            "address",
            "social_links",
            "legal_entity_name",
        ]
        read_only_fields = fields


class FAQSerializer(serializers.ModelSerializer[FAQ]):
    class Meta:
        model = FAQ
        fields = ["id", "question", "answer_markdown", "position"]
        read_only_fields = fields


class TestimonialSerializer(serializers.ModelSerializer[Testimonial]):
    class Meta:
        model = Testimonial
        fields = ["id", "author_name", "rating", "body", "published_at"]
        read_only_fields = fields


class TestimonialSummarySerializer(serializers.Serializer[Any]):
    average_rating = serializers.FloatField()
    review_count = serializers.IntegerField()


class TestimonialsPageSerializer(serializers.Serializer[Any]):
    summary = TestimonialSummarySerializer()
    count = serializers.IntegerField()
    next = serializers.URLField(allow_null=True)
    previous = serializers.URLField(allow_null=True)
    results = TestimonialSerializer(many=True)


class PublicStatsSerializer(serializers.ModelSerializer[PublicStats]):
    class Meta:
        model = PublicStats
        fields = [
            "sustained_weight_loss_percent",
            "consultations_delivered",
            "average_reply_minutes",
        ]
        read_only_fields = fields


class ComplaintCategorySerializer(serializers.ModelSerializer[ComplaintCategory]):
    class Meta:
        model = ComplaintCategory
        fields = ["code", "label"]
        read_only_fields = fields
