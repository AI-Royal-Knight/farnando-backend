from __future__ import annotations

from django.db import migrations

NURSE = ["PATIENTS_READ", "CLINICAL_READ", "EVALUATIONS_READ", "MESSAGES_CLINICAL", "TICKETS_MANAGE"]
SUPPORT = [
    "PATIENTS_READ",
    "EVALUATIONS_READ",
    "EVALUATIONS_ASSIGN",
    "TICKETS_MANAGE",
    "COMPLAINTS_MANAGE",
    "ORDERS_READ",
    "MESSAGES_SUPPORT",
]


def create_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    StaffProfile = apps.get_model("staff", "StaffProfile")
    ClinicianProfile = apps.get_model("consultations", "ClinicianProfile")
    nurses = set(
        ClinicianProfile.objects.filter(care_role="NURSE").values_list("user_id", flat=True)
    )
    for user in User.objects.filter(role="STAFF"):
        if StaffProfile.objects.filter(user=user).exists():
            continue
        is_nurse = user.pk in nurses
        StaffProfile.objects.create(
            user=user,
            staff_type="NURSE" if is_nurse else "SUPPORT_AGENT",
            permissions=NURSE if is_nurse else SUPPORT,
            status="ACTIVE" if user.is_active else "SUSPENDED",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("staff", "0001_initial"),
        ("consultations", "0005_consultation_needs_staff_attention"),
    ]

    operations = [migrations.RunPython(create_profiles, migrations.RunPython.noop)]
