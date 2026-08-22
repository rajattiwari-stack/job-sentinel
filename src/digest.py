"""Two nudges the raw alert feed can't give you.

1. STRETCH ROLES
   At a 0-2 year cap the honest match list is empty for days at a time. That
   isn't the filter misbehaving — early-career security roles in India are
   genuinely scarce — but a silent bot is indistinguishable from a broken one,
   and "needs 3 years" is a number plenty of people get hired past. So roles
   that cleared every gate except the year count, and missed by a little, go
   out as a separate clearly-labelled digest.

2. FOLLOW-UPS
   Applications die from silence more than from rejection. Any row you marked
   Applied = Yes that has been quiet for FOLLOW_UP_DAYS gets one reminder.

Both are rate-limited by state/run_meta.json so a 4x-daily bot doesn't send
the same digest four times a day, and both are strictly additive: neither can
suppress or delay a real job alert.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import Job

log = logging.getLogger("digest")

FOLLOW_UP_DAYS = 10          # silence after this long is worth a nudge
MAX_STRETCH_IN_DIGEST = 15
MAX_FOLLOW_UPS = 12


def _esc(s: str) -> str:
    import html
    return html.escape(s or "", quote=False)


# ------------------------------------------------------------ stretch roles ---

def should_send(meta_data: dict, key: str, every_hours: int = 20) -> bool:
    """True at most once per `every_hours`. Keeps a 4x-daily bot from repeating."""
    last = meta_data.get(key)
    if not last:
        return True
    try:
        return datetime.fromisoformat(last) + timedelta(hours=every_hours) <= datetime.now()
    except ValueError:
        return True


def mark_sent(meta_data: dict, key: str) -> None:
    meta_data[key] = datetime.now().isoformat(timespec="seconds")


def format_stretch(jobs: list[Job]) -> str:
    jobs = sorted(jobs, key=lambda j: (-j.score, j.company))[:MAX_STRETCH_IN_DIGEST]
    header = (
        f"📈 <b>Stretch roles</b> — {len(jobs)} role(s) just past your experience band.\n"
        "<i>Everything else matched: security title, India/remote, no seniority flag.</i>"
    )
    blocks = [
        f"🏢 <b>{_esc(j.company)}</b> — <a href=\"{_esc(j.url)}\">{_esc(j.title)}</a>\n"
        f"📍 {_esc(j.location or 'Location not listed')}\n"
        f"⏳ {_esc(j.experience_note)}"
        for j in jobs
    ]
    return "\n\n".join([header, *blocks])


# --------------------------------------------------------------- follow-ups ---

def find_follow_ups(tracker_path: Path, today: date | None = None) -> list[dict]:
    """Applied rows that have gone quiet. Never raises — this is a nicety."""
    today = today or date.today()
    try:
        from .tracker import _read_existing
        rows = _read_existing(Path(tracker_path))
    except Exception as e:                    # noqa: BLE001
        log.warning("Follow-up scan failed to read tracker: %s", e)
        return []

    due = []
    for r in rows:
        if (r.get("Applied?") or "").strip().lower() != "yes":
            continue
        raw = (r.get("Applied Date") or "").strip()
        if not raw:
            continue
        try:
            applied = date.fromisoformat(raw[:10])
        except ValueError:
            continue                          # hand-typed dates vary; skip quietly
        age = (today - applied).days
        if age >= FOLLOW_UP_DAYS:
            due.append({**r, "age_days": age})
    due.sort(key=lambda r: -r["age_days"])
    return due[:MAX_FOLLOW_UPS]


def format_follow_ups(rows: list[dict]) -> str:
    blocks = []
    for r in rows:
        link, title = r.get("Link", ""), r.get("Position", "")
        shown = f"<a href=\"{_esc(link)}\">{_esc(title)}</a>" if link else _esc(title)
        blocks.append(f"🏢 <b>{_esc(r.get('Company', ''))}</b> — {shown}\n"
                      f"📨 applied {r['age_days']} days ago ({_esc(r.get('Applied Date', ''))})")
    header = f"⏰ <b>Follow up</b> — {len(rows)} application(s) with no word back."
    footer = "<i>Mark the row's Notes in tracker.xlsx once you've chased it.</i>"
    return "\n\n".join([header, *blocks, footer])
