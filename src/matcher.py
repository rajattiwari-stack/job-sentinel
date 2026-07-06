"""Filtering brain: keywords, location policy, experience window.

Edge cases handled:
- Short keywords (ZIA, ZPA, EDR, UVM) use word boundaries so "median" never matches "edr" etc.
- "Remote" that is region-locked to a non-India region ("Remote - US only",
  "Remote (EMEA)") is EXCLUDED. Global remote / APAC remote / India remote is included.
- Experience ranges appear in many shapes: "3-5 years", "3 to 5 yrs", "5+ years",
  "minimum of 4 years", "at least 7 years". We take the MINIMUM required years and
  keep the job if min <= max_experience. Unparseable => keep (never silently drop),
  tagged "unspecified" so you can eyeball it.
- Titles like "Senior Staff / Principal / Director / VP" are down-ranked but only
  auto-dropped when the description ALSO demands > max years.
- HTML entities / tags already stripped upstream; we normalize whitespace here too.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .models import Job

# ---------------------------------------------------------------- keywords ---

def build_keyword_patterns(keywords: list[str]) -> list[tuple[str, re.Pattern]]:
    pats = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        escaped = re.escape(kw).replace(r"\ ", r"[\s\-]+")  # "network security" ~ "network-security"
        # word boundaries protect short tokens (zia, zpa, edr, uvm, zpa...)
        pats.append((kw, re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)))
    return pats


# ---------------------------------------------------------------- location ---

_INDIA_HINTS = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|gurugram|gurgaon|"
    r"noida|delhi|ncr|kolkata|ahmedabad|kochi|thiruvananthapuram|trivandrum|"
    r"chandigarh|mohali|jaipur|indore|coimbatore|bhubaneswar|vadodara|nagpur)\b",
    re.IGNORECASE,
)
_REMOTE_HINTS = re.compile(r"\b(remote|work\s*from\s*home|wfh|anywhere|distributed|telecommute)\b", re.IGNORECASE)

# Remote but locked to a region that is NOT India / NOT global.
_REGION_LOCK = re.compile(
    r"\b(us(a)?\s*(only|based)|united\s+states|u\.s\.|canada|north\s+america|"
    r"emea|europe(an)?|uk\b|united\s+kingdom|germany|france|poland|ireland|netherlands|"
    r"latam|latin\s+america|brazil|mexico|australia|new\s+zealand|japan(?!.*india)|"
    r"singapore(?!.*india)|israel|middle\s+east(?!.*india))\b",
    re.IGNORECASE,
)
_GLOBAL_OK = re.compile(r"\b(global|worldwide|anywhere|apac|asia|international)\b", re.IGNORECASE)


def location_ok(job: Job) -> tuple[bool, str]:
    """Policy: (India, any work mode) OR (remote open to India/global).

    Returns (accepted, reason).
    """
    loc = f"{job.location} {job.title}"
    desc_head = job.description[:1500]  # remote policy usually stated early

    if _INDIA_HINTS.search(loc):
        return True, "india"

    is_remote = job.remote or _REMOTE_HINTS.search(loc) or _REMOTE_HINTS.search(desc_head or "")
    if not is_remote:
        return False, "not india, not remote"

    # Remote — check for region locks in the location string itself.
    if _REGION_LOCK.search(loc) and not _INDIA_HINTS.search(loc) and not _GLOBAL_OK.search(loc):
        return False, f"remote but region-locked ({job.location})"
    return True, "remote"


# -------------------------------------------------------------- experience ---

_YRS = r"(?:years?|yrs?)"
_RANGE = re.compile(rf"(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})\s*\+?\s*{_YRS}", re.IGNORECASE)
_PLUS = re.compile(rf"(\d{{1,2}})\s*\+\s*{_YRS}", re.IGNORECASE)
_MINIMUM = re.compile(rf"(?:minimum|min\.?|at\s+least)\s*(?:of\s*)?(\d{{1,2}})\s*\+?\s*{_YRS}", re.IGNORECASE)
_BARE = re.compile(rf"(\d{{1,2}})\s*{_YRS}[\s\w]{{0,20}}?experience", re.IGNORECASE)

_SENIORITY_RED_FLAGS = re.compile(
    r"\b(principal|distinguished|staff|director|vp|vice\s+president|head\s+of|fellow)\b",
    re.IGNORECASE,
)


def experience_check(job: Job, max_years: int) -> tuple[bool, str]:
    """Keep the job if its minimum required experience <= max_years.

    Strategy: collect every candidate 'minimum years' the text asks for and use the
    smallest plausible requirement (postings often list one core requirement plus
    larger 'nice to have' numbers). Unparseable => keep, tagged unspecified.
    """
    text = job.description or ""
    mins: list[int] = []

    for m in _RANGE.finditer(text):
        mins.append(int(m.group(1)))
    for m in _MINIMUM.finditer(text):
        mins.append(int(m.group(1)))
    for m in _PLUS.finditer(text):
        mins.append(int(m.group(1)))
    if not mins:
        for m in _BARE.finditer(text):
            mins.append(int(m.group(1)))

    mins = [n for n in mins if 0 <= n <= 30]  # discard garbage like "30 years of Linux history"

    if not mins:
        if _SENIORITY_RED_FLAGS.search(job.title):
            return True, "unspecified (senior-sounding title — verify)"
        return True, "unspecified"

    required = min(mins)
    if required <= max_years:
        return True, f"min {required} yrs"
    return False, f"needs {required}+ yrs"


# ------------------------------------------------------------------ engine ---

@dataclass
class MatchConfig:
    keywords: list[str]
    max_experience_years: int = 6
    title_boost_keywords: list[str] = field(default_factory=list)


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


class Matcher:
    def __init__(self, cfg: MatchConfig):
        self.cfg = cfg
        self.patterns = build_keyword_patterns(cfg.keywords)

    def evaluate(self, job: Job) -> bool:
        """Mutates job (matched_keywords, score, experience_note). Returns keep/drop."""
        job.description = clean_text(job.description)
        haystack_title = job.title or ""
        haystack_all = f"{haystack_title}\n{job.department}\n{job.description}"

        hits, score = [], 0
        for kw, pat in self.patterns:
            if pat.search(haystack_all):
                hits.append(kw)
                score += 3 if pat.search(haystack_title) else 1  # title hits matter more
        if not hits:
            return False
        job.matched_keywords = hits

        ok_loc, _ = location_ok(job)
        if not ok_loc:
            return False

        ok_exp, note = experience_check(job, self.cfg.max_experience_years)
        job.experience_note = note
        if not ok_exp:
            return False

        if _SENIORITY_RED_FLAGS.search(haystack_title):
            score -= 2
        job.score = score
        return True
