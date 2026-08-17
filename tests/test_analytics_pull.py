from datetime import datetime, timedelta

from src.workers.analytics import due_snapshot_days


def test_no_snapshots_due_for_a_brand_new_video():
    now = datetime(2026, 8, 17)
    uploaded = now - timedelta(hours=2)
    assert due_snapshot_days(uploaded, set(), now) == []


def test_day_one_and_seven_due_at_ten_days_old():
    now = datetime(2026, 8, 17)
    uploaded = now - timedelta(days=10)
    assert due_snapshot_days(uploaded, set(), now) == [1, 7]


def test_already_captured_days_are_not_repeated():
    now = datetime(2026, 8, 17)
    uploaded = now - timedelta(days=10)
    assert due_snapshot_days(uploaded, {1}, now) == [7]
    assert due_snapshot_days(uploaded, {1, 7}, now) == []


def test_missed_run_catches_up_on_day_thirty_without_double_counting():
    now = datetime(2026, 8, 17)
    uploaded = now - timedelta(days=35)
    # A prior run already caught days 1 and 7 — only 30 should still be due,
    # proving a missed beat/cron tick doesn't lose or duplicate a snapshot.
    assert due_snapshot_days(uploaded, {1, 7}, now) == [30]
