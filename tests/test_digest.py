"""Tests for follow-up reminders and their rate limiting."""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.digest import (FOLLOW_UP_DAYS, find_follow_ups, format_follow_ups,  # noqa: E402
                        mark_sent, should_send)
from src.models import Job  # noqa: E402


def job(**kw):
    base = dict(company="Acme", title="Security Engineer", url="https://x/1",
                location="Bengaluru, India", description="")
    base.update(kw)
    return Job(**base)


def test_first_send_is_allowed():
    assert should_send({}, "k")


def test_second_send_is_suppressed():
    meta = {}
    mark_sent(meta, "k")
    assert not should_send(meta, "k", every_hours=20)


def test_send_allowed_again_after_the_window():
    meta = {"k": (datetime.now() - timedelta(hours=21)).isoformat(timespec="seconds")}
    assert should_send(meta, "k", every_hours=20)


def test_corrupt_timestamp_does_not_block_forever():
    assert should_send({"k": "not-a-timestamp"}, "k")


def _tracker(tmp_path, rows):
    from src.tracker import _write
    p = tmp_path / "tracker.xlsx"
    _write(p, rows)
    return p


def test_stale_application_is_flagged(tmp_path):
    old = (date.today() - timedelta(days=FOLLOW_UP_DAYS + 3)).isoformat()
    p = _tracker(tmp_path, [{"Job ID": "a", "Company": "Zscaler", "Position": "Analyst",
                             "Applied?": "Yes", "Applied Date": old, "Link": "https://x/1"}])
    due = find_follow_ups(p)
    assert len(due) == 1 and due[0]["age_days"] >= FOLLOW_UP_DAYS


def test_recent_application_is_left_alone(tmp_path):
    recent = (date.today() - timedelta(days=2)).isoformat()
    p = _tracker(tmp_path, [{"Job ID": "a", "Company": "Zscaler", "Position": "Analyst",
                             "Applied?": "Yes", "Applied Date": recent, "Link": ""}])
    assert find_follow_ups(p) == []


def test_unapplied_rows_are_never_chased(tmp_path):
    old = (date.today() - timedelta(days=90)).isoformat()
    p = _tracker(tmp_path, [{"Job ID": "a", "Company": "Zscaler", "Position": "Analyst",
                             "Applied?": "No", "Applied Date": old, "Link": ""}])
    assert find_follow_ups(p) == []


def test_hand_typed_date_is_skipped_not_fatal(tmp_path):
    p = _tracker(tmp_path, [{"Job ID": "a", "Company": "Z", "Position": "P",
                             "Applied?": "Yes", "Applied Date": "last tuesday", "Link": ""}])
    assert find_follow_ups(p) == []


def test_missing_tracker_is_not_fatal(tmp_path):
    assert find_follow_ups(tmp_path / "nope.xlsx") == []


def test_follow_up_message_shape():
    out = format_follow_ups([{"Company": "Zscaler", "Position": "Analyst",
                              "Link": "https://x/1", "Applied Date": "2026-08-01",
                              "age_days": 18}])
    assert "18 days ago" in out and "Zscaler" in out
    assert "\n\n\n" not in out
