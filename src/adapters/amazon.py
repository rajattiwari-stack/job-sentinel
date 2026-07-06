"""Amazon (amazon.jobs) — custom ATS with a public search JSON endpoint.

GET https://www.amazon.jobs/en/search.json?base_query=security&country=IND&result_limit=100&offset=N
- Also queried with country=Remote-friendly variants via 'location[]' left open.
- Descriptions come inline (description + basic_qualifications) — no detail call needed.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..http_client import HttpError, get_json
from ..models import Company, Job

log = logging.getLogger("ats.amazon")

API = "https://www.amazon.jobs/en/search.json"
LIMIT = 100
MAX_PAGES = 10
QUERIES = [
    {"base_query": "security", "country": "IND"},
    {"base_query": "network security", "country": "IND"},
    {"base_query": "security engineer virtual", "country": ""},  # remote/virtual roles
]


def fetch(company: Company) -> Iterable[Job]:
    seen: set[str] = set()
    for q in QUERIES:
        offset = 0
        for _ in range(MAX_PAGES):
            params = {"result_limit": LIMIT, "offset": offset, "sort": "recent", **q}
            try:
                data = get_json(API, params={k: v for k, v in params.items() if v != ""})
            except HttpError as e:
                log.warning("Amazon query %s failed: %s", q, e)
                break
            jobs = data.get("jobs", []) or []
            if not jobs:
                break
            for j in jobs:
                jid = str(j.get("id") or j.get("id_icims") or "")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                path = j.get("job_path", "")
                loc = j.get("normalized_location") or j.get("location") or ""
                yield Job(
                    company=company.name,
                    title=(j.get("title") or "").strip(),
                    url=f"https://www.amazon.jobs{path}" if path else j.get("url_next_step", ""),
                    location=loc,
                    description=" ".join(filter(None, [
                        j.get("description", ""), j.get("basic_qualifications", ""),
                        j.get("preferred_qualifications", ""),
                    ])),
                    posted_at=(j.get("posted_date") or "")[:12],
                    department=j.get("business_category", "") or "",
                    remote="virtual" in loc.lower() or "remote" in loc.lower(),
                    source_id=jid,
                )
            offset += LIMIT
            if offset >= int(data.get("hits", 0)):
                break
