#!/usr/bin/env python3
"""Check job-aggregator URLs and record what actually answered.

WHY A SEPARATE CHECKER
----------------------
Aggregators aren't companies. They have no ATS API to probe, so
discover_ats.py has nothing to say about them — the only question worth
asking is "does this URL still serve a page, and is it still the page the
description claims?"

VERDICTS (extends the vocabulary already used in aggregators_resolved.json)
  confirmed_working   200, and the final URL is still the same site
  redirects_noted     200, but it landed somewhere else — rebrand, merger, or
                      a path that now bounces to the homepage. Live, but the
                      row's description may no longer be true.
  blocked_bot_filter  403/429 to a non-browser client. The site is real; it
                      just refuses this UA. NOT a dead link, and specifically
                      not something to "fix" by replacing the URL.
  tls_untrusted_live  The host answers and serves the page, but its certificate
                      chain doesn't validate. Live for a human, and a distinct
                      problem from a dead link: the fix is a cert bundle or an
                      explicit exception, never a new URL.
  broken_needs_review 4xx/5xx/DNS/TLS failure with no replacement to offer.
                      Left flagged rather than silently rewritten, because
                      guessing a successor URL is how wrong data gets in.

`bot_safe` mirrors the companies files: true when an automated fetch can
expect to get the page. A blocked site is real but not bot_safe.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = 20


def registrable(url: str) -> str:
    """Host reduced to its last two labels, so www/subdomain moves aren't 'redirects'."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _second_chance(url: str) -> tuple[str, requests.Response | None]:
    """Tell a broken certificate apart from a host that isn't there.

    A first-pass TLS or connection error covers three very different states:
    an expired/misissued cert on a perfectly live site, a slow host that beat
    the timeout, and a domain that is genuinely gone. Recording all three as
    "dead" would retire working aggregators, so re-ask with the constraint
    that failed removed and report what actually answered.
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    hdrs = {"User-Agent": UA}
    try:                                      # cert invalid, but is it serving?
        r = requests.get(url, headers=hdrs, timeout=TIMEOUT + 15,
                         allow_redirects=True, verify=False)
        return "tls", r
    except Exception:                         # noqa: BLE001
        pass
    try:                                      # last resort: plain http
        r = requests.get("http://" + url.split("://", 1)[-1], headers=hdrs,
                         timeout=TIMEOUT + 15, allow_redirects=True)
        return "http", r
    except Exception:                         # noqa: BLE001
        return "", None


def check(row: dict) -> dict:
    url = (row.get("url") or "").strip()
    out = dict(row)
    out["checked_on"] = date.today().isoformat()
    out["url_final"] = ""
    out["http_status"] = ""

    if not url:
        out.update(verification="broken_needs_review", bot_safe=False,
                   link_status_raw="No URL in source row.")
        return out

    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT,
                         allow_redirects=True)
    except Exception as e:                    # noqa: BLE001 — reporting tool
        why = f"{type(e).__name__}: {str(e)[:110]}"
        how, r2 = _second_chance(url)
        if r2 is not None and r2.status_code in (403, 429):
            out.update(verification="blocked_bot_filter", bot_safe=False,
                       http_status=r2.status_code, url_final=r2.url,
                       link_status_raw=f"HTTP {r2.status_code} to a non-browser "
                                       "client. Site is live; needs a browser-like UA.")
            return out
        if r2 is None or r2.status_code >= 400:
            code = r2.status_code if r2 is not None else "-"
            out.update(verification="broken_needs_review", bot_safe=False,
                       http_status=code,
                       link_status_raw=f"Unreachable on retry ({why}). "
                                       "No replacement URL identified — needs review.")
            return out
        out["http_status"] = r2.status_code
        out["url_final"] = r2.url
        if how == "tls":
            out.update(verification="tls_untrusted_live", bot_safe=False,
                       link_status_raw=f"HTTP {r2.status_code} — site is live and "
                                       f"serving, but its TLS chain fails validation "
                                       f"({why}). Needs a cert exception, not a new URL.")
        else:
            out.update(verification="redirects_noted", bot_safe=True,
                       link_status_raw=f"HTTP {r2.status_code} over plain http only "
                                       f"(https failed: {why}). Landed on {r2.url}.")
        return out

    out["http_status"] = r.status_code
    out["url_final"] = r.url

    if r.status_code in (403, 429):
        out.update(verification="blocked_bot_filter", bot_safe=False,
                   link_status_raw=f"HTTP {r.status_code} to a non-browser client. "
                                   "Site is live; needs a browser-like UA to fetch.")
    elif r.status_code >= 400:
        out.update(verification="broken_needs_review", bot_safe=False,
                   link_status_raw=f"HTTP {r.status_code}. No replacement URL "
                                   "identified — needs manual review.")
    elif registrable(r.url) != registrable(url):
        out.update(verification="redirects_noted", bot_safe=True,
                   link_status_raw=f"HTTP {r.status_code}, redirected off-domain to "
                                   f"{r.url} — verify the row still describes it.")
    else:
        out.update(verification="confirmed_working", bot_safe=True,
                   link_status_raw=f"HTTP {r.status_code}, verified live.")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = raw["platforms"]

    print(f"Checking {len(rows)} platforms ...\n", file=sys.stderr)
    started = time.time()
    done: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(check, r): r for r in rows}
        for fut in as_completed(futs):
            res = fut.result()
            done.append(res)
            tag = {"confirmed_working": "ok   ", "redirects_noted": "REDIR",
                   "blocked_bot_filter": "BLOCK", "tls_untrusted_live": "TLS  ",
                   "broken_needs_review": "DEAD "}
            print(f"  {tag.get(res['verification'],'?')} {res['platform'][:38]:<38} "
                  f"{res.get('http_status','')}", file=sys.stderr)

    done.sort(key=lambda r: r["platform"].lower())
    counts: dict[str, int] = {}
    for r in done:
        counts[r["verification"]] = counts.get(r["verification"], 0) + 1

    raw["platforms"] = done
    raw["total"] = len(done)
    raw["verified_on"] = date.today().isoformat()
    raw["verification_breakdown"] = counts
    Path(args.out).write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                              encoding="utf-8")

    print(f"\nDone in {time.time()-started:.0f}s", file=sys.stderr)
    for k, v in sorted(counts.items()):
        print(f"  {k:22} {v}", file=sys.stderr)
    print(f"\nWrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
