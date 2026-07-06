"""Hardened HTTP client shared by all adapters.

Production concerns handled here:
- Retries with exponential backoff + jitter on 429/5xx and connection errors.
- Per-request timeout (a hung socket must never stall the whole run).
- Respect Retry-After headers on 429.
- A realistic User-Agent (some ATS endpoints 403 the default python-requests UA).
- Small politeness delay between calls to the same host.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any, Optional
from urllib.parse import urlparse

import requests

log = logging.getLogger("http")

DEFAULT_TIMEOUT = 25          # seconds
MAX_RETRIES = 4
BACKOFF_BASE = 1.6
PER_HOST_DELAY = 0.8          # polite gap between hits to the same host

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 JobSentinel/1.0"
)

_last_hit: dict[str, float] = {}


class HttpError(Exception):
    pass


def _politeness_wait(url: str) -> None:
    host = urlparse(url).netloc
    last = _last_hit.get(host, 0.0)
    wait = PER_HOST_DELAY - (time.monotonic() - last)
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.monotonic()


def request_json(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    """GET/POST a URL and return parsed JSON, with retries. Raises HttpError on final failure."""
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)

    last_err: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        _politeness_wait(url)
        try:
            resp = requests.request(
                method, url, params=params, json=json_body,
                headers=hdrs, timeout=timeout,
            )
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                delay = float(retry_after) if (retry_after or "").isdigit() else BACKOFF_BASE ** attempt
                log.warning("429 from %s — sleeping %.1fs (attempt %d)", url, delay, attempt)
                time.sleep(delay + random.uniform(0, 0.5))
                continue
            if 500 <= resp.status_code < 600:
                raise HttpError(f"HTTP {resp.status_code}")
            if resp.status_code in (401, 403, 404):
                # Permanent for this run — do not retry, report upward.
                raise HttpError(f"HTTP {resp.status_code} (permanent) for {url}")
            resp.raise_for_status()
            return resp.json()
        except HttpError as e:
            if "(permanent)" in str(e):
                raise
            last_err = e
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            last_err = e

        sleep = BACKOFF_BASE ** attempt + random.uniform(0, 0.7)
        log.warning("Retry %d/%d for %s after error: %s (sleep %.1fs)",
                    attempt, MAX_RETRIES, url, last_err, sleep)
        time.sleep(sleep)

    raise HttpError(f"Giving up on {url}: {last_err}")


def get_json(url: str, **kw: Any) -> Any:
    return request_json("GET", url, **kw)


def post_json(url: str, json_body: dict, **kw: Any) -> Any:
    return request_json("POST", url, json_body=json_body, **kw)
