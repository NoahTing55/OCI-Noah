from __future__ import annotations

from datetime import datetime, timezone


def parse_oci_datetime(value: str | datetime | None) -> datetime | None:
    """Parse OCI/SQLite ISO timestamps and normalize them to UTC."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def calculate_survival_days(
    registration_time: str | datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """Return a one-based account age.

    The registration instant is day 1. Every completed 24-hour period advances
    the value by one, so exactly 24 hours after registration is day 2.
    """
    started_at = parse_oci_datetime(registration_time)
    if started_at is None:
        return None
    current = parse_oci_datetime(now or datetime.now(timezone.utc))
    if current is None:
        return None
    elapsed_seconds = (current - started_at).total_seconds()
    if elapsed_seconds <= 0:
        return 1
    return int(elapsed_seconds // 86_400) + 1


def earliest_registration_time(values: list[str | datetime | None]) -> str | None:
    """Return the earliest valid subscription start as a UTC ISO timestamp."""
    parsed = [item for item in (parse_oci_datetime(value) for value in values) if item]
    return min(parsed).isoformat() if parsed else None
