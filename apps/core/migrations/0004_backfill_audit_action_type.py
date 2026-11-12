from __future__ import annotations

from django.db import migrations


def backfill(apps, schema_editor):
    from apps.core.audit import action_type_for

    AuditLog = apps.get_model("core", "AuditLog")
    for entry in AuditLog.objects.only("id", "action").iterator():
        AuditLog.objects.filter(pk=entry.pk).update(action_type=action_type_for(entry.action))


class Migration(migrations.Migration):
    dependencies = [("core", "0003_auditlog_action_type_auditlog_changed_fields_and_more")]

    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
