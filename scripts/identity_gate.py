#!/usr/bin/env python3
"""Reject auto-discovered boards that belong to a different company.

WHY THIS RUNS BEFORE THE MERGE, NOT AFTER
-----------------------------------------
Slug guessing fails loudly (404, nothing merges) or silently. The silent mode
is the dangerous one: `css` resolves to CloudKitchens, `ultimate` to Ultimate
Heating & Air, `linkedin` to a test board. Each serves real, plausible-looking
jobs, so nothing errors and nothing looks wrong in the feed — the postings are
just the wrong company's, filed under a name you trust.

validate_companies.py --identity reports this, deliberately without acting,
because deleting a curated board over a string comparison is worse than a
warning. That logic is right for curated entries and wrong for fresh guesses:
a guess has earned no benefit of the doubt yet. So the same check runs here as
a gate, before anything reaches config/companies.yaml.

WHAT CAN AND CANNOT BE CHECKED
  greenhouse, smartrecruiters  publish the board owner -> checked.
  workday                      slug comes from parsing the company's own
                               careers page markup, and the host is tenant
                               specific (ffive.wd5 = F5) -> trusted.
  lever, ashby                 publish no owner. Cannot be checked; passed
                               through and listed so the risk is visible
                               rather than silent.

Usage:
    python scripts/identity_gate.py --in discovered.yaml --out clean.yaml
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.discovery import board_identity, identity_matches  # noqa: E402

CHECKABLE = {"greenhouse", "smartrecruiters"}


def verdict(c: dict) -> tuple[dict, str, str]:
    ats = c.get("ats", "")
    if ats not in CHECKABLE:
        reason = ("workday host is tenant-specific and the slug came from the "
                  "company's own careers page" if ats == "workday"
                  else f"{ats} publishes no board owner")
        return c, "unverifiable", reason
    owner = board_identity(ats, str(c.get("slug", "")))
    if owner is None:
        return c, "unverifiable", "owner lookup returned nothing"
    if identity_matches(c["name"], owner):
        return c, "match", owner
    return c, "mismatch", owner


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    rows = (yaml.safe_load(Path(args.src).read_text("utf-8")) or {}).get("companies", [])
    print(f"Identity-checking {len(rows)} discovered boards ...\n")

    out: list[tuple[dict, str, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(verdict, c) for c in rows]
        for f in as_completed(futs):
            out.append(f.result())

    keep = [r for r in out if r[1] != "mismatch"]
    drop = [r for r in out if r[1] == "mismatch"]
    unver = [r for r in keep if r[1] == "unverifiable" and r[0].get("ats") in ("lever", "ashby")]

    for c, v, why in sorted(drop, key=lambda r: r[0]["name"].lower()):
        print(f"  REJECT {c['name'][:30]:<30} {c['ats']}/{c.get('slug')} "
              f"— board belongs to \"{why}\"")
    print()
    for c, v, why in sorted([r for r in keep if r[1] == 'match'], key=lambda r: r[0]['name'].lower()):
        print(f"  ok     {c['name'][:30]:<30} {c['ats']}/{c.get('slug')} — owner \"{why}\"")
    print()
    for c, v, why in sorted(unver, key=lambda r: r[0]["name"].lower()):
        print(f"  UNVER  {c['name'][:30]:<30} {c['ats']}/{c.get('slug')} — {why}")

    keep.sort(key=lambda r: r[0]["name"].lower())
    lines = ["# Identity-gated output of scripts/identity_gate.py.",
             "# Boards whose ATS names a different owner have been removed.",
             "", "companies:"]
    for c, _, _ in keep:
        lines.append(f"  - name: {json.dumps(c['name'])}")
        lines.append(f"    ats: {c['ats']}")
        lines.append(f"    slug: {json.dumps(str(c.get('slug', '')))}")
        if c["ats"] == "workday":
            lines.append(f"    workday_host: {c.get('workday_host','')}")
            lines.append(f"    workday_path: {c.get('workday_path','')}")
        lines.append(f"    priority: {c.get('priority','unknown')}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\nkept {len(keep)}, rejected {len(drop)} "
          f"({len(unver)} kept but unverifiable) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
