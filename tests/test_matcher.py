"""Tests for the highest-risk logic: keyword boundaries, location policy, experience parsing.

Run: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matcher import MatchConfig, Matcher, experience_check, location_ok  # noqa: E402
from src.models import Job  # noqa: E402

CFG = MatchConfig(keywords=["ZIA", "ZPA", "EDR", "UVM", "cyber security", "network security",
                            "security architect", "Zscaler", "Avalor"], max_experience_years=6)


def make(title="Security Engineer", loc="Bengaluru, India", desc="EDR experience. 2-4 years."):
    return Job(company="X", title=title, url="https://x/1", location=loc, description=desc)


# ---- keyword boundaries: short tokens must not match inside other words ----
def test_short_keyword_no_substring_match():
    m = Matcher(CFG)
    j = make(title="Frontend Engineer", desc="Work on media redraw pipelines. 2 years experience.")
    assert m.evaluate(j) is False  # "edr" inside "redraw" must NOT match


def test_short_keyword_real_match():
    m = Matcher(CFG)
    j = make(title="EDR Detection Engineer", desc="Build EDR detections. 1-3 years.")
    assert m.evaluate(j) is True and "EDR" in j.matched_keywords


def test_hyphen_and_space_variants():
    m = Matcher(CFG)
    j = make(title="Network-Security Analyst", desc="network-security monitoring. 0-2 years.")
    assert m.evaluate(j) is True


# ---- location policy ----
def test_india_onsite_ok():
    ok, _ = location_ok(make(loc="Pune, Maharashtra, India"))
    assert ok


def test_remote_global_ok():
    ok, _ = location_ok(make(loc="Remote - Worldwide"))
    assert ok


def test_remote_us_only_rejected():
    ok, _ = location_ok(make(loc="Remote - US only"))
    assert not ok


def test_remote_emea_rejected():
    ok, _ = location_ok(make(loc="Remote (EMEA)"))
    assert not ok


def test_onsite_london_rejected():
    ok, _ = location_ok(make(loc="London, United Kingdom", desc="office based role"))
    assert not ok


def test_remote_india_ok():
    ok, _ = location_ok(make(loc="Remote - India"))
    assert ok


# ---- experience parsing ----
def test_range():
    ok, note = experience_check(make(desc="Requires 3-5 years of experience in SOC."), 6)
    assert ok and "3" in note


def test_plus_over_cap():
    ok, _ = experience_check(make(desc="8+ years of security architecture experience required."), 6)
    assert not ok


def test_minimum_phrase():
    ok, _ = experience_check(make(desc="Minimum of 7 years in network security."), 6)
    assert not ok


def test_takes_smallest_requirement():
    # Core req 2 yrs; a nice-to-have mentions 10 yrs — must keep.
    ok, _ = experience_check(make(desc="2+ years required. Bonus: 10+ years leadership."), 6)
    assert ok


def test_unspecified_kept():
    ok, note = experience_check(make(desc="We want passionate security folks."), 6)
    assert ok and "unspecified" in note


def test_garbage_years_ignored():
    ok, _ = experience_check(make(desc="Linux has 30 years of history. 1-2 years experience needed."), 6)
    assert ok


# ---- full pipeline ----
def test_full_match_pipeline():
    m = Matcher(CFG)
    j = make(title="ZPA Cloud Security Engineer",
             loc="Hyderabad, India",
             desc="<p>Work on <b>ZPA</b> and ZIA. 2 to 4 yrs experience.</p>")
    assert m.evaluate(j) is True
    assert set(j.matched_keywords) >= {"ZPA", "ZIA"}
    assert j.score >= 4  # title hit boosted


def test_no_keywords_dropped():
    m = Matcher(CFG)
    j = make(title="Product Designer", desc="Figma. 2 years experience.")
    assert m.evaluate(j) is False
