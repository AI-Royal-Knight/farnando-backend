from django.db import migrations

TYPES = [
    ("INITIAL", "Initial Consultation", "First visit with your physician.", 30, True, 0),
    (
        "METABOLIC_RESET",
        "Metabolic Reset Induction",
        "Start-of-protocol visit to review dosing and goals.",
        45,
        False,
        1,
    ),
    ("FOLLOW_UP", "Follow-up", "Progress check-in with your physician.", 20, False, 2),
]


def seed(apps, schema_editor):
    ConsultationType = apps.get_model("consultations", "ConsultationType")
    for code, name, description, minutes, is_initial, position in TYPES:
        ConsultationType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "description": description,
                "duration_minutes": minutes,
                "is_initial": is_initial,
                "position": position,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("consultations", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
