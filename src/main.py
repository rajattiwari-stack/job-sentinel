"""Job Sentinel orchestrator.

Run: python -m src.main [--dry-run] [--profile NAME]

Production properties:
- Per-company isolation: one broken company/slug can NEVER kill the run;
  it's logged and reported in the run summary instead.
- Bounded parallelism (ThreadPool) — the run finishes in minutes, not an hour.
- At-least-once delivery: jobs are marked "seen" ONLY after notification
  succeeds, so a Telegram outage means a retry next run, never a lost job.
- Non-zero exit only on systemic failure (>50% companies failed), so GitHub
  Actions alerts you when something is truly broken, not on one flaky API.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import yaml

from .adapters import fetch_jobs
from .matcher import MatchConfig, Matcher, build_role_weights
from .models import Company, Job
from .notifier import notify
from .state import RunMeta, SeenStore
from .tracker import update_tracker

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "seen_jobs.json"
RUN_META_FILE = ROOT / "state" / "run_meta.json"
TRACKER_FILE = ROOT / "tracker.xlsx"
MAX_WORKERS = 6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def load_companies() -> list[Company]:
    raw = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text("utf-8"))
    out = []
    for c in raw.get("companies", []):
        comp = Company(
            name=c["name"], ats=c["ats"], slug=c["slug"],
            workday_host=c.get("workday_host"), workday_path=c.get("workday_path"),
            enabled=c.get("enabled", True),
            priority=c.get("priority", "unknown"),
        )
        if comp.enabled:
            out.append(comp)
    return out


def select_shard(companies: list[Company], shards: int, index: int) -> list[Company]:
    """Split the roster so one run scans one slice of it.

    A few dozen companies fit comfortably in a single run; several hundred do
    not, and a run killed by the Actions timeout delivers nothing at all.
    Slicing by a stable hash (not list position) keeps a company in the same
    shard as the roster grows, so edits to companies.yaml don't reshuffle
    everything and cause duplicate-looking gaps in coverage.
    """
    if shards <= 1:
        return companies
    picked = [
        c for c in companies
        if int(hashlib.sha256(c.name.encode("utf-8")).hexdigest(), 16) % shards == index
    ]
    log.info("Shard %d/%d — scanning %d of %d companies this run.",
             index + 1, shards, len(picked), len(companies))
    return picked


def load_settings(profile_override: str | None) -> tuple[MatchConfig, int, dict]:
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text("utf-8"))
    profile_name = profile_override or raw["active_profile"]
    prof = raw["profiles"][profile_name]
    cfg = MatchConfig(
        keywords=prof["keywords"],
        max_experience_years=int(prof.get("max_experience_years", 6)),
        exclude_titles=prof.get("exclude_titles") or [],
        priority_boost=prof.get("priority_boost") or {},
        require_role_match=bool(prof.get("require_role_match", True)),
        max_posting_age_days=int(prof.get("max_posting_age_days", 0)),
        role_weights=build_role_weights(prof.get("role_weights") or []),
    )
    cap = int((raw.get("report") or {}).get("max_jobs_per_run", 60))
    scan = raw.get("scan") or {}
    log.info("Profile: %s | %d keywords | ≤%d yrs experience | %d excluded titles",
             profile_name, len(cfg.keywords), cfg.max_experience_years,
             len(cfg.exclude_titles))
    return cfg, cap, scan


def scan_company(company: Company, matcher: Matcher) -> tuple[str, list[Job], str, int]:
    """Returns (company, matched_jobs, error, postings_seen). Never raises."""
    try:
        jobs = fetch_jobs(company)
        matched = []
        for j in jobs:
            if not (j.title and j.url):
                continue
            j.priority = company.priority
            if matcher.evaluate(j):
                matched.append(j)
        log.info("%-22s %4d postings → %d matches", company.name, len(jobs), len(matched))
        return company.name, matched, "", len(jobs)
    except Exception as e:  # noqa: BLE001 — isolation by design
        log.error("%-22s FAILED: %s", company.name, e)
        return company.name, [], str(e), 0


def _send_extras(meta: RunMeta) -> None:
    from .digest import find_follow_ups, format_follow_ups, mark_sent, should_send
    from .notifier import send_telegram_text

    if should_send(meta.data, "last_follow_up", every_hours=24):
        due = find_follow_ups(TRACKER_FILE)
        if due:
            log.info("Follow-up reminder: %d application(s) gone quiet.", len(due))
            if send_telegram_text(format_follow_ups(due)):
                mark_sent(meta.data, "last_follow_up")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print matches, no notify, no state write")
    ap.add_argument("--profile", default=None, help="Override active_profile from settings.yaml")
    ap.add_argument("--shard", type=int, default=None,
                    help="Which shard to scan (0-based). Default: the next one in rotation.")
    ap.add_argument("--limit", type=int, default=0, help="Scan only the first N companies (debugging)")
    args = ap.parse_args()

    companies = load_companies()
    match_cfg, cap, scan_cfg = load_settings(args.profile)

    shards = max(1, int(scan_cfg.get("shards", 1)))
    meta = RunMeta(RUN_META_FILE)
    shard_index = args.shard if args.shard is not None else meta.current_shard(shards)
    companies = select_shard(companies, shards, shard_index)
    if args.limit:
        companies = companies[: args.limit]

    matcher = Matcher(match_cfg)
    store = SeenStore(STATE_FILE)
    workers = max(1, int(scan_cfg.get("max_workers", MAX_WORKERS)))

    all_matches: list[Job] = []
    failures: dict[str, str] = {}
    postings_seen: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(scan_company, c, matcher) for c in companies]
        for fut in as_completed(futs):
            name, matched, err, seen = fut.result()
            if err:
                failures[name] = err
            postings_seen[name] = seen
            all_matches.extend(matched)

    uniq: dict[str, Job] = {}
    for j in all_matches:
        uniq.setdefault(j.fingerprint, j)

    new_jobs = [j for j in uniq.values() if store.is_new(j.fingerprint)]
    new_jobs.sort(key=lambda j: (-j.score, j.company, j.title))
    if len(new_jobs) > cap:
        log.warning("Capping notification at %d of %d new jobs (rest go out next run).", cap, len(new_jobs))
        deferred, new_jobs = new_jobs[cap:], new_jobs[:cap]
    else:
        deferred = []

    log.info("Companies: %d ok / %d failed | matches: %d | NEW: %d",
             len(companies) - len(failures), len(failures), len(uniq), len(new_jobs))

    if args.dry_run:
        for j in new_jobs:
            print(f"[{j.score:>2}] {j.company} | {j.title} | {j.location} | "
                  f"{j.experience_note} | {','.join(j.matched_keywords)} | {j.url}")
        return 0

    added = update_tracker(TRACKER_FILE, new_jobs)
    log.info("Archive tracker: %d row(s) added.", added)

    if new_jobs:
        delivered = notify(new_jobs)
        from .notifier import send_telegram_excel
        from .tracker import build_run_workbook
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        run_xlsx = ROOT / "state" / f"new_jobs_{stamp}.xlsx"
        try:
            build_run_workbook(run_xlsx, new_jobs)
            send_telegram_excel(
                str(run_xlsx),
                f"📊 {len(new_jobs)} new cybersecurity jobs — {stamp.replace('_', ' ')} IST run",
            )
        except Exception as e:  # noqa: BLE001 — Excel is a bonus, never blocks alerts
            log.error("Run-Excel build/send failed: %s", e)
        finally:
            run_xlsx.unlink(missing_ok=True)

        if delivered:
            for j in new_jobs:
                store.mark(j.fingerprint)
        else:
            log.error("All notification channels failed — jobs NOT marked, will retry next run.")
    else:
        log.info("No new jobs this run.")

    try:
        _send_extras(meta)
    except Exception as e:  # noqa: BLE001
        log.warning("Follow-up pass failed: %s", e)

    try:
        from .healer import (apply_fixes, diagnose, format_report, heal,
                             record_attempts)
        from .notifier import send_telegram_text
        attempts = meta.data.setdefault("heal_attempts", {})
        suspects = diagnose(companies, postings_seen, failures, attempts)
        if suspects:
            log.info("Self-healing: re-probing %d silent/failed compan(ies).", len(suspects))
            fixes = heal(suspects)
            record_attempts(attempts, suspects, fixes)
            if apply_fixes(ROOT / "config" / "companies.yaml", fixes):
                send_telegram_text(format_report(fixes))
    except Exception as e:  # noqa: BLE001 — healing must never break delivery
        log.warning("Self-healing pass failed: %s", e)

    try:
        from .report import write_dashboard
        write_dashboard(TRACKER_FILE, ROOT / "docs" / "index.html")
    except Exception as e:  # noqa: BLE001 — dashboard is cosmetic, never fatal
        log.warning("Dashboard generation failed: %s", e)

    _ = deferred
    store.save()
    meta.advance(shards)
    meta.save()

    if companies and len(failures) > len(companies) / 2:
        log.error("More than half of companies failed: %s", list(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
