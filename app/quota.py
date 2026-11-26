"""Persisted, conservative deletion budget; provider observations take priority."""

import sqlite3
import time
from typing import Any

import httpx

from app.config import Settings
from app.storage import Database


class Quota:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db, self.settings = db, settings

    def current(self, db: sqlite3.Connection, user_id: str) -> dict[str, Any]:
        now = time.time()
        row = db.execute("SELECT * FROM quota WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            return {
                "limit": self.settings.fallback_limit,
                "remaining": self.settings.fallback_limit,
                "resets": None,
                "source": "estimate",
            }
        expired = row["resets"] <= now
        return {
            "limit": row["limit_value"],
            "remaining": row["limit_value"] if expired else row["remaining"],
            "resets": None if expired else row["resets"],
            "source": "estimate" if expired or row["observed"] is None else "x_response",
        }

    def get(self, user_id: str) -> dict[str, Any]:
        with self.db.connect() as db:
            quota = self.current(db, user_id)
        return {
            **quota,
            "run_max": self.settings.run_max,
            "can_plan": min(quota["remaining"], self.settings.run_max),
        }

    def consume(self, db: sqlite3.Connection, user_id: str) -> bool:
        value = self.current(db, user_id)
        if value["remaining"] <= 0:
            return False
        reset = value["resets"] or time.time() + self.settings.quota_window
        # Claim is committed with the sending item before the remote effect.
        db.execute(
            "INSERT INTO quota VALUES(?,?,?,?,NULL) ON CONFLICT(user_id) DO UPDATE SET "
            "limit_value=excluded.limit_value,remaining=excluded.remaining, "
            "observed=CASE WHEN quota.resets<=? THEN NULL ELSE quota.observed END,resets=excluded.resets",
            (user_id, value["limit"], value["remaining"] - 1, reset, time.time()),
        )
        return True

    def observe(
        self, db: sqlite3.Connection, user_id: str, headers: httpx.Headers, status: int
    ) -> None:
        now = time.time()
        current = self.current(db, user_id)
        try:
            limit = int(headers["x-rate-limit-limit"])
            remaining = int(headers["x-rate-limit-remaining"])
            reset = float(headers["x-rate-limit-reset"])
            valid = (
                0 < limit <= 1_000_000
                and 0 <= remaining <= limit
                and now - 1 <= reset <= now + 172800
            )
        except (KeyError, ValueError, OverflowError):
            valid = False
        if valid:
            # Observed remaining can be lower due to another client. Never undo
            # locally reserved budget in the same window with a late observation.
            if current["resets"] is not None and abs(reset - current["resets"]) <= 2:
                remaining = min(remaining, current["remaining"])
            if status == 429:
                remaining = 0
            db.execute(
                "INSERT INTO quota VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "limit_value=excluded.limit_value,remaining=excluded.remaining,"
                "resets=excluded.resets,observed=excluded.observed",
                (user_id, limit, remaining, max(now + 1, reset), now),
            )
        elif status == 429:
            db.execute(
                "INSERT INTO quota VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                "remaining=0,resets=excluded.resets,observed=excluded.observed",
                (user_id, current["limit"], 0, now + self.settings.quota_window, now),
            )
