"""Tests for self-healing.

The healer REWRITES config/companies.yaml in place. A bug here doesn't just
fail to fix things — it can corrupt the registry the whole system reads. The
first version of apply_fixes did exactly that: its block regex consumed the
newline separating two entries, welding the next company onto the healed one,
so the file still parsed but silently became a different company. Every test
below reparses the result and checks the untouched entries survived.

Run: python -m pytest tests/test_healer.py -v
"""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.healer import _same_board, apply_fixes, diagnose  # noqa: E402
from src.models import Company  # noqa: E402

REGISTRY = """\
# Company registry.
companies:

  # ---- priority: high ----
  # Zscaler moved off SmartRecruiters.
  - name: "Zscaler"
    ats: smartrecruiters
    slug: "Zscaler"
    priority: high
  - name: "CrowdStrike"
    ats: workday
    slug: "crowdstrike"
    workday_host: crowdstrike.wd5.myworkdayjobs.com
    workday_path: crowdstrikecareers
    priority: high

  # ---- priority: medium ----
  - name: "Cloudflare"
    ats: greenhouse
    slug: "cloudflare"
    priority: medium
    enabled: true
"""


def _load(p: Path) -> dict[str, dict]:
    data = yaml.safe_load(p.read_text("utf-8"))
    return {c["name"]: c for c in data["companies"]}


def test_heal_rewrites_only_the_target(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(REGISTRY, "utf-8")

    applied = apply_fixes(p, [{"name": "Zscaler", "old": "smartrecruiters/Zscaler",
                               "ats": "greenhouse", "slug": "zscaler",
                               "postings": 346, "how": "slug-guess"}])
    assert applied == 1

    got = _load(p)
    assert got["Zscaler"]["ats"] == "greenhouse"
    assert got["Zscaler"]["slug"] == "zscaler"
    assert got["Zscaler"]["priority"] == "high"
    assert len(got) == 3
    assert got["Cloudflare"]["slug"] == "cloudflare"
    assert got["Cloudflare"]["ats"] == "greenhouse"
    assert got["CrowdStrike"]["workday_path"] == "crowdstrikecareers"


def test_heal_to_workday_writes_host_and_path(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(REGISTRY, "utf-8")

    apply_fixes(p, [{"name": "Zscaler", "old": "smartrecruiters/Zscaler",
                     "ats": "workday", "slug": "zscaler",
                     "workday_host": "zscaler.wd5.myworkdayjobs.com",
                     "workday_path": "zscalercareers", "postings": 12,
                     "how": "careers-page"}])
    got = _load(p)
    assert got["Zscaler"]["workday_host"] == "zscaler.wd5.myworkdayjobs.com"
    assert got["Zscaler"]["workday_path"] == "zscalercareers"
    assert len(got) == 3


def test_heal_leaves_a_breadcrumb(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(REGISTRY, "utf-8")
    apply_fixes(p, [{"name": "Zscaler", "old": "smartrecruiters/Zscaler",
                     "ats": "greenhouse", "slug": "zscaler", "postings": 1,
                     "how": "slug-guess"}])
    assert "auto-healed: was smartrecruiters/Zscaler" in p.read_text("utf-8")


def test_no_fixes_leaves_file_byte_identical(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(REGISTRY, "utf-8")
    assert apply_fixes(p, []) == 0
    assert p.read_text("utf-8") == REGISTRY


def test_unknown_company_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "companies.yaml"
    p.write_text(REGISTRY, "utf-8")
    applied = apply_fixes(p, [{"name": "NotInFile", "old": "x/y",
                               "ats": "greenhouse", "slug": "z", "postings": 1,
                               "how": "slug-guess"}])
    assert applied == 0
    assert len(_load(p)) == 3


def test_zero_postings_is_a_suspect():
    live = Company(name="Live", ats="greenhouse", slug="live")
    dead = Company(name="Dead", ats="smartrecruiters", slug="dead")
    suspects = diagnose([live, dead], {"Live": 340, "Dead": 0}, {})
    assert [c.name for c in suspects] == ["Dead"]


def test_hard_failure_is_a_suspect():
    broken = Company(name="Broken", ats="greenhouse", slug="broken")
    suspects = diagnose([broken], {"Broken": 0}, {"Broken": "HTTP 404"})
    assert [c.name for c in suspects] == ["Broken"]


def test_healthy_company_is_never_probed():
    live = Company(name="Live", ats="greenhouse", slug="live")
    assert diagnose([live], {"Live": 12}, {}) == []


def test_suspect_list_is_bounded():
    many = [Company(name=f"C{i}", ats="greenhouse", slug=f"c{i}") for i in range(200)]
    assert len(diagnose(many, {c.name: 0 for c in many}, {})) <= 12


def test_same_board_is_not_a_fix():
    c = Company(name="X", ats="greenhouse", slug="acme")
    assert _same_board(c, {"ats": "greenhouse", "slug": "ACME"})
    assert not _same_board(c, {"ats": "lever", "slug": "acme"})


def test_same_board_compares_workday_host_and_path():
    c = Company(name="X", ats="workday", slug="x",
                workday_host="x.wd5.myworkdayjobs.com", workday_path="ext")
    assert _same_board(c, {"ats": "workday", "workday_host": "X.wd5.myworkdayjobs.com",
                           "workday_path": "ext"})
    assert not _same_board(c, {"ats": "workday", "workday_host": "x.wd5.myworkdayjobs.com",
                               "workday_path": "different"})


def test_repeatedly_unfixable_company_is_set_aside():
    """Tenable/Akamai/Fortinet/Splunk can't be re-resolved from anything we
    hold. Without a memory they'd eat the budget every run forever, and a
    board that broke today would never be looked at."""
    from src.healer import GIVE_UP_AFTER
    stuck = Company(name="Stuck", ats="workday", slug="stuck")
    fresh = Company(name="Fresh", ats="greenhouse", slug="fresh")
    attempts = {"Stuck": GIVE_UP_AFTER}
    picked = diagnose([stuck, fresh], {"Stuck": 0, "Fresh": 0}, {}, attempts)
    assert [c.name for c in picked] == ["Fresh"]


def test_attempts_accumulate_then_clear_on_success():
    from src.healer import record_attempts
    a, c = {}, Company(name="X", ats="greenhouse", slug="x")
    record_attempts(a, [c], [])
    record_attempts(a, [c], [])
    assert a["X"] == 2
    record_attempts(a, [c], [{"name": "X"}])
    assert "X" not in a


def test_no_attempt_memory_behaves_as_before():
    c = Company(name="X", ats="greenhouse", slug="x")
    assert [x.name for x in diagnose([c], {"X": 0}, {})] == ["X"]
