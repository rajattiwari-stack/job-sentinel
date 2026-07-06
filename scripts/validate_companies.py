#!/usr/bin/env python3
"""Ping every company's ATS endpoint and report broken slugs.

Run this after editing config/companies.yaml:
    python scripts/validate_companies.py

ATS slugs drift when companies migrate ATS or rename boards — this catches it
in seconds instead of you discovering weeks later that a company went silent.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.adapters import fetch_jobs          # noqa: E402
from src.main import load_companies          # noqa: E402


def main() -> int:
    bad = 0
    for c in load_companies():
        try:
            jobs = fetch_jobs(c)
            print(f"  OK   {c.name:<24} ({c.ats}/{c.slug}) — {len(jobs)} postings")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  FAIL {c.name:<24} ({c.ats}/{c.slug}) — {e}")
    print(f"\n{bad} broken compan{'y' if bad == 1 else 'ies'}." if bad else "\nAll companies healthy.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
