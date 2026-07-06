"""Ashby public job board API.

Endpoint: POST https://api.ashbyhq.com/posting-api/job-board/{slug}
Body {"includeCompensation": false} — returns all listings + descriptions.
"""
from __future__ import annotations

from typing import Iterable

from ..http_client import post_json
from ..models import Company, Job

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def fetch(company: Company) -> Iterable[Job]:
    data = post_json(API.format(slug=company.slug), {"includeCompensation": False})
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
