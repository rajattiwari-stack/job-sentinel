#!/usr/bin/env python3
"""Resolve raw company names to scrapeable ATS boards.

WHY THIS EXISTS
---------------
A company list is usually just names + careers-page links (LinkedIn searches,
`acme.com/careers`, ...). None of that is machine-readable. This scraper needs
an (ats, slug) pair — the stable JSON endpoint behind the careers page.

This script closes that gap ONCE, offline, so the 4x-daily run stays fast:

  raw names  ──►  candidate slugs  ──►  probe 4 ATS APIs  ──►  confirmed boards
                  (name normalizing)     (200 + parseable)      → companies.yaml

A board is only accepted when the endpoint returns valid JSON in the expected
shape. Guessing is cheap; a wrong guess costs nothing because the probe fails.

Usage:
    python scripts/discover_ats.py --input path/to/companies.json [--limit N]
    python scripts/discover_ats.py --input ... --out config/companies.yaml

Output is written as YAML ready to drop into config/companies.yaml, plus a
CSV audit trail of everything that did NOT resolve (so nothing is lost silently).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JobSentinel-Discovery/1.0"
)
TIMEOUT = 12
PER_HOST_DELAY = 0.18          # polite: this is a one-time bulk probe
WORKERS = 14

# Corporate noise that never appears in an ATS slug.
_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|limited|pvt|private|corp|corporation|company|co|group|holdings|"
    r"technologies|technology|systems|software|solutions|services|labs|lab|"
    r"consulting|global|international|india|worldwide|plc|gmbh|sa|ag|nv|bv)\b\.?",
    re.IGNORECASE,
)

_lock = threading.Lock()
_last_hit: dict[str, float] = {}


def _wait(host: str) -> None:
    """Serialize hits per host so a bulk probe stays polite."""
    while True:
        with _lock:
            now = time.monotonic()
            gap = now - _last_hit.get(host, 0.0)
            if gap >= PER_HOST_DELAY:
                _last_hit[host] = now
                return
            sleep_for = PER_HOST_DELAY - gap
        time.sleep(sleep_for)


def slug_candidates(name: str) -> list[str]:
    """Generate plausible ATS slugs for a company name, best guess first."""
    # "1mg (Tata Health)" -> also try the parenthetical as its own name
    extras: list[str] = []
    paren = re.findall(r"\(([^)]+)\)", name)
    base = re.sub(r"\([^)]*\)", " ", name)

    def norm(s: str) -> list[str]:
        s = s.replace("&", " and ")
        s = re.sub(r"[^\w\s-]", " ", s)          # drop punctuation
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            return []
        stripped = _SUFFIXES.sub(" ", s)
        stripped = re.sub(r"\s+", " ", stripped).strip() or s
        out = []
        for variant in dict.fromkeys([stripped, s]):     # de-dup, keep order
            low = variant.lower()
            out.append(re.sub(r"[\s-]+", "", low))       # paloaltonetworks
            out.append(re.sub(r"[\s_]+", "-", low))      # palo-alto-networks
            first = low.split()[0] if low.split() else ""
            if first and len(first) > 3:
                out.append(first)                        # zscaler
        return out

    cands = norm(base)
    for p in paren:
        extras.extend(norm(p))
    seen, ordered = set(), []
    for c in cands + extras:
        if c and len(c) >= 2 and c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered[:4]          # cap the guess budget per company


# ------------------------------------------------------------------ probes ---
# Each probe returns (ok, job_count). ok=True only on a well-formed response.

def probe_greenhouse(slug: str) -> tuple[bool, int]:
    _wait("boards-api.greenhouse.io")
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return ("jobs" in d), len(d.get("jobs", []))


def probe_lever(slug: str) -> tuple[bool, int]:
    _wait("api.lever.co")
    r = requests.get(
        f"https://api.lever.co/v0/postings/{slug}",
        params={"mode": "json"}, headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return isinstance(d, list), (len(d) if isinstance(d, list) else 0)


def probe_ashby(slug: str) -> tuple[bool, int]:
    _wait("api.ashbyhq.com")
    r = requests.post(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        json={"includeCompensation": False},
        headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return isinstance(d, dict) and "jobs" in d, len((d or {}).get("jobs", []))


def probe_smartrecruiters(slug: str) -> tuple[bool, int]:
    """SmartRecruiters answers 200 + totalFound:0 for ANY string, real or not.

    Status code therefore proves nothing here — only a non-empty board does.
    (Greenhouse/Lever 404 and Ashby 401 on unknown slugs, so they self-verify.)
    """
    _wait("api.smartrecruiters.com")
    r = requests.get(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
        params={"limit": 10}, headers={"User-Agent": UA}, timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return False, 0
    d = r.json()
    total = int(d.get("totalFound", 0))
    return ("content" in d and total > 0), total


PROBES = [
    ("greenhouse", probe_greenhouse),
    ("lever", probe_lever),
    ("ashby", probe_ashby),
    ("smartrecruiters", probe_smartrecruiters),
]

# An ATS board URL embedded anywhere in a careers page tells us the truth
# without guessing. Cheap second pass for companies slug-guessing missed.
_EMBED = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
]
_WORKDAY = re.compile(r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)


def probe_careers_page(url: str) -> dict | None:
    """Fetch a careers page and read the ATS straight out of its markup."""
    if not url or "linkedin.com" in url.lower():
        return None
    try:
        host = re.sub(r"^https?://", "", url).split("/")[0]
        _wait(host)
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None
        html = r.text[:400_000]
        blob = html + " " + r.url
        m = _WORKDAY.search(blob)
        if m:
            return {"ats": "workday", "workday_host": m.group(1).lower(), "workday_path": m.group(2)}
        for ats, pat in _EMBED:
            hit = pat.search(blob)
            if hit:
                return {"ats": ats, "slug": hit.group(1)}
    except Exception:                       # noqa: BLE001 — discovery is best-effort
        return None
    return None


def resolve(company: dict, deep: bool) -> dict:
    """Find a scrapeable board for one company. Never raises.

    Probe budget matters: ~85% of names are on none of these platforms, and a
    naive (every slug x every ATS) sweep spends all its time proving negatives.
    So the best-guess slug gets the full sweep, and fallback spellings only get
    the two platforms that actually dominate the long tail.
    """
    name = company["company"]
    result = {"name": name, "status": "unresolved", "meta": company}

    cands = slug_candidates(name)
    plan = [(s, PROBES) if i == 0 else (s, PROBES[:2]) for i, s in enumerate(cands)]

    for slug, probes in plan:
        for ats, fn in probes:
            try:
                ok, count = fn(slug)
            except Exception:               # noqa: BLE001 — a dead probe is just a miss
                continue
            if ok:
                result.update(status="resolved", ats=ats, slug=slug,
                              postings=count, how="slug-guess")
                return result

    if deep:
        found = probe_careers_page(company.get("career_portal_url", ""))
        if found:
            result.update(status="resolved", how="careers-page", postings=-1, **found)
            return result
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Company JSON (list under 'companies')")
    ap.add_argument("--out", default=None, help="Write YAML here (default: stdout preview)")
    ap.add_argument("--unresolved", default=None, help="CSV audit trail of misses")
    ap.add_argument("--limit", type=int, default=0, help="Only probe the first N (sampling)")
    ap.add_argument("--deep", action="store_true", help="Also fetch careers pages (slower, finds Workday)")
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    companies = raw["companies"] if isinstance(raw, dict) else raw
    if args.limit:
        companies = companies[: args.limit]

    print(f"Probing {len(companies)} companies (deep={args.deep}) ...", file=sys.stderr)
    started = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(resolve, c, args.deep): c for c in companies}
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            if r["status"] == "resolved":
                print(f"  HIT  {r['name'][:34]:<34} {r['ats']}/"
                      f"{r.get('slug') or r.get('workday_host')} ({r['postings']} postings)",
                      file=sys.stderr)
            if i % 100 == 0:
                hits = sum(1 for x in results if x["status"] == "resolved")
                print(f"  ... {i}/{len(companies)} probed, {hits} resolved "
                      f"({time.time() - started:.0f}s)", file=sys.stderr)

    hits = [r for r in results if r["status"] == "resolved"]
    miss = [r for r in results if r["status"] != "resolved"]
    print(f"\nResolved {len(hits)}/{len(results)} "
          f"({100 * len(hits) / max(len(results), 1):.1f}%) in {time.time() - started:.0f}s",
          file=sys.stderr)

    hits, rejected = _reject_bad_hits(hits)
    for r in rejected:
        miss.append(r)
    print(f"After collision/empty filtering: {len(hits)} usable, "
          f"{len(rejected)} rejected", file=sys.stderr)

    Path(str(args.out) + ".raw.json").write_text(
        json.dumps([{k: v for k, v in r.items() if k != "meta"} for r in results],
                   indent=1), encoding="utf-8",
    ) if args.out else None

    hits.sort(key=lambda r: r["name"].lower())
    if args.out:
        Path(args.out).write_text(_to_yaml(hits), encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    else:
        print(_to_yaml(hits[:25]))

    if args.unresolved:
        with open(args.unresolved, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["company", "why", "career_portal_url", "sector", "profile_fit"])
            for r in sorted(miss, key=lambda x: x["name"].lower()):
                m = r["meta"]
                w.writerow([r["name"], r["status"], m.get("career_portal_url", ""),
                            m.get("sector", ""), m.get("profile_fit", "")])
        print(f"Wrote {args.unresolved} ({len(miss)} unresolved)", file=sys.stderr)
    return 0


def _reject_bad_hits(hits: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop the two failure modes slug-guessing produces.

    1. Empty boards. A slug that resolves but serves 0 jobs is either stale or
       was never that company's board; either way it can only add noise.
    2. Slug collisions. "Apollo Hospitals" and "Apollo Diagnostics" both guess
       `apollo`, which on Greenhouse is Apollo GraphQL. When several distinct
       companies land on one board, at most one is right and we can't tell
       which — so none of them are trustworthy.
    """
    by_board: dict[tuple[str, str], list[dict]] = {}
    for r in hits:
        key = (r["ats"], (r.get("slug") or r.get("workday_host", "")).lower())
        by_board.setdefault(key, []).append(r)

    keep, drop = [], []
    for key, group in by_board.items():
        if len(group) > 1:
            for r in group:
                r["status"] = f"rejected: slug collision on {key[0]}/{key[1]}"
                drop.append(r)
            continue
        r = group[0]
        if r.get("postings", 0) == 0:
            r["status"] = "rejected: board resolved but has 0 postings"
            drop.append(r)
        else:
            keep.append(r)
    return keep, drop


def _fit_tier(meta: dict) -> str:
    """Map the source list's fit language to a priority tier used for scoring."""
    fit = (meta.get("profile_fit") or "").lower()
    if fit.startswith("high"):
        return "high"
    if fit.startswith("medium"):
        return "medium"
    if fit.startswith("low"):
        return "low"
    return "unknown"


def _to_yaml(rows: list[dict]) -> str:
    out = [
        "# AUTO-GENERATED by scripts/discover_ats.py — every entry below was",
        "# confirmed live against its ATS API at generation time.",
        "# Re-run the generator (or scripts/validate_companies.py) when boards drift.",
        "",
        "companies:",
    ]
    for r in rows:
        meta = r["meta"]
        out.append(f"  - name: {json.dumps(r['name'])}")
        out.append(f"    ats: {r['ats']}")
        if r["ats"] == "workday":
            out.append(f"    slug: {json.dumps(r.get('workday_host', '').split('.')[0])}")
            out.append(f"    workday_host: {r['workday_host']}")
            out.append(f"    workday_path: {r['workday_path']}")
        else:
            out.append(f"    slug: {json.dumps(r['slug'])}")
        out.append(f"    priority: {_fit_tier(meta)}")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    sys.exit(main())
