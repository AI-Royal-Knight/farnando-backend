from decimal import Decimal

from django.db import migrations

# [OPEN] prices are not in the Figma; these are placeholders for demos.


def seed(apps, schema_editor):
    Program = apps.get_model("assessments", "Program")
    Product = apps.get_model("treatments", "Product")
    Package = apps.get_model("orders", "Package")
    PackageItem = apps.get_model("orders", "PackageItem")
    program = Program.objects.filter(slug="weight-loss").first()
    if program is None:
        return
    product, _ = Product.objects.update_or_create(
        code="semaglutide-0-5",
        defaults={
            "name": "Semaglutide GLP-1 (0.5 mg)",
            "description": (
                "Subcutaneous peptide formula designed for metabolic stabilization "
                "and appetite regulation."
            ),
            "route": "SUBCUTANEOUS",
            "safety_information_markdown": (
                "**[MEDICAL REVIEW]** Safety information and label warnings must be "
                "provided by the prescribing physician before launch."
            ),
        },
    )
    packages = [
        ("Monthly Protocol", "One month of medication and supplies.", 4, "ONE_TIME", None, 0),
        (
            "Custom 3-Month Protocol",
            "Three months of medication, delivered with cold-chain packaging.",
            12,
            "ONE_TIME",
            None,
            1,
        ),
        (
            "Monthly Subscription",
            "Monthly delivery that renews automatically. Pause or cancel anytime.",
            4,
            "RECURRING",
            1,
            2,
        ),
    ]
    prices = {4: Decimal("599.00"), 12: Decimal("1599.00")}
    for name, description, weeks, billing_type, interval, position in packages:
        package, _ = Package.objects.update_or_create(
            program=program,
            name=name,
            defaults={
                "description": description,
                "supply_duration_weeks": weeks,
                "billing_type": billing_type,
                "billing_interval_months": interval,
                "currency": "PEN",
                "position": position,
            },
        )
        if not PackageItem.objects.filter(package=package).exists():
            PackageItem.objects.create(
                package=package,
                product=product,
                description="2mL vial + needles",
                quantity=1,
                unit_price=prices[weeks],
            )


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0002_initial"),
        ("treatments", "0001_initial"),
        ("assessments", "0002_seed_weight_loss"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
