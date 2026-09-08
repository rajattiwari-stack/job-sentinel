#!/usr/bin/env python3
"""Turn the portal-audit JSON files into a discovery input list.

WHY THIS EXISTS
---------------
companies_resolved.json / companies_unresolved.json carry three different URLs
per company and only one of them is worth probing:

  career_portal_url  the URL originally guessed. For the unresolved set this is
                     the one that already 404'd — probing it again just re-reads
                     the same dead page.
  url_final          where that URL actually landed after redirects. Often the
                     live page even when the check was recorded as a failure
                     (REDIRECTED_OFF_TARGET, REDIRECTED_TO_HOME).
  portal_url         the audit's chosen answer. Live and correct for the
                     resolved set; a LinkedIn search stub for the unresolved
                     set, which probe_careers_page refuses by design.

discover_ats.py --deep reads career_portal_url, so feeding it these files raw
points it at the stale URL for exactly the companies that need the live one.
This rewrites the field to the best available live URL first.

Also drops names already in config/companies.yaml: merge_companies.py gives
curated entries precedence regardless, so probing them spends the budget on
a result that gets discarded.

Usage:
    python scripts/prep_discovery_input.py --input companies_unresolved.json \
        --registry config/companies.yaml --out prepped.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.merge_companies import norm  # noqa: E402


def best_url(c: dict) -> str:
    """The most likely-live careers URL for this company, or ''.

    Ordered by how much evidence we have that the URL actually serves a page:
    portal_url was the audit's verified answer, url_final is where the redirect
    chain terminated, career_portal_url is the original guess.
    """
    for key in ("portal_url", "url_final", "career_portal_url"):
        url = (c.get(key) or "").strip()
        if url and "linkedin.com" not in url.lower():
            return url
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--registry", default="config/companies.yaml")
    ap.add_argument("--out", required=True)
    ap.add_argument("--skip-ats-api", action="store_true",
                    help="Drop rows that already carry a resolved ats_platform")
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = raw["companies"] if isinstance(raw, dict) else raw

    reg = yaml.safe_load(Path(args.registry).read_text("utf-8")) or {}
    known = {norm(c["name"]) for c in (reg.get("companies") or [])}

    out, skipped_known, skipped_ats, no_url = [], 0, 0, 0
    for c in rows:
        if norm(c["company"]) in known:
            skipped_known += 1
            continue
        if args.skip_ats_api and c.get("ats_platform"):
            skipped_ats += 1
            continue
        url = best_url(c)
        if not url:
            no_url += 1
        out.append({**c, "career_portal_url": url})

    Path(args.out).write_text(
        json.dumps({"companies": out}, indent=2), encoding="utf-8")

    print(f"in                 : {len(rows)}")
    print(f"skipped (in registry): {skipped_known}")
    if args.skip_ats_api:
        print(f"skipped (has ats)  : {skipped_ats}")
    print(f"kept               : {len(out)}  ({len(out) - no_url} with a probe-able URL)")
    print(f"wrote              : {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
