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
import threading
from dataclasses import dataclass, field
from datetime import date, datetime

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
#
# Two patterns, deliberately: the multi-word names are safe case-insensitively,
# but bare tokens ("US", "UK", "EU") must stay case-SENSITIVE or the word "us"
# in ordinary prose ("join us in Bengaluru") would region-lock a valid job.
_REGION_LOCK = re.compile(
    r"\b(us(a)?\s*(only|based)|united\s+states|u\.s\.|canada|north\s+america|"
    r"emea|europe(an)?|united\s+kingdom|germany|france|poland|ireland|netherlands|"
    r"latam|latin\s+america|brazil|mexico|australia|new\s+zealand|japan(?!.*india)|"
    r"singapore(?!.*india)|israel|middle\s+east(?!.*india))\b",
    re.IGNORECASE,
)
_REGION_LOCK_TOKENS = re.compile(r"\b(USA?|UK|EU|EMEA|LATAM|APJ)\b")   # case-sensitive on purpose

# US state names: "Remote - Texas" / "Remote, Ohio" is US-locked even though the
# posting never says "US". Without this the most common phrasing of a US-only
# remote role sails straight through to an India-based candidate.
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
_GLOBAL_OK = re.compile(r"\b(global|worldwide|anywhere|apac|asia|international)\b", re.IGNORECASE)


def _region_locked(text: str) -> bool:
    return bool(
        _REGION_LOCK.search(text)
        or _REGION_LOCK_TOKENS.search(text)
        or _US_STATES.search(text)
    )


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
    # "Anywhere in the US" names a global-sounding word but is still US-locked,
    # so an explicit lock beats the _GLOBAL_OK hint rather than tying with it.
    if _region_locked(loc):
        return False, f"remote but region-locked ({job.location})"
    if _GLOBAL_OK.search(loc):
        return True, "remote (global)"
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

    At a low cap (0-2 yrs) "take the smallest" gets leaky: a 5-7 yr role that
    mentions "2 years of scripting" would sneak through. We still keep it —
    silently dropping a real match is the worse error — but the note carries
    the higher numbers so a glance at the alert is enough to judge.
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
    if required > max_years:
        return False, f"needs {required}+ yrs"

    higher = sorted({n for n in mins if n > max_years})
    if higher:
        return True, f"min {required} yrs (also asks {'/'.join(map(str, higher))} — verify)"
    return True, f"min {required} yrs"


# ------------------------------------------------------------------ engine ---

@dataclass
class MatchConfig:
    keywords: list[str]
    max_experience_years: int = 6
    title_boost_keywords: list[str] = field(default_factory=list)
    exclude_titles: list[str] = field(default_factory=list)
    priority_boost: dict[str, int] = field(default_factory=dict)
    require_role_match: bool = True   # keyword must hit the title/department
    stretch_years: int = 3            # how far past the cap still counts as reachable


def build_exclude_pattern(words: list[str]) -> re.Pattern | None:
    """Titles that disqualify a job outright, regardless of what the body says.

    Experience parsing only sees numbers a posting bothered to state. Plenty of
    senior roles state none — for a 0-2 yr candidate the title is the more
    reliable signal, so it gets to veto.
    """
    cleaned = [re.escape(w.strip()).replace(r"\ ", r"[\s\-]+") for w in words if w.strip()]
    if not cleaned:
        return None
    return re.compile(rf"(?<![A-Za-z0-9])(?:{'|'.join(cleaned)})(?![A-Za-z0-9])", re.IGNORECASE)


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    txt = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


_YEARS_IN_NOTE = re.compile(r"needs (\d{1,2})\+ yrs")

# Workday reports age as prose ("Posted 3 Days Ago"), the JSON ATSs give an
# ISO date. Both are worth reading: response rates fall off a cliff once a
# posting has been up a week, so a fresh role you can apply to today beats a
# slightly better-matching one that has been collecting applicants for a month.
_POSTED_PROSE = re.compile(r"(\d+)\+?\s*day", re.IGNORECASE)
_POSTED_TODAY = re.compile(r"\b(today|just posted|yesterday)\b", re.IGNORECASE)


def days_since_posted(job: Job) -> int | None:
    """Age of the posting in days, or None if it can't be determined."""
    raw = (job.posted_at or "").strip()
    if not raw:
        return None
    if _POSTED_TODAY.search(raw):
        return 0
    m = _POSTED_PROSE.search(raw)
    if m:
        return int(m.group(1))
    try:
        posted = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return max((date.today() - posted).days, 0)


def freshness_boost(job: Job) -> int:
    age = days_since_posted(job)
    if age is None:
        return 0                 # unknown age is not penalised, just not boosted
    if age <= 3:
        return 5
    if age <= 7:
        return 3
    if age <= 14:
        return 1
    return 0


class Matcher:
    def __init__(self, cfg: MatchConfig):
        self.cfg = cfg
        self.patterns = build_keyword_patterns(cfg.keywords)
        self.exclude = build_exclude_pattern(cfg.exclude_titles)
        # Roles that cleared every gate except the year count, and missed it by
        # a little. At a 0-2 yr cap the honest match list is often empty for
        # days — not because the filter is broken but because the market is
        # thin. These are the "3-5 yrs" postings worth stretching for, and
        # surfacing them is the difference between a quiet feed and a dead one.
        self.stretch: list[Job] = []
        self._stretch_lock = threading.Lock()

    def evaluate(self, job: Job) -> bool:
        """Mutates job (matched_keywords, score, experience_note). Returns keep/drop."""
        job.description = clean_text(job.description)
        haystack_title = job.title or ""
        haystack_all = f"{haystack_title}\n{job.department}\n{job.description}"

        # Cheapest, most decisive gate first — skip the regex sweep on a title
        # that can never qualify.
        if self.exclude and self.exclude.search(haystack_title):
            return False

        # WHERE a keyword matches decides whether the job qualifies at all.
        #
        # Security vendors put "zero trust / cloud security / SASE" in the
        # About-us boilerplate of EVERY posting, so a description match says
        # nothing about the role. Measured on Zscaler's live board: every one
        # of its India openings hit 3-4 keywords in the description and zero in
        # the title — "Deputy Manager, Procurement" and "Employee Relations
        # Manager" scored exactly like a security engineering role.
        #
        # So the title or department has to carry the match. Description hits
        # still rank a job, they just can't qualify one on their own.
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

        ok_exp, note = experience_check(job, self.cfg.max_experience_years)
        job.experience_note = note

        if _SENIORITY_RED_FLAGS.search(haystack_title):
            score -= 2
        if loc_reason == "india":
            score += 2          # on-the-ground India beats a maybe-remote listing
        score += self.cfg.priority_boost.get(job.priority, 0)
        score += freshness_boost(job)   # apply-early beats apply-well
        job.score = score

        if not ok_exp:
            self._record_stretch(job, note)
            return False
        return True

    def _record_stretch(self, job: Job, note: str) -> None:
        """Keep a job that missed only on years, and only by a little."""
        m = _YEARS_IN_NOTE.search(note)
        if not m:
            return
        required = int(m.group(1))
        if required <= self.cfg.max_experience_years + self.cfg.stretch_years:
            with self._stretch_lock:
                self.stretch.append(job)
