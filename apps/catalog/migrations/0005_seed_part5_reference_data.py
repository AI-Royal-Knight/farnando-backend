from __future__ import annotations

from django.db import migrations

TICKET_CATEGORIES = [
    ("ACCOUNT_ACCESS", "Account access", False),
    ("ORDERING", "Ordering", False),
    ("SHIPPING_COLD_CHAIN", "Shipping and cold chain", False),
    ("MEDICATION_GUIDANCE", "Medication guidance", True),
    ("BILLING", "Billing", False),
    ("OTHER", "Other", False),
]


def seed(apps, schema_editor):
    TicketCategory = apps.get_model("catalog", "TicketCategory")
    LabPanel = apps.get_model("catalog", "LabPanel")
    Program = apps.get_model("assessments", "Program")
    for position, (code, label, clinical) in enumerate(TICKET_CATEGORIES):
        TicketCategory.objects.get_or_create(
            code=code,
            defaults={"label": label, "requires_clinical_staff": clinical, "position": position},
        )
    position = 0
    for program in Program.objects.order_by("position", "name"):
        panels = program.lab_panels if isinstance(program.lab_panels, list) else []
        for panel in panels:
            if not isinstance(panel, dict) or not panel.get("code"):
                continue
            _, created = LabPanel.objects.get_or_create(
                code=str(panel["code"]),
                defaults={
                    "name": str(panel.get("name", ""))[:120],
                    "description": str(panel.get("description", ""))[:300],
                    "position": position,
                },
            )
            position += int(created)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_labpanel_platformsettings_ticketcategory_and_more"),
        ("assessments", "0008_evaluation_assigned_physician_evaluation_priority_and_more"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
