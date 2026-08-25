"""Small shared helpers: boolean and datetime parsing for the workbook data."""

from __future__ import annotations

from datetime import datetime

from . import config


def to_bool(value) -> bool:
    """Normalise workbook truthiness. openpyxl may give '1'/'0', 'True'/'False', etc."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    return s in {"1", "true", "yes", "y"}


def parse_dt(value: str | None) -> datetime | None:
    """Parse a workbook timestamp like '2026-08-16 09:00' as IST-aware datetime."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=config.IST)
        except ValueError:
            continue
    return None


def minutes_between(later: datetime | None, earlier: datetime | None) -> float | None:
    """Whole minutes from `earlier` to `later` (may be negative). None if missing."""
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 60.0, 1)
