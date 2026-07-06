"""Microsoft Careers — custom ATS, public search API used by careers.microsoft.com.

GET https://gcsservices.careers.microsoft.com/search/api/v1/search
    ?q=security&lc=India&l=en_us&pg=1&pgSz=20&o=Recent&flt=true
- Paginated (pgSz max ~20). Result payload nests under operationResult.result.jobs.
- Job URL pattern: https://jobs.careers.microsoft.com/global/en/job/{jobId}/
- Descriptions come in 'properties.description' on the search hit (truncated but
  sufficient for keyword + experience matching).
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..http_client import HttpError, get_json
from ..models import Company, Job

log = logging.getLogger("ats.microsoft")

API = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
PAGE_SIZE = 20
MAX_PAGES = 15
QUERIES = [
    {"q": "security", "lc": "India"},
    {"q": "network security", "lc": "India"},
    {"q": "security engineer", "ws": "Up to 100% work from home"},
]


def fetch(company: Company) -> Iterable[Job]:
    seen: set[str] = set()
    for q in QUERIES:
        for page in range(1, MAX_PAGES + 1):
            params = {"l": "en_us", "pg": page, "pgSz": PAGE_SIZE, "o": "Recent", "flt": "true", **q}
            try:
                data = get_json(API, params=params)
            except HttpError as e:
                log.warning("Microsoft query %s failed: %s", q, e)
                break
            result = ((data.get("operationResult") or {}).get("result") or {})
            jobs = result.get("jobs", []) or []
            if not jobs:
                break
            for j in jobs:
                jid = str(j.get("jobId") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                props = j.get("properties") or {}
                locs = props.get("locations") or []
                loc = "; ".join(locs) if locs else (props.get("primaryLocation") or "")
                wsite = (props.get("workSiteFlexibility") or "")
                yield Job(
                    company=company.name,
                    title=(j.get("title") or "").strip(),
                    url=f"https://jobs.careers.microsoft.com/global/en/job/{jid}/",
                    location=f"{loc} ({wsite})".strip() if wsite else loc,
                    description=" ".join(filter(None, [
                        props.get("description", ""), props.get("qualifications", ""),
                        props.get("responsibilities", ""),
                    ])),
                    posted_at=(j.get("postingDate") or "")[:10],
                    department=props.get("profession", "") or "",
                    remote="100%" in wsite or "remote" in wsite.lower(),
                    source_id=jid,
                )
            total = int(result.get("totalJobs", 0))
            if page * PAGE_SIZE >= total:
                break
