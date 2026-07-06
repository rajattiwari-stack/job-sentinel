"""Core domain models for Job Sentinel."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Company:
    """A company whose careers page we monitor."""
    name: str
    ats: str                      # greenhouse | lever | ashby | smartrecruiters | workday
    slug: str                     # ATS board token / company identifier
    workday_host: Optional[str] = None   # e.g. "crowdstrike.wd5.myworkdayjobs.com"
    workday_path: Optional[str] = None   # e.g. "crowdstrikecareers"
    enabled: bool = True


@dataclass
class Job:
    """A normalized job posting from any ATS."""
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""          # plain text, HTML stripped
    posted_at: str = ""            # ISO date if available
    department: str = ""
    remote: bool = False
    source_id: str = ""            # ATS-native job id

    # Populated by the matcher
    matched_keywords: list[str] = field(default_factory=list)
    experience_note: str = ""      # e.g. "2-5 yrs" / "unspecified"
    score: int = 0

    @property
    def fingerprint(self) -> str:
        """Stable dedup key. Prefer ATS id; fall back to company+title+location.

        Using the URL alone is unreliable: some ATSs rotate tracking params.
        """
        basis = f"{self.company}|{self.source_id or ''}|{self.title}|{self.location}".lower()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
