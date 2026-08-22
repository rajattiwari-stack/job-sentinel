"""Resolve a company name to a live ATS board.

Shared by two callers with different tempos:

- scripts/discover_ats.py — offline, bulk, thousands of names at once.
- src/healer.py — inside the run, for the handful of boards that just went dark.

Deliberately does NOT use http_client: that client retries 4x with backoff,
which is right for fetching a board you believe in and wrong for probing a
guess you expect to fail. A probe wants one fast attempt and a clean verdict.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Callable, Optional

import requests

log = logging.getLogger("discovery")

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JobSentinel-Discovery/1.0"
)
TIMEOUT = 12
PER_HOST_DELAY = 0.18

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
    """Space out hits to one host. Slot is reserved under the lock, slept outside."""
    with _lock:
        now = time.monotonic()
        earliest = max(now, _last_hit.get(host, 0.0) + PER_HOST_DELAY)
        _last_hit[host] = earliest
    delay = earliest - now
    if delay > 0:
        time.sleep(delay)


def slug_candidates(name: str, limit: int = 4) -> list[str]:
    """Plausible ATS slugs for a company name, best guess first."""
    extras: list[str] = []
    paren = re.findall(r"\(([^)]+)\)", name)      # "Splunk (Cisco)" -> try "Cisco" too
    base = re.sub(r"\([^)]*\)", " ", name)

    def norm(s: str) -> list[str]:
        s = re.sub(r"\s+", " ", re.sub(r"[^\w\s-]", " ", s.replace("&", " and "))).strip()
        if not s:
            return []
        stripped = re.sub(r"\s+", " ", _SUFFIXES.sub(" ", s)).strip() or s
        out = []
        for variant in dict.fromkeys([stripped, s]):
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
    return ordered[:limit]


# ------------------------------------------------------------------ probes ---
# Each returns (ok, job_count). ok is True only for a well-formed, useful board.

def probe_greenhouse(slug: str) -> tuple[bool, int]:
    _wait("boards-api.greenhouse.io")
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return ("jobs" in d), len(d.get("jobs", []))


def probe_lever(slug: str) -> tuple[bool, int]:
    _wait("api.lever.co")
    r = requests.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"},
                     headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return isinstance(d, list), (len(d) if isinstance(d, list) else 0)


def probe_ashby(slug: str) -> tuple[bool, int]:
    _wait("api.ashbyhq.com")
    r = requests.post(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                      json={"includeCompensation": False},
                      headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, 0
    d = r.json()
    return isinstance(d, dict) and "jobs" in d, len((d or {}).get("jobs", []))


def probe_smartrecruiters(slug: str) -> tuple[bool, int]:
    """SmartRecruiters answers 200 + totalFound:0 for ANY string, real or not.

    Status code proves nothing here — only a non-empty board does. Greenhouse
    and Lever 404 on unknown slugs and Ashby 401s, so those self-verify.
    """
    _wait("api.smartrecruiters.com")
    r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                     params={"limit": 10}, headers={"User-Agent": UA}, timeout=TIMEOUT)
    if r.status_code != 200:
        return False, 0
    d = r.json()
    total = int(d.get("totalFound", 0))
    return ("content" in d and total > 0), total


PROBES: list[tuple[str, Callable[[str], tuple[bool, int]]]] = [
    ("greenhouse", probe_greenhouse),
    ("lever", probe_lever),
    ("ashby", probe_ashby),
    ("smartrecruiters", probe_smartrecruiters),
]

# An ATS URL embedded in a careers page tells us the truth without guessing —
# and it is the only way to find a Workday board, whose host and path are not
# derivable from a company name.
_EMBED = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
]
_WORKDAY = re.compile(
    r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)


def probe_careers_page(url: str) -> Optional[dict]:
    """Read the ATS straight out of a careers page's markup."""
    if not url or "linkedin.com" in url.lower():
        return None
    try:
        _wait(re.sub(r"^https?://", "", url).split("/")[0])
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None
        blob = r.text[:400_000] + " " + r.url
        m = _WORKDAY.search(blob)
        if m:
            return {"ats": "workday", "slug": m.group(1).split(".")[0],
                    "workday_host": m.group(1).lower(), "workday_path": m.group(2)}
        for ats, pat in _EMBED:
            hit = pat.search(blob)
            if hit:
                return {"ats": ats, "slug": hit.group(1)}
    except Exception:                        # noqa: BLE001 — discovery is best-effort
        return None
    return None


def find_board(name: str, careers_url: str = "", full_sweep: bool = True) -> Optional[dict]:
    """Find a live board for one company name. Returns None if nothing resolves.

    Probe budget matters when most names resolve to nothing: the best-guess
    slug gets every platform, fallback spellings only get the two that
    dominate the long tail.
    """
    cands = slug_candidates(name)
    plan = [(s, PROBES) if i == 0 or full_sweep else (s, PROBES[:2])
            for i, s in enumerate(cands)]
    for slug, probes in plan:
        for ats, fn in probes:
            try:
                ok, count = fn(slug)
            except Exception:                # noqa: BLE001 — a dead probe is just a miss
                continue
            if ok:
                return {"ats": ats, "slug": slug, "postings": count, "how": "slug-guess"}

    found = probe_careers_page(careers_url)
    if found:
        found.setdefault("postings", -1)
        found["how"] = "careers-page"
        return found
    return None
