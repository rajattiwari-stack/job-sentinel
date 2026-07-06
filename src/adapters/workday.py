"""Workday (used by CrowdStrike, Fortinet, Tenable, Qualys, many enterprises).

Workday has no official public API, but every hosted board exposes the same
internal endpoint its own frontend calls:

  POST https://{host}/wday/cxs/{tenant}/{path}/jobs
  body: {"appliedFacets": {}, "limit": 20, "offset": N, "searchText": "..."}

Config needs: workday_host (e.g. crowdstrike.wd5.myworkdayjobs.com) and
workday_path (the board name in the careers URL, e.g. crowdstrikecareers).
The tenant is the first label of the host.

Edge cases:
- limit is capped at 20 by Workday → paginate.
- We issue ONE search per configured search term (searchText narrows massively),
  then de-dup by job id across searches.
- Job detail (description) requires a second call per job to
  /wday/cxs/{tenant}/{path}{externalPath} — done only for shortlisted titles.
- 'postedOn' is a human string ("Posted 3 Days Ago") — kept as-is, informational.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from ..http_client import HttpError, post_json, get_json
from ..models import Company, Job

log = logging.getLogger("ats.workday")

LIMIT = 20
MAX_PAGES_PER_TERM = 15   # 300 results per search term — plenty
SEARCH_TERMS = ["security", "network security", "EDR", "SOC", "cyber"]

_PLAUSIBLE = re.compile(
    r"secur|cyber|network|soc\b|siem|edr|threat|vulnerab|firewall|sase|"
    r"zero\s*trust|incident|penetration|appsec|infosec|architect",
    re.IGNORECASE,
)


def _endpoint(company: Company) -> tuple[str, str, str]:
    host = (company.workday_host or "").strip("/")
    path = (company.workday_path or "").strip("/")
    if not host or not path:
        raise ValueError(f"{company.name}: workday_host and workday_path are required")
    tenant = host.split(".")[0]
    return host, tenant, path


def fetch(company: Company) -> Iterable[Job]:
    host, tenant, path = _endpoint(company)
    search_url = f"https://{host}/wday/cxs/{tenant}/{path}/jobs"
    seen: set[str] = set()

    for term in SEARCH_TERMS:
        offset = 0
        for _ in range(MAX_PAGES_PER_TERM):
            try:
                data = post_json(search_url, {
                    "appliedFacets": {}, "limit": LIMIT, "offset": offset, "searchText": term,
                })
            except HttpError as e:
                log.warning("%s: workday search '%s' failed: %s", company.name, term, e)
                break
            postings = data.get("jobPostings", []) or []
            if not postings:
                break
            for p in postings:
                ext = p.get("externalPath", "")
                jid = ext or p.get("bulletFields", [""])[0]
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = (p.get("title") or "").strip()
                if not _PLAUSIBLE.search(title):
                    continue
                loc = p.get("locationsText", "") or ""
                desc, url = "", f"https://{host}/{path}{ext}"
                try:
                    detail = get_json(f"https://{host}/wday/cxs/{tenant}/{path}{ext}")
                    info = detail.get("jobPostingInfo") or {}
                    desc = info.get("jobDescription", "") or ""
                    loc = info.get("location", loc) or loc
                    extra = info.get("additionalLocations") or []
                    if extra:
                        loc = ", ".join([loc, *extra])
                    url = info.get("externalUrl") or url
                except HttpError as e:
                    log.warning("%s: workday detail failed %s: %s", company.name, ext, e)
                yield Job(
                    company=company.name,
                    title=title,
                    url=url,
                    location=loc,
                    description=desc,
                    posted_at=p.get("postedOn", ""),
                    remote="remote" in loc.lower(),
                    source_id=jid,
                )
            offset += LIMIT
            if offset >= int(data.get("total", 0)):
                break
