from __future__ import annotations

from django.db import migrations


def seed_public_content(apps, schema_editor):
    SiteSettings = apps.get_model("catalog", "SiteSettings")
    PublicStats = apps.get_model("catalog", "PublicStats")
    ComplaintCategory = apps.get_model("catalog", "ComplaintCategory")
    SiteSettings.objects.get_or_create(
        defaults={
            "contact_email": "",
            "contact_phone": "",
            "address": "",
            "social_links": [],
            "legal_entity_name": "",
        }
    )
    PublicStats.objects.get_or_create(defaults={
        "sustained_weight_loss_percent": None,
        "consultations_delivered": None,
        "average_reply_minutes": None,
    })
    for position, (code, label) in enumerate(
        [
            ("PRODUCT", "Product"),
            ("SERVICE", "Service"),
            ("BILLING", "Billing"),
            ("DELIVERY", "Delivery"),
            ("PRIVACY", "Privacy"),
            ("OTHER", "Other"),
        ]
    ):
        ComplaintCategory.objects.get_or_create(
            code=code,
            defaults={"label": label, "position": position, "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_public_content, migrations.RunPython.noop),
    ]
