"""Lever public postings API.

Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
- Single response, no pagination; descriptions arrive both as HTML and plain text.
"""
from __future__ import annotations

from typing import Iterable

from ..http_client import get_json
from ..models import Company, Job

API = "https://api.lever.co/v0/postings/{slug}"


def fetch(company: Company) -> Iterable[Job]:
    data = get_json(API.format(slug=company.slug), params={"mode": "json"})
    if not isinstance(data, list):
        return
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location", "") or ""
        all_locs = j.get("allLocations") or []
        if all_locs:
            loc = ", ".join(dict.fromkeys([loc, *all_locs]).keys()).strip(", ")
        wt = (j.get("workplaceType") or "").lower()
        ts = j.get("createdAt")
        posted = ""
        if isinstance(ts, (int, float)):
            from datetime import datetime, timezone
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date().isoformat()
        yield Job(
            company=company.name,
            title=(j.get("text") or "").strip(),
            url=j.get("hostedUrl", ""),
            location=loc,
            description=(j.get("descriptionPlain") or j.get("description") or ""),
            posted_at=posted,
            department=cats.get("team", "") or cats.get("department", "") or "",
            remote=(wt == "remote") or "remote" in loc.lower(),
            source_id=str(j.get("id", "")),
        )
