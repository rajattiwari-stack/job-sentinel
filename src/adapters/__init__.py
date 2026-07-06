"""ATS adapters. Each adapter turns one Applicant Tracking System's public API
into a stream of normalized Job objects.

Why adapters instead of scraping career-page HTML:
- HTML changes weekly and silently; JSON APIs are versioned and stable.
- APIs give structured location/department/posted-date; HTML rarely does.
- One adapter unlocks EVERY company on that ATS — adding a company is config, not code.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..models import Company, Job
from . import greenhouse, lever, ashby, smartrecruiters, workday, amazon, microsoft

_REGISTRY: dict[str, Callable[[Company], Iterable[Job]]] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
    "smartrecruiters": smartrecruiters.fetch,
    "workday": workday.fetch,
    "amazon": amazon.fetch,          # custom big-tech ATS
    "microsoft": microsoft.fetch,    # custom big-tech ATS
}


def fetch_jobs(company: Company) -> list[Job]:
    fn = _REGISTRY.get(company.ats)
    if fn is None:
        raise ValueError(f"Unknown ATS '{company.ats}' for {company.name}")
    return list(fn(company))
