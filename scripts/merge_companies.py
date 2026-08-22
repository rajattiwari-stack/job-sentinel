#!/usr/bin/env python3
"""Merge auto-discovered ATS boards into the curated company registry.

WHY A MERGE AND NOT A REPLACE
-----------------------------
Auto-discovery (scripts/discover_ats.py) guesses slugs and probes four JSON
APIs. That finds Greenhouse/Lever/Ashby/SmartRecruiters boards well, and finds
Workday boards essentially never — a Workday board needs a host AND a board
path (crowdstrike.wd5.myworkdayjobs.com + crowdstrikecareers) and neither is
derivable from a company name.

So a wholesale replace would quietly delete CrowdStrike, Tenable, Qualys,
Fortinet, Proofpoint, Splunk, Adobe, Salesforce and friends — hand-verified
boards that no amount of re-running discovery brings back.

Precedence, therefore:
  1. Curated entries win. They were verified by hand; a guess doesn't override.
  2. Discovered entries are added for companies the curated file doesn't cover.
  3. Duplicate boards (same ats+slug under two names) are dropped — see
     _reject_bad_hits in discover_ats.py for why they're untrustworthy.

Usage:
    python scripts/merge_companies.py \
        --curated config/companies.yaml \
        --discovered discovered.yaml \
        --out config/companies.yaml
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, "unknown": 3}


def norm(name: str) -> str:
    """Match 'Splunk (Cisco)' to 'Splunk' and 'Trend Micro' to 'trendmicro'."""
    base = re.sub(r"\([^)]*\)", " ", name)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    return data.get("companies", []) or []


def board_key(c: dict) -> tuple[str, str]:
    if c.get("ats") == "workday":
        return ("workday", f"{c.get('workday_host', '')}/{c.get('workday_path', '')}".lower())
    return (c.get("ats", ""), str(c.get("slug", "")).lower())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated", required=True)
    ap.add_argument("--discovered", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    curated = load(Path(args.curated))
    discovered = load(Path(args.discovered))

    merged: list[dict] = []
    seen_names: set[str] = set()
    seen_boards: set[tuple[str, str]] = set()

    kept_curated = added = skip_name = skip_board = 0

    for c in curated:                       # curated first — they win ties
        n, b = norm(c["name"]), board_key(c)
        if n in seen_names or b in seen_boards:
            continue
        seen_names.add(n)
        seen_boards.add(b)
        merged.append(c)
        kept_curated += 1

    for c in discovered:
        n, b = norm(c["name"]), board_key(c)
        if n in seen_names:
            skip_name += 1
            continue
        if b in seen_boards:
            skip_board += 1
            continue
        seen_names.add(n)
        seen_boards.add(b)
        merged.append(c)
        added += 1

    merged.sort(key=lambda c: (PRIORITY_ORDER.get(c.get("priority", "unknown"), 3),
                               c["name"].lower()))

    Path(args.out).write_text(_render(merged), encoding="utf-8")

    print(f"curated kept      : {kept_curated}")
    print(f"discovered added  : {added}")
    print(f"skipped (dup name): {skip_name}")
    print(f"skipped (dup board): {skip_board}")
    print(f"TOTAL             : {len(merged)}  ->  {args.out}")
    by_ats: dict[str, int] = {}
    for c in merged:
        by_ats[c["ats"]] = by_ats.get(c["ats"], 0) + 1
    print("by ATS            :", ", ".join(f"{k}={v}" for k, v in sorted(by_ats.items())))
    return 0


def _render(rows: list[dict]) -> str:
    out = [
        "# Company registry — the set of boards scanned every run.",
        "#",
        "# Curated entries were verified by hand; the rest were resolved by",
        "# scripts/discover_ats.py, which only accepts a board after its ATS API",
        "# answers with a well-formed, NON-EMPTY response. Regenerate with:",
        "#",
        "#   python scripts/discover_ats.py --input <list>.json --out discovered.yaml",
        "#   python scripts/merge_companies.py --curated config/companies.yaml \\",
        "#       --discovered discovered.yaml --out config/companies.yaml",
        "#",
        "# `priority` (high|medium|low|unknown) ranks alerts — it never filters.",
        "# Set `enabled: false` to pause a company without deleting it.",
        "#",
        "# Run scripts/validate_companies.py after editing: it flags both broken",
        "# slugs AND boards that answer 200 with zero jobs (the silent killer).",
        "",
        "companies:",
    ]
    current = None
    for c in rows:
        tier = c.get("priority", "unknown")
        if tier != current:
            current = tier
            out.append(f"\n  # ---- priority: {tier} ----")
        out.append(f"  - name: {json.dumps(c['name'])}")
        out.append(f"    ats: {c['ats']}")
        out.append(f"    slug: {json.dumps(str(c.get('slug', '')))}")
        if c.get("ats") == "workday":
            out.append(f"    workday_host: {c.get('workday_host', '')}")
            out.append(f"    workday_path: {c.get('workday_path', '')}")
        out.append(f"    priority: {tier}")
        if c.get("enabled") is False:
            out.append("    enabled: false")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
