"""Tests for the highest-risk logic: keyword boundaries, location policy, experience parsing.

Run: python -m pytest tests/ -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.matcher import (MatchConfig, Matcher, build_role_weights,  # noqa: E402
                         experience_check, location_ok)
from src.models import Job  # noqa: E402

CFG = MatchConfig(keywords=["ZIA", "ZPA", "EDR", "UVM", "cyber security", "network security",
                            "security architect", "Zscaler", "Avalor"], max_experience_years=6)


def make(title="Security Engineer", loc="Bengaluru, India", desc="EDR experience. 2-4 years."):
    return Job(company="X", title=title, url="https://x/1", location=loc, description=desc)


def test_short_keyword_no_substring_match():
    m = Matcher(CFG)
    j = make(title="Frontend Engineer", desc="Work on media redraw pipelines. 2 years experience.")
    assert m.evaluate(j) is False


def test_short_keyword_real_match():
    m = Matcher(CFG)
    j = make(title="EDR Detection Engineer", desc="Build EDR detections. 1-3 years.")
    assert m.evaluate(j) is True and "EDR" in j.matched_keywords


def test_three_letter_tokens_need_boundaries():
    """SOC / IAM / EDR / ZIA are short enough to hide inside ordinary words.

    They are matched against the TITLE, where "Social Media Manager" and
    "Biamp Systems Engineer" are entirely plausible postings.
    """
    m = Matcher(MatchConfig(keywords=["SOC", "IAM", "EDR"], max_experience_years=2))
    for title in ("Social Media Manager", "Biamp Systems Engineer", "Redraw Tooling Engineer"):
        j = make(title=title, desc="1-2 years experience.")
        assert m.evaluate(j) is False, f"{title!r} must not match a 3-letter token"
    for title in ("SOC Analyst", "IAM Engineer", "EDR Detection Engineer"):
        j = make(title=title, desc="1-2 years experience.")
        assert m.evaluate(j) is True, f"{title!r} should match"


def test_hyphen_and_space_variants():
    m = Matcher(CFG)
    j = make(title="Network-Security Analyst", desc="network-security monitoring. 0-2 years.")
    assert m.evaluate(j) is True


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


def test_remote_us_state_rejected():
    for loc in ("Remote - Texas, USA", "Remote, Ohio", "Remote - Illinois, USA",
                "Remote - US", "Remote (Anywhere in the US)"):
        ok, why = location_ok(make(loc=loc))
        assert not ok, f"{loc!r} should be region-locked, got {why!r}"


def test_remote_tied_to_a_foreign_city_is_rejected():
    """The blocklist's fatal flaw: it fails OPEN.

    None of these name a country, a US state, or the word "US", so a
    reject-known-bad-regions rule passed every one of them. All were observed
    on live Ashby boards flagged remote, and all are useless from India.
    Enumerating every city on earth is not a strategy — hence the allowlist:
    India, explicitly global, or nothing specific.
    """
    for loc in ("Palo Alto", "Seattle", "San Francisco", "Foster City, CA",
                "Helsinki, Finland", "London"):
        j = make(loc=loc)
        j.remote = True
        ok, why = location_ok(j)
        assert not ok, f"{loc!r} should be rejected, got {why!r}"


def test_remote_with_no_named_place_is_kept():
    """Genuinely unspecified remote stays in — ambiguity shouldn't cost a match."""
    for loc in ("Remote", "", "Remote or Hybrid", "Remote - Multiple Locations"):
        j = make(loc=loc)
        j.remote = True
        ok, why = location_ok(j)
        assert ok, f"{loc!r} should be kept, got {why!r}"


def test_title_does_not_count_as_a_location():
    """The residue check must read the location field alone — a title always
    leaves words behind and would reject every unspecified remote role."""
    j = make(title="Senior Cloud Security Engineer", loc="Remote")
    j.remote = True
    assert location_ok(j)[0]


def test_multi_location_including_india_kept():
    ok, _ = location_ok(make(loc="Remote - Illinois, USA; Remote - India"))
    assert ok


def test_lowercase_us_in_prose_does_not_lock():
    ok, _ = location_ok(make(title="Security Engineer - come join us",
                             loc="Bengaluru, India"))
    assert ok


