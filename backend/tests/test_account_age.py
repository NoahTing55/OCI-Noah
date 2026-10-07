from datetime import datetime, timedelta, timezone

from app.account_age import (
    calculate_survival_days,
    earliest_registration_time,
    parse_oci_datetime,
)


def test_registration_instant_is_day_one():
    start = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    assert calculate_survival_days(start, now=start) == 1
    assert calculate_survival_days(start, now=start + timedelta(hours=23, minutes=59)) == 1


def test_every_completed_24_hours_advances_one_day():
    start = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    assert calculate_survival_days(start, now=start + timedelta(hours=24)) == 2
    assert calculate_survival_days(start, now=start + timedelta(hours=48)) == 3


def test_iso_offsets_are_normalized_before_calculation():
    start = "2026-07-29T20:00:00+08:00"
    now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    assert calculate_survival_days(start, now=now) == 2
    assert parse_oci_datetime("2026-07-29T12:00:00Z") == datetime(
        2026, 7, 29, 12, 0, tzinfo=timezone.utc
    )


def test_missing_or_invalid_registration_time_is_unknown():
    assert calculate_survival_days(None) is None
    assert calculate_survival_days("") is None
    assert calculate_survival_days("not-a-date") is None


def test_upgraded_account_keeps_earliest_subscription_start():
    assert earliest_registration_time([
        "2026-07-20T00:00:00Z",
        "2025-01-02T08:30:00+08:00",
        None,
    ]) == "2025-01-02T00:30:00+00:00"
