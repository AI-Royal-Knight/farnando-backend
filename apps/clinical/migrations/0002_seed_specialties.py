from django.db import migrations

# [OPEN] The list and which specialties need an RNE number are admin-editable (Part 5).
SPECIALTIES = [
    ("Endocrinology", True),
    ("Internal Medicine", True),
    ("Cardiology", True),
    ("Family Medicine", True),
    ("General Medicine", False),
]


def seed(apps, schema_editor):
    Specialty = apps.get_model("clinical", "Specialty")
    for position, (name, requires) in enumerate(SPECIALTIES):
        Specialty.objects.update_or_create(
            name=name, defaults={"requires_registration_number": requires, "position": position}
        )


class Migration(migrations.Migration):
    dependencies = [("clinical", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
