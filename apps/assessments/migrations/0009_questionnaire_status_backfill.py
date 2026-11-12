from __future__ import annotations

from django.db import migrations


def backfill(apps, schema_editor):
    QuestionnaireVersion = apps.get_model("assessments", "QuestionnaireVersion")
    for questionnaire in QuestionnaireVersion.objects.select_related("program"):
        questionnaire.status = "PUBLISHED" if questionnaire.is_published else "ARCHIVED"
        if not questionnaire.title:
            questionnaire.title = f"{questionnaire.program.name} assessment"[:120]
        questionnaire.save(update_fields=["status", "title"])


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0008_evaluation_assigned_physician_evaluation_priority_and_more"),
    ]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
