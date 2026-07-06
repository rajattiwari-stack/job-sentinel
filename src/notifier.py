"""Delivery channels: Telegram (primary) and SMTP email (optional fallback).

Edge cases handled:
- Telegram hard-caps messages at 4096 chars → we chunk on job boundaries.
- Job titles routinely contain Markdown-hostile chars ((), [], -, |) →
  we send HTML parse mode and escape, which is far more robust than MarkdownV2.
- Telegram rate limit (~30 msgs/sec, 20/min per group) → small sleep per chunk.
- Notifier failures NEVER crash the run; jobs stay unmarked so they retry
  next run (delivery is at-least-once by design).
"""
from __future__ import annotations

import html
import logging
import os
import smtplib
import time
from email.mime.text import MIMEText

import requests

from .models import Job

log = logging.getLogger("notify")

TG_LIMIT = 4000  # headroom under the 4096 hard cap


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def format_job_html(j: Job) -> str:
    kw = ", ".join(j.matched_keywords[:6])
    lines = [
        f"🏢 <b>{_esc(j.company)}</b> — <a href=\"{_esc(j.url)}\">{_esc(j.title)}</a>",
        f"📍 {_esc(j.location or 'Location not listed')}",
        f"🎯 {_esc(kw)}   ⏳ {_esc(j.experience_note)}",
    ]
    if j.posted_at:
        lines.append(f"🗓 {_esc(j.posted_at)}")
    return "\n".join(lines)


def chunk_messages(header: str, blocks: list[str], limit: int = TG_LIMIT) -> list[str]:
    msgs, cur = [], header
    for b in blocks:
        if len(b) > limit:                       # pathological single block
            b = b[: limit - 20] + "…"
        if len(cur) + len(b) + 2 > limit:
            msgs.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        msgs.append(cur)
    return msgs


def _chat_ids() -> list[str]:
    """TELEGRAM_CHAT_ID supports multiple recipients: '12345,67890' (you + friend)."""
    raw = os.environ.get("TELEGRAM_CHAT_ID", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def send_telegram(jobs: list[Job]) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = _chat_ids()
    if not token or not chats:
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return False

    header = f"🔔 <b>Job Sentinel</b> — {len(jobs)} new cybersecurity role(s)"
    blocks = [format_job_html(j) for j in jobs]
    ok = False
    for chat_id in chats:
        chat_ok = True
        for msg in chunk_messages(header, blocks):
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                    timeout=20,
                )
                if r.status_code == 429:
                    retry = (r.json().get("parameters") or {}).get("retry_after", 3)
                    time.sleep(retry + 1)
                    r = requests.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML",
                              "disable_web_page_preview": True},
                        timeout=20,
                    )
                r.raise_for_status()
                time.sleep(1.2)
            except Exception as e:  # noqa: BLE001 — delivery must not kill the run
                log.error("Telegram send to %s failed: %s", chat_id, e)
                chat_ok = False
        ok = ok or chat_ok   # success if AT LEAST one recipient got it
    return ok


def send_telegram_excel(filepath: str, caption: str) -> bool:
    """Send the compiled Excel as a document to every configured chat.

    Telegram bot upload cap is 50 MB — our workbook is a few hundred KB, safe.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = _chat_ids()
    if not token or not chats:
        return False
    ok = False
    for chat_id in chats:
        try:
            with open(filepath, "rb") as fh:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendDocument",
                    data={"chat_id": chat_id, "caption": caption[:1000]},
                    files={"document": (os.path.basename(filepath), fh,
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                    timeout=60,
                )
            r.raise_for_status()
            ok = True
            time.sleep(1.2)
        except Exception as e:  # noqa: BLE001
            log.error("Excel send to %s failed: %s", chat_id, e)
    return ok


def send_email(jobs: list[Job]) -> bool:
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        return False
    user = os.environ.get("SMTP_USER", "")
    pwd = os.environ.get("SMTP_PASS", "")
    to = os.environ.get("EMAIL_TO", user)
    try:
        body = "\n\n".join(
            f"{j.company} — {j.title}\n{j.location}\nKeywords: {', '.join(j.matched_keywords)}"
            f"\nExperience: {j.experience_note}\n{j.url}"
            for j in jobs
        )
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = f"Job Sentinel: {len(jobs)} new cybersecurity roles"
        msg["From"], msg["To"] = user, to
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(user, pwd)
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001
        log.error("Email send failed: %s", e)
        return False


def notify(jobs: list[Job]) -> bool:
    """Returns True if at least one channel delivered."""
    if not jobs:
        return True
    tg = send_telegram(jobs)
    em = send_email(jobs)
    return tg or em
