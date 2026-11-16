"""The intake evaluation is no longer paid for: the consultation is.

Programs stop asking for an evaluation fee, and any evaluation still waiting for
that payment moves forward to review with its fee waived. The money is charged
later, when the patient books a consultation.
"""

from __future__ import annotations

from django.db import migrations


def waive(apps, schema_editor):
    Program = apps.get_model("assessments", "Program")
    Evaluation = apps.get_model("assessments", "Evaluation")
    EvaluationFee = apps.get_model("assessments", "EvaluationFee")
    EvaluationStatusHistory = apps.get_model("assessments", "EvaluationStatusHistory")

    Program.objects.update(fee_is_waived=True)
    EvaluationFee.objects.filter(status="PENDING").update(status="WAIVED")

    for evaluation in Evaluation.objects.filter(status="AWAITING_PAYMENT"):
        evaluation.status = "SUBMITTED"
        evaluation.save(update_fields=["status"])
        EvaluationStatusHistory.objects.create(
            evaluation=evaluation,
            from_status="AWAITING_PAYMENT",
            to_status="SUBMITTED",
            reason="Evaluation fee removed (charged at consultation booking)",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0010_payment_consultation_alter_payment_purpose"),
    ]

    operations = [migrations.RunPython(waive, migrations.RunPython.noop)]
