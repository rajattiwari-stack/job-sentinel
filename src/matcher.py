"""Filtering: keywords, seniority veto, location policy, experience and freshness."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .models import Job


def build_keyword_patterns(keywords: list[str]) -> list[tuple[str, re.Pattern]]:
    pats = []
    for kw in keywords:
        kw = kw.strip()
        if not kw:
            continue
        escaped = re.escape(kw).replace(r"\ ", r"[\s\-]+")
        pats.append((kw, re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)))
    return pats


_INDIA_HINTS = re.compile(
    r"\b(india|bengaluru|bangalore|hyderabad|pune|mumbai|chennai|gurugram|gurgaon|"
    r"noida|delhi|ncr|kolkata|ahmedabad|kochi|thiruvananthapuram|trivandrum|"
    r"chandigarh|mohali|jaipur|indore|coimbatore|bhubaneswar|vadodara|nagpur)\b",
    re.IGNORECASE,
)
_REMOTE_HINTS = re.compile(r"\b(remote|work\s*from\s*home|wfh|anywhere|distributed|telecommute)\b", re.IGNORECASE)

_REGION_LOCK = re.compile(
    r"\b(us(a)?\s*(only|based)|united\s+states|u\.s\.|canada|north\s+america|"
    r"emea|europe(an)?|united\s+kingdom|germany|france|poland|ireland|netherlands|"
    r"latam|latin\s+america|brazil|mexico|australia|new\s+zealand|japan(?!.*india)|"
    r"singapore(?!.*india)|israel|middle\s+east(?!.*india))\b",
    re.IGNORECASE,
)
_REGION_LOCK_TOKENS = re.compile(r"\b(USA?|UK|EU|EMEA|LATAM|APJ)\b")

_US_STATES = re.compile(
    r"\b(alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|"
    r"florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|"
    r"maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|"
    r"nebraska|nevada|new\s+hampshire|new\s+jersey|new\s+mexico|new\s+york|"
    r"north\s+carolina|north\s+dakota|ohio|oklahoma|oregon|pennsylvania|rhode\s+island|"
    r"south\s+carolina|south\s+dakota|tennessee|texas|utah|vermont|virginia|"
    r"washington|west\s+virginia|wisconsin|wyoming|district\s+of\s+columbia)\b",
    re.IGNORECASE,
)
_GLOBAL_OK = re.compile(r"\b(global|worldwide|anywhere|apac|international)\b", re.IGNORECASE)

_PLACELESS = re.compile(
    r"\b(remote|hybrid|on-?site|in-?office|work\s*from\s*home|wfh|flexible|"
    r"telecommute|distributed|optional|primary|multiple|locations?|office|"
    r"home|based|any|or|and|the|other|various|open)\b|[\s,;:()/|+—–-]",
    re.IGNORECASE,
)


def _region_locked(text: str) -> bool:
    return bool(
        _REGION_LOCK.search(text)
        or _REGION_LOCK_TOKENS.search(text)
        or _US_STATES.search(text)
    )


def location_ok(job: Job) -> tuple[bool, str]:
    """Accept India in any mode, or remote that is open to India."""
    loc = f"{job.location} {job.title}"
    desc_head = job.description[:1500]

    if _INDIA_HINTS.search(loc):
        return True, "india"

    is_remote = job.remote or _REMOTE_HINTS.search(loc) or _REMOTE_HINTS.search(desc_head or "")
    if not is_remote:
        return False, "not india, not remote"

    if _region_locked(loc):
        return False, f"remote but region-locked ({job.location})"
    if _GLOBAL_OK.search(loc):
        return True, "remote (global)"

    residue = _PLACELESS.sub(" ", job.location or "").strip()
    if residue:
        return False, f"remote but tied to {job.location}"
    return True, "remote (unspecified)"


_YRS = r"(?:years?|yrs?)"
_RANGE = re.compile(rf"(\d{{1,2}})\s*(?:-|–|to)\s*(\d{{1,2}})\s*\+?\s*{_YRS}", re.IGNORECASE)
_PLUS = re.compile(rf"(\d{{1,2}})\s*\+\s*{_YRS}", re.IGNORECASE)
_MINIMUM = re.compile(rf"(?:minimum|min\.?|at\s+least)\s*(?:of\s*)?(\d{{1,2}})\s*\+?\s*{_YRS}", re.IGNORECASE)
_BARE = re.compile(rf"(\d{{1,2}})\s*{_YRS}[\s\w]{{0,20}}?experience", re.IGNORECASE)

_SENIORITY_RED_FLAGS = re.compile(
    r"\b(principal|distinguished|staff|director|vp|vice\s+president|head\s+of|fellow)\b",
    re.IGNORECASE,
)


def experience_check(job: Job, max_years: int, reject_above: int = 0,
                     candidate_years: int = 0) -> tuple[bool, str]:
    """Keep a posting only if its stated experience band fits the candidate.

    max_years is the highest opening requirement worth seeing. reject_above
    discards a posting whose ceiling is far beyond that even when it also
    mentions a small number, which is how a 5-8 year role sneaks past a
    lowest-number-wins rule.
    """
    text = job.description or ""
    mins: list[int] = []

    for m in _RANGE.finditer(text):
        mins.append(int(m.group(1)))
        mins.append(int(m.group(2)))
    for m in _MINIMUM.finditer(text):
        mins.append(int(m.group(1)))
    for m in _PLUS.finditer(text):
        mins.append(int(m.group(1)))
    if not mins:
        for m in _BARE.finditer(text):
            mins.append(int(m.group(1)))

    mins = [n for n in mins if 0 <= n <= 30]

    if not mins:
        if _SENIORITY_RED_FLAGS.search(job.title):
            return True, "experience not stated (senior-sounding title — verify)"
        return True, "experience not stated"

    required, highest = min(mins), max(mins)

    if required > max_years:
        return False, f"needs {required}+ yrs"
    if reject_above and highest > reject_above:
        return False, f"asks up to {highest} yrs"

    band = f"wants {required}-{highest} yrs" if highest > required else f"wants {required}+ yrs"
    if candidate_years and required > candidate_years:
        band += f" (you have {candidate_years})"
    return True, band


_POSTED_PROSE = re.compile(r"(\d+)\+?\s*day", re.IGNORECASE)
_POSTED_TODAY = re.compile(r"\b(today|just posted|yesterday)\b", re.IGNORECASE)
_MONTH_NAME = re.compile(
    r"([A-Z][a-z]{2,8})\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE)
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], start=1)}


def days_since_posted(job: Job) -> int | None:
    raw = (job.posted_at or "").strip()
    if not raw:
        return None
    if _POSTED_TODAY.search(raw):
        return 0
    m = _POSTED_PROSE.search(raw)
    if m:
        return int(m.group(1))
    m = _MONTH_NAME.search(raw)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            try:
                posted = date(int(m.group(3)), month, int(m.group(2)))
            except ValueError:
                return None
            return max((date.today() - posted).days, 0)
    try:
        posted = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max((date.today() - posted).days, 0)


def freshness_boost(job: Job) -> int:
    age = days_since_posted(job)
    if age is None:
        return 0
    if age <= 3:
        return 5
    if age <= 7:
        return 3
    if age <= 14:
        return 1
    return 0


@dataclass
class RoleWeight:
    name: str
    weight: int
    pattern: re.Pattern


@dataclass
class MatchConfig:
    keywords: list[str]
    max_experience_years: int = 3
    reject_above_years: int = 0
    candidate_years: int = 0
    max_posting_age_days: int = 0
    title_boost_keywords: list[str] = field(default_factory=list)
    exclude_titles: list[str] = field(default_factory=list)
    priority_boost: dict[str, int] = field(default_factory=dict)
    require_role_match: bool = True
    role_weights: list[RoleWeight] = field(default_factory=list)


def build_exclude_pattern(words: list[str]) -> re.Pattern | None:
    cleaned = [re.escape(w.strip()).replace(r"\ ", r"[\s\-]+") for w in words if w.strip()]
    if not cleaned:
        return None
    return re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(cleaned)})(?![A-Za-z0-9])", re.IGNORECASE)


def build_role_weights(raw: list[dict]) -> list[RoleWeight]:
    out = []
    for entry in raw or []:
        pats = [re.escape(p.strip()).replace(r"\ ", r"[\s\-]+")
                for p in entry.get("patterns", []) if p.strip()]
        if not pats:
            continue
        out.append(RoleWeight(
            name=entry.get("name", "role"),
            weight=int(entry.get("weight", 0)),
            pattern=re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(pats)})(?![A-Za-z0-9])", re.IGNORECASE),
        ))
    return out


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
        self.exclude = build_exclude_pattern(cfg.exclude_titles)

    def evaluate(self, job: Job) -> bool:
        job.description = clean_text(job.description)
        haystack_title = job.title or ""

        if self.exclude and self.exclude.search(haystack_title):
            return False

        if self.cfg.max_posting_age_days:
            age = days_since_posted(job)
            if age is not None and age > self.cfg.max_posting_age_days:
                return False

        title_hits, dept_hits, desc_hits = [], [], []
        for kw, pat in self.patterns:
            if pat.search(haystack_title):
                title_hits.append(kw)
            elif pat.search(job.department or ""):
                dept_hits.append(kw)
            elif pat.search(job.description or ""):
                desc_hits.append(kw)

        if self.cfg.require_role_match and not (title_hits or dept_hits):
            return False
        if not (title_hits or dept_hits or desc_hits):
            return False

        job.matched_keywords = title_hits + dept_hits + desc_hits
        score = 3 * len(title_hits) + 2 * len(dept_hits) + len(desc_hits)

        ok_loc, loc_reason = location_ok(job)
        if not ok_loc:
            return False

        ok_exp, note = experience_check(job, self.cfg.max_experience_years,
                                        self.cfg.reject_above_years,
                                        self.cfg.candidate_years)
        job.experience_note = note
        if not ok_exp:
            return False

        for role in self.cfg.role_weights:
            if role.pattern.search(haystack_title):
                score += role.weight
                job.role_fit = role.name
                break

        if _SENIORITY_RED_FLAGS.search(haystack_title):
            score -= 2
        if loc_reason == "india":
            score += 2
        score += self.cfg.priority_boost.get(job.priority, 0)
        score += freshness_boost(job)
        job.score = score
        return True
