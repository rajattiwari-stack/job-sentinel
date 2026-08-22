"""SmartRecruiters public postings API (used by Zscaler, Palo Alto Networks, ...).

List:   https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=N
Detail: https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}

Edge cases:
- List responses do NOT include the description → we fetch details, but ONLY for
  postings whose title/location pre-filter as plausible (keeps the run fast and
  avoids hammering the API for irrelevant jobs). The pre-filter is intentionally
  broad — the real Matcher does the precise filtering later.
- Pagination via offset; hard cap to avoid infinite loops on API misbehavior.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from ..http_client import HttpError, get_json
from ..models import Company, Job

log = logging.getLogger("ats.smartrecruiters")

LIST_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings/{pid}"
PAGE = 100
MAX_PAGES = 30    # 3000 postings ceiling — sanity guard
MAX_DETAILS = 60  # per-company round-trip budget; see workday.py for the rationale

# Pre-filter deciding which postings are worth a detail request.
#
# This used to be "security-ish title OR India/remote location", which meant
# every India-based opening — Finance, Sales, Facilities — bought a round trip.
# The matcher now requires a keyword in the TITLE or DEPARTMENT, so a posting
# whose title and function look nothing like security cannot match no matter
# what its description says. Fetching it is pure cost, and SmartRecruiters
# details all serialize behind one host's politeness delay.
#
# Deliberately broader than the configured keyword list: this is a cheap
# pre-filter, and the Matcher does the precise work afterwards.
_PLAUSIBLE = re.compile(
    r"secur|cyber|network|soc\b|siem|edr|xdr|threat|vulnerab|zscaler|zia\b|zpa\b|"
    r"firewall|sase|sse\b|ztna|casb|\bdlp\b|zero\s*trust|incident|penetration|"
    r"appsec|infosec|devsecops|iam\b|identity|malware|forensic|risk|compliance|"
    r"privacy|fraud|trust\s*&?\s*safety|architect",
    re.IGNORECASE,
)


def _loc_str(j: dict) -> str:
    loc = j.get("location") or {}
    parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
    s = ", ".join(p for p in parts if p)
    if loc.get("remote"):
        s = f"Remote, {s}".strip(", ")
    return s


def fetch(company: Company) -> Iterable[Job]:
    offset = 0
    details = 0
    for _ in range(MAX_PAGES):
        data = get_json(LIST_API.format(slug=company.slug), params={"limit": PAGE, "offset": offset})
        content = data.get("content", [])
        if not content:
            break
        for j in content:
            title = (j.get("name") or "").strip()
            loc = _loc_str(j)
            dept = (j.get("function") or {}).get("label", "") or ""
            if not _PLAUSIBLE.search(f"{title} {dept}"):
                continue
            if details >= MAX_DETAILS:
                log.info("%s: detail budget (%d) reached, stopping early.",
                         company.name, MAX_DETAILS)
                return
            details += 1
            pid = j.get("id", "")
            desc = ""
            try:
                detail = get_json(DETAIL_API.format(slug=company.slug, pid=pid))
                sections = ((detail.get("jobAd") or {}).get("sections") or {})
                desc = " ".join(
                    (sections.get(k) or {}).get("text", "")
                    for k in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
                )
                loc = _loc_str(detail) or loc
            except HttpError as e:
                log.warning("Detail fetch failed for %s/%s: %s", company.slug, pid, e)
            yield Job(
                company=company.name,
                title=title,
                url=f"https://jobs.smartrecruiters.com/{company.slug}/{pid}",
                location=loc,
                description=desc,
                posted_at=(j.get("releasedDate") or "")[:10],
                department=((j.get("function") or {}).get("label", "")),
                remote=bool((j.get("location") or {}).get("remote")),
                source_id=str(pid),
            )
        offset += PAGE
        if offset >= int(data.get("totalFound", 0)):
            break