def test_onsite_london_rejected():
    ok, _ = location_ok(make(loc="London, United Kingdom", desc="office based role"))
    assert not ok


def test_remote_india_ok():
    ok, _ = location_ok(make(loc="Remote - India"))
    assert ok


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
    ok, _ = experience_check(make(desc="2+ years required. Bonus: 10+ years leadership."), 6)
    assert ok


def test_unspecified_kept():
    ok, note = experience_check(make(desc="We want passionate security folks."), 6)
    assert ok and "not stated" in note


def test_garbage_years_ignored():
    ok, _ = experience_check(make(desc="Linux has 30 years of history. 1-2 years experience needed."), 6)
    assert ok


def test_full_match_pipeline():
    m = Matcher(CFG)
    j = make(title="ZPA Cloud Security Engineer",
             loc="Hyderabad, India",
             desc="<p>Work on <b>ZPA</b> and ZIA. 2 to 4 yrs experience.</p>")
    assert m.evaluate(j) is True
    assert set(j.matched_keywords) >= {"ZPA", "ZIA"}
    assert j.score >= 4


def test_no_keywords_dropped():
    m = Matcher(CFG)
    j = make(title="Product Designer", desc="Figma. 2 years experience.")
    assert m.evaluate(j) is False


JUNIOR = MatchConfig(
    keywords=["EDR", "cloud security", "security engineer"],
    max_experience_years=2,
    exclude_titles=["senior", "sr", "staff", "principal", "lead", "manager",
                    "director", "head", "architect"],
)


def test_senior_title_vetoed_even_without_stated_years():
    m = Matcher(JUNIOR)
    j = make(title="Senior Security Engineer", desc="Work on EDR. Great team.")
    assert m.evaluate(j) is False


def test_excluded_word_needs_word_boundary():
    m = Matcher(JUNIOR)
    j = make(title="Security Engineer", desc="EDR work with leadership exposure. 1-2 years.")
    assert m.evaluate(j) is True


def test_junior_title_kept():
    m = Matcher(JUNIOR)
    j = make(title="Cloud Security Engineer", desc="0-2 years of experience. EDR exposure.")
    assert m.evaluate(j) is True


def test_ceiling_rejects_a_senior_role_that_mentions_a_small_number():
    ok, note = experience_check(
        make(desc="2+ years of security experience required. 8+ years preferred."),
        3, reject_above=4)
    assert not ok
    assert "8" in note


def test_band_is_shown_when_a_range_is_stated():
    ok, note = experience_check(make(desc="2 to 4 years of experience."), 3, reject_above=4)
    assert ok and note == "wants 2-4 yrs"


def test_stretch_is_labelled_against_candidate_experience():
    ok, note = experience_check(make(desc="Minimum of 3 years experience."), 3,
                                reject_above=4, candidate_years=2)
    assert ok and "you have 2" in note


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


def test_freshness_reads_every_ats_date_format():
    """Greenhouse/Lever/Ashby give ISO dates; Workday gives prose."""
    from datetime import date, timedelta

    from src.matcher import days_since_posted

    def with_date(raw):
        j = make()
        j.posted_at = raw
        return j

    today = date.today()
    assert days_since_posted(with_date(today.isoformat())) == 0
    assert days_since_posted(with_date((today - timedelta(days=5)).isoformat())) == 5
    assert days_since_posted(with_date("Posted Today")) == 0
    assert days_since_posted(with_date("Posted 3 Days Ago")) == 3
    assert days_since_posted(with_date("Posted 30+ Days Ago")) == 30
    assert days_since_posted(with_date("")) is None
    assert days_since_posted(with_date("sometime last spring")) is None


def test_fresh_posting_outranks_stale_one():
    from datetime import date, timedelta

    m = Matcher(JUNIOR)
    fresh, stale = make(desc="EDR. 1-2 years."), make(desc="EDR. 1-2 years.")
    fresh.posted_at = date.today().isoformat()
    stale.posted_at = (date.today() - timedelta(days=60)).isoformat()
    assert m.evaluate(fresh) and m.evaluate(stale)
    assert fresh.score > stale.score


