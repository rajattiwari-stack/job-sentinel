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


# The common shapes of a US-locked remote posting. None of these contain the
# words "US only", which is why they used to sail through to an India-based
# candidate — the single largest source of noise in the alert feed.
def test_remote_us_state_rejected():
    for loc in ("Remote - Texas, USA", "Remote, Ohio", "Remote - Illinois, USA",
                "Remote - US", "Remote (Anywhere in the US)"):
        ok, why = location_ok(make(loc=loc))
        assert not ok, f"{loc!r} should be region-locked, got {why!r}"


def test_multi_location_including_india_kept():
    # A job open in several US states AND India is still a real India job.
    ok, _ = location_ok(make(loc="Remote - Illinois, USA; Remote - India"))
    assert ok


def test_lowercase_us_in_prose_does_not_lock():
    # "join us" must not read as the United States.
    ok, _ = location_ok(make(title="Security Engineer - come join us",
                             loc="Bengaluru, India"))
    assert ok


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


# ---- 0-2 yr targeting: seniority veto + leaky-minimum tagging ----
JUNIOR = MatchConfig(
    keywords=["EDR", "cloud security", "security engineer"],
    max_experience_years=2,
    exclude_titles=["senior", "sr", "staff", "principal", "lead", "manager",
                    "director", "head", "architect"],
)


def test_senior_title_vetoed_even_without_stated_years():
    # The trap: a senior posting that never states a year count. The experience
    # parser sees nothing to reject, so only the title can catch it.
    m = Matcher(JUNIOR)
    j = make(title="Senior Security Engineer", desc="Work on EDR. Great team.")
    assert m.evaluate(j) is False


def test_excluded_word_needs_word_boundary():
    # "lead" must not fire inside "leadership"; the job is otherwise valid.
    m = Matcher(JUNIOR)
    j = make(title="Security Engineer", desc="EDR work with leadership exposure. 1-2 years.")
    assert m.evaluate(j) is True


def test_junior_title_kept():
    m = Matcher(JUNIOR)
    j = make(title="Cloud Security Engineer", desc="0-2 years of experience. EDR exposure.")
    assert m.evaluate(j) is True


def test_higher_requirement_is_surfaced_in_note():
    # "Take the smallest stated requirement" gets leaky at a 2-yr cap: this job
    # is kept on the strength of its 2-yr line. It is NOT dropped (that would
    # silently lose real matches) but the note must carry the 8-yr ask so a
    # glance at the Telegram alert is enough to judge it.
    ok, note = experience_check(
        make(desc="2+ years of security experience required. 8+ years preferred."), 2)
    assert ok
    assert "8" in note and "verify" in note


def test_clearly_senior_range_still_dropped():
    ok, note = experience_check(make(desc="6-8 years in network security required."), 2)
    assert not ok and "6" in note


def test_india_onsite_outranks_bare_remote():
    m = Matcher(JUNIOR)
    onsite = make(title="Security Engineer", loc="Pune, India", desc="EDR. 1-2 years.")
    remote = make(title="Security Engineer", loc="Remote - Worldwide", desc="EDR. 1-2 years.")
    assert m.evaluate(onsite) and m.evaluate(remote)
    assert onsite.score > remote.score


def test_boilerplate_description_does_not_qualify_a_job():
    """The security-vendor trap, measured on Zscaler's live board.

    Every posting there — Procurement, Employee Relations, Account Executive —
    carries an "About us" blurb naming zero trust / cloud security / SASE. On a
    description-anywhere match all of them scored like security roles.
    """
    m = Matcher(JUNIOR)
    j = Job(company="Zscaler", title="Deputy Manager, Procurement",
            url="https://x/1", location="Bengaluru, India", department="Accounting",
            description="About us: our cloud security platform... 1-2 years experience.")
    assert m.evaluate(j) is False


def test_title_match_still_qualifies():
    m = Matcher(JUNIOR)
    j = Job(company="Zscaler", title="Cloud Security Engineer", url="https://x/1",
            location="Bengaluru, India", department="Engineering",
            description="1-2 years experience.")
    assert m.evaluate(j) is True


def test_department_match_qualifies():
    # Generic title, but the department places the role in security.
    m = Matcher(JUNIOR)
    j = Job(company="Acme", title="Engineer I", url="https://x/1",
            location="Pune, India", department="Cloud Security",
            description="1-2 years experience.")
    assert m.evaluate(j) is True


def test_role_match_can_be_relaxed():
    cfg = MatchConfig(keywords=["cloud security"], max_experience_years=2,
                      require_role_match=False)
    j = Job(company="Acme", title="Analyst", url="https://x/1", location="Pune, India",
            department="Finance", description="We do cloud security. 1 year experience.")
    assert Matcher(cfg).evaluate(j) is True


def test_priority_boost_ranks_high_fit_companies():
    cfg = MatchConfig(keywords=["security engineer"], max_experience_years=2,
                      priority_boost={"high": 6, "medium": 2})
    m = Matcher(cfg)
    hi, lo = make(desc="EDR work. 1-2 years."), make(desc="EDR work. 1-2 years.")
    hi.priority, lo.priority = "high", "unknown"
    assert m.evaluate(hi) and m.evaluate(lo)
    assert hi.score - lo.score == 6
