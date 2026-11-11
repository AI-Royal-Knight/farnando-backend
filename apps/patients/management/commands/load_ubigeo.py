"""Load the national INEI ubigeo table from CSV.

Expected header: ``code,name[,is_deliverable]``. Codes are 2 (department),
4 (province) or 6 (district) digits. Parents must appear before children or
already exist. ``is_deliverable`` is only applied to districts.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.patients.models import Ubigeo, UbigeoLevel

LEVELS = {2: UbigeoLevel.DEPARTMENT, 4: UbigeoLevel.PROVINCE, 6: UbigeoLevel.DISTRICT}


class Command(BaseCommand):
    help = "Load INEI ubigeo codes (departments, provinces, districts) from a CSV file."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("csv_path")

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["csv_path"])
        if not path.is_file():
            raise CommandError(f"File not found: {path}")
        created = updated = 0
        with path.open(newline="", encoding="utf-8") as handle, transaction.atomic():
            for line, row in enumerate(csv.DictReader(handle), start=2):
                code = (row.get("code") or "").strip()
                name = (row.get("name") or "").strip()
                level = LEVELS.get(len(code))
                if not code.isdigit() or level is None or not name:
                    raise CommandError(f"Line {line}: invalid code or name.")
                parent = code[: len(code) - 2] or None
                if parent and not Ubigeo.objects.filter(code=parent).exists():
                    raise CommandError(f"Line {line}: parent {parent} does not exist.")
                defaults: dict[str, Any] = {"name": name, "level": level, "parent_id": parent}
                raw = (row.get("is_deliverable") or "").strip().lower()
                if level == UbigeoLevel.DISTRICT and raw:
                    defaults["is_deliverable"] = raw in ("1", "true", "yes")
                _, was_created = Ubigeo.objects.update_or_create(code=code, defaults=defaults)
                created += int(was_created)
                updated += int(not was_created)
        self.stdout.write(f"Ubigeo loaded: {created} created, {updated} updated.")