def test_unknown_age_is_not_penalised():
    """A missing date must not push a good match below a mediocre fresh one."""
    from src.matcher import freshness_boost
    assert freshness_boost(make()) == 0


def test_priority_boost_ranks_high_fit_companies():
    cfg = MatchConfig(keywords=["security engineer"], max_experience_years=2,
                      priority_boost={"high": 6, "medium": 2})
    m = Matcher(cfg)
    hi, lo = make(desc="EDR work. 1-2 years."), make(desc="EDR work. 1-2 years.")
    hi.priority, lo.priority = "high", "unknown"
    assert m.evaluate(hi) and m.evaluate(lo)
    assert hi.score - lo.score == 6


TARGET = MatchConfig(
    keywords=["security engineer", "vulnerability management", "SOC analyst", "cloud security"],
    max_experience_years=3,
    max_posting_age_days=45,
    exclude_titles=["senior", "sr", "staff", "principal", "lead", "manager", "architect",
                    "tac", "technical support", "support engineer", "helpdesk", "l1",
                    "sales", "account executive", "solutions engineer", "multi-lingual"],
)


def test_support_titles_are_vetoed():
    m = Matcher(TARGET)
    for title in ("Technical Support Engineer", "TAC Engineer", "Security Support Engineer",
                  "Helpdesk Analyst", "SOC Analyst L1", "Solutions Engineer - Cloud Security",
                  "Account Executive - Security", "Multi-lingual ES, Security Operations Center"):
        j = make(title=title, desc="cloud security. 1-2 years experience.")
        assert m.evaluate(j) is False, f"{title!r} should be vetoed"


def test_core_security_titles_survive_the_support_veto():
    m = Matcher(TARGET)
    for title in ("Security Engineer", "Vulnerability Management Analyst", "SOC Analyst II",
                  "Cloud Security Engineer"):
        j = make(title=title, desc="1-2 years experience.")
        assert m.evaluate(j) is True, f"{title!r} should pass"


def test_three_year_cap():
    ok, _ = experience_check(make(desc="3+ years of security experience."), 3)
    assert ok
    ok, _ = experience_check(make(desc="4+ years of security experience."), 3)
    assert not ok


def test_stale_postings_are_dropped():
    from datetime import date, timedelta
    m = Matcher(TARGET)
    fresh = make(title="Security Engineer", desc="1-2 years.")
    fresh.posted_at = (date.today() - timedelta(days=10)).isoformat()
    stale = make(title="Security Engineer", desc="1-2 years.")
    stale.posted_at = (date.today() - timedelta(days=200)).isoformat()
    assert m.evaluate(fresh) is True
    assert m.evaluate(stale) is False


def test_undated_posting_is_not_dropped_as_stale():
    m = Matcher(TARGET)
    assert m.evaluate(make(title="Security Engineer", desc="1-2 years.")) is True


def test_amazon_month_name_date_is_parsed():
    from src.matcher import days_since_posted
    from datetime import date
    j = make()
    j.posted_at = "August  4, 2026"
    age = days_since_posted(j)
    assert age is not None and age == (date.today() - date(2026, 8, 4)).days


def test_role_weight_ranks_career_target_highest():
    cfg = MatchConfig(
        keywords=["security engineer", "vulnerability management", "GRC"],
        max_experience_years=3,
        role_weights=build_role_weights([
            {"name": "vuln", "weight": 12, "patterns": ["vulnerability management"]},
            {"name": "grc", "weight": 3, "patterns": ["GRC"]},
        ]),
    )
    m = Matcher(cfg)
    vuln = make(title="Vulnerability Management Analyst", desc="1-2 years.")
    grc = make(title="GRC Analyst", desc="1-2 years.")
    assert m.evaluate(vuln) and m.evaluate(grc)
    assert vuln.score > grc.score
    assert vuln.role_fit == "vuln"


def test_amazon_duplicate_requisitions_collapse():
    from src.models import Job
    a = Job(company="Amazon", title="Security Operations Center Associate",
            url="https://x/1", location="Bengaluru", source_id="111")
    b = Job(company="Amazon", title="Security Operations Center Associate",
            url="https://x/2", location="Bengaluru", source_id="222")
    assert a.fingerprint == b.fingerprint
