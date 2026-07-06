"""Greenhouse public job board API.

Endpoint: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
- Returns ALL open jobs in one response (no pagination) with full HTML content.
- 404 => wrong slug (surface loudly so config gets fixed).
"""
from __future__ import annotations

from typing import Iterable

from ..http_client import get_json
from ..models import Company, Job

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"


def fetch(company: Company) -> Iterable[Job]:
    data = get_json(API.format(slug=company.slug), params={"content": "true"})
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "") or ""
        offices = ", ".join(o.get("name", "") for o in j.get("offices", []) if o.get("name"))
        depts = ", ".join(d.get("name", "") for d in j.get("departments", []) if d.get("name"))
        yield Job(
            company=company.name,
            title=j.get("title", "").strip(),
            url=j.get("absolute_url", ""),
            location=", ".join(x for x in {loc, offices} if x),
            description=j.get("content", "") or "",
            posted_at=(j.get("updated_at") or j.get("first_published") or "")[:10],
            department=depts,
            remote="remote" in loc.lower(),
            source_id=str(j.get("id", "")),
        )
