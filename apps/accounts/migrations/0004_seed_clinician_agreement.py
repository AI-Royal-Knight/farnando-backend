from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    LegalDocument = apps.get_model("accounts", "LegalDocument")
    LegalDocument.objects.get_or_create(
        document_type="CLINICIAN_AGREEMENT",
        version="2026-11-01",
        defaults={
            "title": "Clinician Agreement",
            "sections": [
                {"heading": "Clinician Agreement", "body_markdown": "CONTENT PENDING LEGAL REVIEW."}
            ],
            "published_at": timezone.now(),
            "is_current": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_alter_consentrecord_document_type_and_more")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
