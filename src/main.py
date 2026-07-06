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
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from .adapters import fetch_jobs
from .matcher import MatchConfig, Matcher
from .models import Company, Job
from .notifier import notify
from .state import SeenStore
from .tracker import update_tracker

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = ROOT / "state" / "seen_jobs.json"
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
        )
        if comp.enabled:
            out.append(comp)
    return out


def load_settings(profile_override: str | None) -> tuple[MatchConfig, int]:
    raw = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text("utf-8"))
    profile_name = profile_override or raw["active_profile"]
    prof = raw["profiles"][profile_name]
    cfg = MatchConfig(
        keywords=prof["keywords"],
        max_experience_years=int(prof.get("max_experience_years", 6)),
    )
    cap = int((raw.get("report") or {}).get("max_jobs_per_run", 60))
    log.info("Profile: %s | %d keywords | ≤%d yrs experience",
             profile_name, len(cfg.keywords), cfg.max_experience_years)
    return cfg, cap


def scan_company(company: Company, matcher: Matcher) -> tuple[str, list[Job], str]:
    """Returns (company, matched_jobs, error). Never raises."""
    try:
        jobs = fetch_jobs(company)
        matched = [j for j in jobs if j.title and j.url and matcher.evaluate(j)]
        log.info("%-22s %4d postings → %d matches", company.name, len(jobs), len(matched))
        return company.name, matched, ""
    except Exception as e:  # noqa: BLE001 — isolation by design
        log.error("%-22s FAILED: %s", company.name, e)
        return company.name, [], str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print matches, no notify, no state write")
    ap.add_argument("--profile", default=None, help="Override active_profile from settings.yaml")
    args = ap.parse_args()

    companies = load_companies()
    match_cfg, cap = load_settings(args.profile)
    matcher = Matcher(match_cfg)
    store = SeenStore(STATE_FILE)

    all_matches: list[Job] = []
    failures: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = [pool.submit(scan_company, c, matcher) for c in companies]
        for fut in as_completed(futs):
            name, matched, err = fut.result()
            if err:
                failures[name] = err
            all_matches.extend(matched)

    # Dedup within the run (same job can surface via multiple Workday searches / offices)
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

    # Cumulative archive (no action needed from you) — also a 2nd dedup layer.
    added = update_tracker(TRACKER_FILE, new_jobs)
    log.info("Archive tracker: %d row(s) added.", added)

    if new_jobs:
        delivered = notify(new_jobs)          # instant text alerts with links
        # Compiled Excel of THIS run's jobs → sent as a file into Telegram
        from datetime import datetime
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
            run_xlsx.unlink(missing_ok=True)   # ephemeral: lives in Telegram, not the repo

        if delivered:
            for j in new_jobs:
                store.mark(j.fingerprint)
        else:
            log.error("All notification channels failed — jobs NOT marked, will retry next run.")
    else:
        log.info("No new jobs this run.")

    try:
        from .report import write_dashboard
        write_dashboard(TRACKER_FILE, ROOT / "docs" / "index.html")
    except Exception as e:  # noqa: BLE001 — dashboard is cosmetic, never fatal
        log.warning("Dashboard generation failed: %s", e)

    # deferred jobs intentionally not marked → they flow out next run
    _ = deferred
    store.save()

    # Systemic failure signal for CI alerting
    if companies and len(failures) > len(companies) / 2:
        log.error("More than half of companies failed: %s", list(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
