from django.db import migrations

# [MEDICAL REVIEW] Placeholder guardrails; a physician must approve them before launch.
DOSES = ["0.25", "0.5", "1.0", "1.7", "2.4"]


def seed(apps, schema_editor):
    Product = apps.get_model("treatments", "Product")
    Program = apps.get_model("assessments", "Program")
    product = Product.objects.filter(code="semaglutide-0-5").first()
    if product is None:
        return
    product.active_ingredient = "Semaglutide"
    product.form = "Vial"
    product.allowed_doses = [
        {"value": float(dose), "unit": "mg", "label": f"{float(dose):g} mg"} for dose in DOSES
    ]
    product.starting_doses = [{"value": 0.25, "unit": "mg", "label": "0.25 mg"}]
    product.default_frequency = "WEEKLY"
    product.max_refills = 5
    product.save()
    program = Program.objects.filter(slug="weight-loss").first()
    if program is not None:
        product.programs.add(program)


class Migration(migrations.Migration):
    dependencies = [
        ("treatments", "0002_product_active_ingredient_product_allowed_doses_and_more"),
        ("orders", "0003_seed_weight_loss_package"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
