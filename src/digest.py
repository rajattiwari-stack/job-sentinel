"""Application follow-up reminders read from tracker.xlsx."""
from __future__ import annotations

import html
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

log = logging.getLogger("digest")

FOLLOW_UP_DAYS = 10
MAX_FOLLOW_UPS = 12


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def should_send(meta_data: dict, key: str, every_hours: int = 20) -> bool:
    last = meta_data.get(key)
    if not last:
        return True
    try:
        return datetime.fromisoformat(last) + timedelta(hours=every_hours) <= datetime.now()
    except ValueError:
        return True


def mark_sent(meta_data: dict, key: str) -> None:
    meta_data[key] = datetime.now().isoformat(timespec="seconds")


def find_follow_ups(tracker_path: Path, today: date | None = None) -> list[dict]:
    today = today or date.today()
    try:
        from .tracker import _read_existing
        rows = _read_existing(Path(tracker_path))
    except Exception as e:  # noqa: BLE001
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
            continue
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
