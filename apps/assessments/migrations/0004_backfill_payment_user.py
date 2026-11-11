from django.db import migrations


def backfill(apps, schema_editor):
    Payment = apps.get_model("assessments", "Payment")
    for payment in Payment.objects.filter(user__isnull=True, evaluation__isnull=False).select_related(
        "evaluation__assessment"
    ):
        payment.user_id = payment.evaluation.assessment.user_id
        payment.purpose = "EVALUATION_FEE"
        payment.save(update_fields=["user", "purpose"])


class Migration(migrations.Migration):
    dependencies = [("assessments", "0003_part3_payment_purpose")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
