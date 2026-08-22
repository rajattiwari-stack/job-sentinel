"""Tests for the stretch digest and follow-up reminders.

Both are rate-limited and both send Telegram messages, so the failure mode
worth guarding is spam: a 4x-daily bot re-sending the same digest every run.

Run: python -m pytest tests/test_digest.py -v
"""
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.digest import (FOLLOW_UP_DAYS, find_follow_ups, format_follow_ups,  # noqa: E402
                        format_stretch, mark_sent, should_send)
from src.matcher import MatchConfig, Matcher  # noqa: E402
from src.models import Job  # noqa: E402


def job(**kw):
    base = dict(company="Acme", title="Security Engineer", url="https://x/1",
                location="Bengaluru, India", description="")
    base.update(kw)
    return Job(**base)


# ---- rate limiting ----
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


# ---- which roles count as a stretch ----
def _matcher(cap=2, stretch=3):
    return Matcher(MatchConfig(keywords=["security engineer"], max_experience_years=cap,
                               stretch_years=stretch))


def test_role_just_past_the_cap_is_a_stretch():
    m = _matcher()
    assert m.evaluate(job(description="5+ years of security experience.")) is False
    assert len(m.stretch) == 1
    assert "needs 5" in m.stretch[0].experience_note


def test_role_far_past_the_cap_is_not_a_stretch():
    m = _matcher()
    assert m.evaluate(job(description="12+ years of security experience.")) is False
    assert m.stretch == []


def test_a_real_match_is_not_also_a_stretch():
    m = _matcher()
    assert m.evaluate(job(description="1-2 years of experience.")) is True
    assert m.stretch == []


def test_rejects_for_other_reasons_are_not_stretches():
    """Only the year count may be the thing that failed."""
    m = _matcher()
    m.evaluate(job(location="Remote - Texas, USA", description="5+ years."))   # wrong region
    m.evaluate(job(title="Product Designer", description="5+ years."))          # wrong role
    assert m.stretch == []


def test_stretch_message_names_the_requirement():
    j = job()
    j.experience_note = "needs 4+ yrs"
    out = format_stretch([j])
    assert "needs 4+ yrs" in out and "Acme" in out
    assert "\n\n\n" not in out          # no doubled blank lines


# ---- follow-ups ----
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


# ---- weekly health report ----
def test_health_report_names_the_problems():
    from src.digest import format_health
    out = format_health(60, {"Foo": "HTTP 404"}, ["Bar", "Baz"], healed=2)
    assert "57/60" in out and "Foo" in out and "Bar" in out and "2 auto-repaired" in out


def test_health_report_when_all_is_well():
    from src.digest import format_health
    out = format_health(60, {}, [])
    assert "60/60" in out and "Nothing to do" in out


def test_health_report_is_weekly_not_per_run():
    meta = {}
    mark_sent(meta, "last_health_report")
    assert not should_send(meta, "last_health_report", every_hours=168)
