"""Antivirus scanner port for lab uploads.

The no-op scanner is for local development and tests only. Production must
configure a real scanner (for example a ClamAV adapter) before labs are enabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from django.conf import settings


@dataclass(frozen=True)
class ScanResult:
    clean: bool
    reason: str = ""


class LabScanner(Protocol):
    def scan(self, storage_key: str) -> ScanResult: ...


class NoopLabScanner:
    def scan(self, storage_key: str) -> ScanResult:
        _ = storage_key
        return ScanResult(clean=True)


class ClamAvLabScanner:
    def scan(self, storage_key: str) -> ScanResult:
        _ = storage_key
        # A real adapter is supplied with Phase 2 infrastructure. Failing loudly
        # is safer than silently marking clinical files as clean.
        raise RuntimeError("No ClamAV adapter is configured")


def get_lab_scanner() -> LabScanner:
    path = str(getattr(settings, "LAB_SCANNER", ""))
    if path.endswith("NoopLabScanner"):
        return NoopLabScanner()
    if path.endswith("ClamAvLabScanner"):
        return ClamAvLabScanner()
    from django.utils.module_loading import import_string

    return import_string(path)()  # type: ignore[no-any-return]


__all__ = [
    "ClamAvLabScanner",
    "LabScanner",
    "NoopLabScanner",
    "ScanResult",
    "get_lab_scanner",
]
