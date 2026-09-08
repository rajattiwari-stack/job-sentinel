"""Tests for what discovery is allowed to put in the registry.

Every guess that survives _reject_bad_hits becomes a company the bot scans
four times a day forever, so the interesting cases here are the ones that
look healthy: a board that resolves, returns 200, and serves real JSON —
just not for the company whose name produced the slug, or not for anyone.

Run: python -m pytest tests/test_discover_ats.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.discover_ats import MIN_POSTINGS, _reject_bad_hits  # noqa: E402


def hit(name, ats="smartrecruiters", slug=None, postings=10, how="slug-guess"):
    return {"name": name, "ats": ats, "slug": slug or name.lower(),
            "postings": postings, "how": how, "status": "resolved", "meta": {}}


def test_healthy_board_is_kept():
    keep, drop = _reject_bad_hits([hit("Zscaler", postings=346)])
    assert [r["name"] for r in keep] == ["Zscaler"]
    assert drop == []


def test_empty_board_is_rejected():
    keep, drop = _reject_bad_hits([hit("Ghost", postings=0)])
    assert keep == []
    assert "0 postings" in drop[0]["status"]


def test_trial_account_board_is_rejected():
    """SmartRecruiters is full of abandoned trial accounts answering 200 with
    a single posting called "test" or "job". Seven of them reached the
    registry as live companies because postings > 0 was the whole bar."""
    hits = [hit("Knowlarity", slug="knowlarity1", postings=1),
            hit("Vulcan Cyber", slug="vulcancyberinc", postings=2)]
    keep, drop = _reject_bad_hits(hits)
    assert keep == []
    assert len(drop) == 2
    for r in drop:
        assert "trial account" in r["status"]


def test_board_exactly_at_the_floor_is_kept():
    keep, _ = _reject_bad_hits([hit("Smallco", postings=MIN_POSTINGS)])
    assert [r["name"] for r in keep] == ["Smallco"]


def test_careers_page_hit_survives_the_floor():
    """probe_careers_page reports -1, meaning "found, count unknown". That is
    the only route to a Workday board, so it must not read as "too small"."""
    keep, drop = _reject_bad_hits(
        [hit("Boeing", ats="workday", slug="boeing", postings=-1, how="careers-page")])
    assert [r["name"] for r in keep] == ["Boeing"]
    assert drop == []


def test_slug_collision_rejects_every_claimant():
    """Apollo Hospitals and Apollo Diagnostics both guess `apollo`, which on
    Greenhouse is Apollo GraphQL. At most one could be right and nothing here
    can tell which, so none of them are trustworthy."""
    hits = [hit("Apollo Hospitals", ats="greenhouse", slug="apollo", postings=40),
            hit("Apollo Diagnostics", ats="greenhouse", slug="apollo", postings=40)]
    keep, drop = _reject_bad_hits(hits)
    assert keep == []
    assert len(drop) == 2
    for r in drop:
        assert "collision" in r["status"]
