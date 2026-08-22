"""Ashby public job board API.

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{slug}
Returns all listings + descriptions in one response.

NOTE: this endpoint used to be a POST with {"includeCompensation": false}.
Ashby now answers POST with 401 Unauthorized for every board, valid or not,
which read exactly like a wrong slug — so all three configured Ashby
companies (Notion, OpenAI, Ramp) failed identically and looked like three
unrelated stale slugs rather than one API change. GET returns 200, and an
unknown slug now 404s, which is a far cleaner signal to probe against.
"""
from __future__ import annotations

from typing import Iterable

from ..http_client import get_json
from ..models import Company, Job

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch(company: Company) -> Iterable[Job]:
    data = get_json(API.format(slug=company.slug), params={"includeCompensation": "false"})
    for j in (data or {}).get("jobs", []):
        secondary = [s.get("location", "") for s in j.get("secondaryLocations", []) if s.get("location")]
        loc = ", ".join(x for x in [j.get("location", ""), *secondary] if x)
        yield Job(
            company=company.name,
            title=(j.get("title") or "").strip(),
            url=j.get("jobUrl") or j.get("applyUrl") or "",
            location=loc,
            description=j.get("descriptionPlain") or j.get("descriptionHtml") or "",
            posted_at=(j.get("publishedAt") or "")[:10],
            department=j.get("department", "") or j.get("team", "") or "",
            remote=bool(j.get("isRemote")) or "remote" in loc.lower(),
            source_id=str(j.get("id", "")),
        )
