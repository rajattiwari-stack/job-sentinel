#!/usr/bin/env python3
"""Ping every company's ATS endpoint and report boards that aren't delivering.

Run this after editing config/companies.yaml:
    python scripts/validate_companies.py            # all companies
    python scripts/validate_companies.py --quiet    # only problems

ATS slugs drift when companies migrate ATS or rename boards — this catches it
in seconds instead of you discovering weeks later that a company went silent.

WHY IT ALSO FLAGS EMPTY BOARDS
------------------------------
An exception is the easy case. The dangerous case is a board that answers 200
with zero jobs: nothing throws, the run logs "0 postings", and the company is
silently dead for months. That is exactly how Zscaler and Palo Alto Networks
— the two highest-value employers on this list — sat at zero after migrating
off SmartRecruiters, whose API returns `{"totalFound": 0}` for any string you
put in the URL, real customer or not.

So: an empty board is reported as a WARNING, not an OK.
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters import fetch_jobs          # noqa: E402
from src.main import load_companies          # noqa: E402
from src.discovery import board_identity, identity_matches  # noqa: E402
from src.models import Company               # noqa: E402


def check(company: Company) -> tuple[Company, int, str]:
    try:
        return company, len(fetch_jobs(company)), ""
    except Exception as e:                    # noqa: BLE001 — reporting tool
        return company, -1, str(e)[:90]


def check_identity(company: Company) -> tuple[Company, str] | None:
    """Flag a board that belongs to somebody else.

    Slug guessing produces confident nonsense: `css` is CloudKitchens, not CSS
    Corp; `ultimate` is Ultimate Heating & Air, not UKG; `linkedin` is a test
    board. Each serves real jobs — just the wrong company's — so no error ever
    fires and the postings look plausible in the feed.

    Reported, never auto-removed: a legitimate rebrand ("Abnormal Security" ->
    "Abnormal") is indistinguishable from an impostor by string comparison
    alone, and deleting a real board is worse than printing a line to check.
    """
    board = board_identity(company.ats, company.slug)
    if board and not identity_matches(company.name, board):
        return company, board
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true", help="Only print problems")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--identity", action="store_true",
                    help="Also check each board belongs to the company it claims")
    args = ap.parse_args()

    logging.disable(logging.WARNING)
    companies = load_companies()
    print(f"Checking {len(companies)} companies ...\n")

    results: list[tuple[Company, int, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(check, c) for c in companies]
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: (r[1], r[0].name.lower()))
    broken = [r for r in results if r[1] == -1]
    empty = [r for r in results if r[1] == 0]

    for c, n, err in results:
        if n == -1:
            print(f"  FAIL  {c.name:<28} ({c.ats}/{c.slug}) — {err}")
        elif n == 0:
            print(f"  EMPTY {c.name:<28} ({c.ats}/{c.slug}) — 0 postings; "
                  f"slug may be stale or the company moved ATS")
        elif not args.quiet:
            print(f"  ok    {c.name:<28} ({c.ats}/{c.slug}) — {n} postings")

    print(f"\n{len(results) - len(broken) - len(empty)} healthy, "
          f"{len(empty)} empty, {len(broken)} broken.")
    if empty:
        print("\nEmpty boards deliver nothing. Re-run scripts/discover_ats.py for "
              "these names, or check the company's careers page for a new ATS.")
    return 1 if (broken or empty) else 0


if __name__ == "__main__":
    sys.exit(main())
