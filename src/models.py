"""Core domain models for Job Sentinel."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Company:
    """A company whose careers page we monitor."""
    name: str
    ats: str
    slug: str
    workday_host: Optional[str] = None
    workday_path: Optional[str] = None
    enabled: bool = True
    priority: str = "unknown"


@dataclass
class Job:
    """A normalized job posting from any ATS."""
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    posted_at: str = ""
    department: str = ""
    remote: bool = False
    source_id: str = ""

    priority: str = "unknown"

    matched_keywords: list[str] = field(default_factory=list)
    experience_note: str = ""
    role_fit: str = ""
    score: int = 0

    @property
    def fingerprint(self) -> str:
        """Dedup key: company + title + location.

        The ATS id is deliberately excluded. Amazon files one role as several
        requisitions with different ids, which produced four identical alerts
        for the same job, and reposting an unfilled role under a fresh id would
        re-alert every time.
        """
        basis = f"{self.company}|{self.title}|{self.location}".lower()
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
