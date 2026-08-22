"""Self-healing: repair company entries whose board stopped working.

THE FAILURE THIS EXISTS FOR
---------------------------
Companies migrate ATS. When they do, the old endpoint rarely errors — it just
stops having jobs. Zscaler and Palo Alto Networks, the two most relevant
employers on this list, both sat at zero postings for an unknown number of
runs: SmartRecruiters answers HTTP 200 with `{"totalFound": 0}` for a slug it
has never heard of, so nothing threw, nothing alerted, and the log line read
"0 postings" exactly like a company with no openings.

A scraper that can't notice this is a scraper you can't trust to run unattended.

HOW IT HEALS
------------
After a scan, any company that hard-failed or returned zero postings is a
suspect. The healer re-runs discovery for it — slug guesses across all four
ATS APIs, then its careers page, which is the only route to a Workday board —
and if it finds a live board it rewrites that entry in companies.yaml. The
workflow commits the file, so the fix persists to the next run.

DELIBERATE LIMITS
-----------------
- A company with genuinely zero open roles looks identical to a migrated one.
  Healing only ever REPLACES a dead board with a live one; it never disables a
  company, so a quiet employer is left alone.
- MAX_HEAL_ATTEMPTS bounds the work: a systemic outage (network down, every
  company at zero) must not turn into hundreds of probes.
- Every change is reported to Telegram. Silent self-modification would be a
  worse failure mode than the bug it fixes.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .discovery import find_board
from .models import Company

log = logging.getLogger("healer")

MAX_HEAL_ATTEMPTS = 12


def _same_board(company: Company, found: dict) -> bool:
    if found["ats"] != company.ats:
        return False
    if found["ats"] == "workday":
        return (found.get("workday_host", "").lower() == (company.workday_host or "").lower()
                and found.get("workday_path", "") == (company.workday_path or ""))
    return str(found.get("slug", "")).lower() == str(company.slug).lower()


GIVE_UP_AFTER = 3


def diagnose(companies: list[Company], counts: dict[str, int],
             failures: dict[str, str], attempts: dict[str, int] | None = None) -> list[Company]:
    """Companies worth re-probing: hard failures first, then silent zeros.

    Some boards simply cannot be re-resolved — Tenable, Akamai, Fortinet and
    Splunk are on Workday under board paths that aren't derivable from anything
    we hold, and none of them appear on another public ATS. Without a memory of
    what's already been tried, those four would consume the probe budget every
    single run and a board that broke *today* would never get looked at. So a
    company that has failed to resolve GIVE_UP_AFTER times is set aside until
    the counter is cleared (weekly), and the budget goes to fresher breakage.
    """
    attempts = attempts or {}
    ordered = [c for c in companies if c.name in failures]
    ordered += [c for c in companies
                if c.name not in failures and counts.get(c.name, 0) == 0]
    fresh = [c for c in ordered if attempts.get(c.name, 0) < GIVE_UP_AFTER]
    skipped = len(ordered) - len(fresh)
    if skipped:
        log.info("Self-healing: %d compan(ies) set aside after %d failed repairs.",
                 skipped, GIVE_UP_AFTER)
    return fresh[:MAX_HEAL_ATTEMPTS]


def record_attempts(attempts: dict[str, int], probed: list[Company],
                    fixes: list[dict]) -> None:
    """Count a miss against a company; clear its counter the moment it's fixed."""
    fixed = {f["name"] for f in fixes}
    for c in probed:
        if c.name in fixed:
            attempts.pop(c.name, None)
        else:
            attempts[c.name] = attempts.get(c.name, 0) + 1


def heal(suspects: list[Company]) -> list[dict]:
    """Re-resolve each suspect. Returns the fixes that were actually found."""
    fixes: list[dict] = []
    for c in suspects:
        try:
            found = find_board(c.name)
        except Exception as e:               # noqa: BLE001 — healing never breaks a run
            log.warning("%s: heal probe failed: %s", c.name, e)
            continue
        if not found or _same_board(c, found):
            continue
        log.warning("HEALED %s: %s/%s -> %s/%s (%s postings)", c.name, c.ats, c.slug,
                    found["ats"], found.get("slug"), found.get("postings"))
        fixes.append({"name": c.name, "old": f"{c.ats}/{c.slug}", **found})
    return fixes


def apply_fixes(config_path: Path, fixes: list[dict]) -> int:
    """Rewrite the healed entries in companies.yaml, preserving the file's shape.

    Edits the YAML as text rather than round-tripping through the parser: a
    reserialize would strip every comment in the file, and the comments are how
    the registry explains itself.
    """
    if not fixes:
        return 0
    lines = config_path.read_text("utf-8").splitlines()
    by_name = {f["name"]: f for f in fixes}
    applied = 0

    entry_start = re.compile(r"^([ \t]*)-[ \t]+name:[ \t]*[\"']?(.+?)[\"']?[ \t]*$")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = entry_start.match(lines[i])
        fix = by_name.get(m.group(2)) if m else None
        if not fix:
            out.append(lines[i])
            i += 1
            continue

        indent = m.group(1)
        header, i = lines[i], i + 1
        priority, kept_comments = "unknown", []
        pad = indent + "  "
        first_prop = True
        while i < len(lines):
            nxt = lines[i]
            if not nxt.strip():
                break
            if entry_start.match(nxt) or not nxt.startswith(indent + " "):
                break
            if first_prop and not nxt.strip().startswith("#"):
                pad = nxt[: len(nxt) - len(nxt.lstrip())]
                first_prop = False
            pm = re.match(r"^[ \t]*priority:[ \t]*(\S+)", nxt)
            if pm:
                priority = pm.group(1)
            elif nxt.strip().startswith("#") and "auto-healed" not in nxt:
                kept_comments.append(nxt)
            i += 1

        out.append(header)
        out.extend(kept_comments)
        out.append(f"{pad}ats: {fix['ats']}")
        out.append(f"{pad}slug: {fix.get('slug', '')}")
        if fix["ats"] == "workday":
            out.append(f"{pad}workday_host: {fix.get('workday_host', '')}")
            out.append(f"{pad}workday_path: {fix.get('workday_path', '')}")
        out.append(f"{pad}priority: {priority}")
        out.append(f"{pad}# auto-healed: was {fix['old']}")
        applied += 1

    if applied:
        _write_atomic(config_path, "\n".join(out) + "\n")
        log.warning("Applied %d self-healing fix(es) to %s", applied, config_path)
    return applied


def _write_atomic(path: Path, text: str) -> None:
    """Never leave a half-written registry behind — a truncated companies.yaml
    would fail the next run before it fetched anything."""
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, "utf-8")
    tmp.replace(path)


def format_report(fixes: list[dict]) -> str:
    lines = ["🔧 <b>Self-healing</b> — board(s) repaired:"]
    for f in fixes:
        lines.append(f"• <b>{f['name']}</b>: {f['old']} → {f['ats']}/{f.get('slug')} "
                     f"({f.get('postings')} postings, via {f.get('how')})")
    lines.append("\nconfig/companies.yaml was updated and committed.")
    return "\n".join(lines)
