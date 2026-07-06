"""Seen-jobs state: guarantees each job is notified exactly once.

Stored as JSON committed back to the repo by the GitHub Action, so state
survives between runs with zero external infrastructure.

Edge cases:
- Atomic write (tmp + os.replace) so a crashed run can't corrupt state.
- Pruning: entries older than RETENTION_DAYS are dropped so the file
  doesn't grow forever. If a pruned job is still live it may re-notify
  once after 90 days — acceptable tradeoff, and it re-surfaces stale
  postings you may have missed.
- Corrupt/missing file => start fresh (first run notifies everything once).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("state")

RETENTION_DAYS = 90


class SeenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._data: dict[str, str] = {}   # fingerprint -> ISO date first seen
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text("utf-8"))
            if not isinstance(self._data, dict):
                raise ValueError("state root must be an object")
        except FileNotFoundError:
            log.info("No state file at %s — first run.", self.path)
            self._data = {}
        except (ValueError, json.JSONDecodeError) as e:
            log.error("Corrupt state file (%s) — starting fresh.", e)
            self._data = {}

    def is_new(self, fingerprint: str) -> bool:
        return fingerprint not in self._data

    def mark(self, fingerprint: str) -> None:
        self._data[fingerprint] = datetime.now(timezone.utc).date().isoformat()

    def prune(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).date().isoformat()
        before = len(self._data)
        self._data = {k: v for k, v in self._data.items() if v >= cutoff}
        return before - len(self._data)

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=0, sort_keys=True), "utf-8")
        os.replace(tmp, self.path)
        log.info("State saved: %d fingerprints.", len(self._data))
