"""Physicians onboarded before Part 4 had no verification step; keep them visible."""

from django.db import migrations


def apply(apps, schema_editor):
    ClinicianProfile = apps.get_model("consultations", "ClinicianProfile")
    ClinicianProfile.objects.filter(care_role="PHYSICIAN").update(verification_status="VERIFIED")


class Migration(migrations.Migration):
    dependencies = [("consultations", "0003_availabilityblock_kind_and_more")]

    operations = [migrations.RunPython(apply, migrations.RunPython.noop)]
