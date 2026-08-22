"""Tests for name→board resolution and board identity.

Slug guessing fails in a uniquely dangerous way: it produces a live board full
of real jobs that belongs to a different company. Nothing errors, the postings
look plausible, and the feed quietly fills with someone else's openings. All
of the pairs below were observed on live boards while building the registry.

Run: python -m pytest tests/test_discovery.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.discovery import identity_matches, slug_candidates  # noqa: E402


def test_rejects_impostor_boards():
    impostors = [
        ("CSS Corp (Movate)", "CloudKitchens"),
        ("Carbon Black (VMware)", "Carbon, Inc."),
        ("UKG (Ultimate Kronos Group)", "Ultimate Heating & Air, Inc"),
        ("Pure Storage", "Everpure"),
        ("BCG (Boston Consulting)", "Bohen Consulting Group"),
        ("Nucleus Security", "Nucleus Global"),
    ]
    for company, board in impostors:
        assert not identity_matches(company, board), \
            f"{company!r} must not accept a board owned by {board!r}"


def test_accepts_the_same_company_written_differently():
    same = [
        ("Dropbox India", "Dropbox"),
        ("Palantir Technologies", "Palantir"),
        ("Western Digital India", "Western Digital"),
        ("Alten India", "ALTEN"),
        ("STT GDC India", "STT GDC"),
        ("Rubrik", "Rubrik Job Board"),
        ("Corelight", "Job Board"),
        ("Zscaler", "Zscaler"),
    ]
    for company, board in same:
        assert identity_matches(company, board), \
            f"{company!r} should accept board named {board!r}"


def test_containment_alone_is_not_enough():
    """"Carbon" sits inside "Carbon Black" — substring matching would accept
    Carbon, Inc.'s board as Carbon Black's."""
    assert not identity_matches("Carbon Black", "Carbon")


def test_unknown_board_name_is_not_held_against_a_company():
    assert identity_matches("Anything", "")


def test_slug_candidates_try_the_obvious_spellings():
    cands = slug_candidates("Palo Alto Networks")
    assert "paloaltonetworks" in cands
    assert "palo-alto-networks" in cands


def test_slug_candidates_strip_corporate_suffixes():
    assert "dropbox" in slug_candidates("Dropbox India Pvt Ltd")


def test_slug_candidates_consider_a_parenthetical():
    cands = slug_candidates("Splunk (Cisco)")
    assert "splunk" in cands
    assert any("cisco" in c for c in cands)


def test_slug_candidates_are_deduped_and_bounded():
    cands = slug_candidates("Acme Acme Technologies Inc")
    assert len(cands) == len(set(cands))
    assert len(cands) <= 4
