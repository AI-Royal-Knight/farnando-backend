"""Part 4 amendments A1/A2: review groups and the weight-loss history question."""

from django.db import migrations

GROUPS = {
    "medical_conditions": "PRIMARY_CONDITIONS",
    "safety_screening": "CONTRAINDICATION_SCREENING",
    "takes_medications": "CURRENT_MEDICATIONS",
    "medication_list": "CURRENT_MEDICATIONS",
}


def apply(apps, schema_editor):
    Question = apps.get_model("assessments", "QuestionnaireQuestion")
    Step = apps.get_model("assessments", "QuestionnaireStep")
    for key, group in GROUPS.items():
        Question.objects.filter(key=key).update(review_group=group)
    for step in Step.objects.filter(key="health-conditions"):
        if Question.objects.filter(step=step, key="weight_loss_history").exists():
            continue
        last = Question.objects.filter(step=step).order_by("-position").first()
        Question.objects.create(
            step=step,
            key="weight_loss_history",
            text="Describe any previous weight-loss attempts or programs",
            type="TEXT",
            required=False,
            max_length=1000,
            position=(last.position + 1) if last else 1,
            review_group="WEIGHT_LOSS_HISTORY",
        )


class Migration(migrations.Migration):
    dependencies = [("assessments", "0006_prescription_signed_document_prescription_start_date_and_more")]

    operations = [migrations.RunPython(apply, migrations.RunPython.noop)]
