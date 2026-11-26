"""Durable batch pacing for explicitly confirmed deletion snapshots."""

import sqlite3
import time
from typing import Any

from app.config import Settings
from app.errors import AppError

SCHEMA = """
CREATE TABLE IF NOT EXISTS run_schedule (
    run_id TEXT PRIMARY KEY REFERENCES runs(id),
    cap INTEGER NOT NULL CHECK(cap BETWEEN 1 AND 10000),
    batch_size INTEGER NOT NULL CHECK(batch_size BETWEEN 1 AND 1000 AND batch_size <= cap),
    batch_used INTEGER NOT NULL DEFAULT 0 CHECK(batch_used >= 0),
    window_started REAL NOT NULL DEFAULT 0,
    waiting_until REAL NOT NULL DEFAULT 0,
    cooldown_source TEXT NOT NULL DEFAULT 'estimate'
        CHECK(cooldown_source IN ('estimate','x_response'))
);
"""


class Schedules:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def validate(self, cap: int, batch_size: int) -> None:
        if type(cap) is not int or not 1 <= cap <= self.settings.schedule_max:
            raise AppError(f"Choose a post cap between 1 and {self.settings.schedule_max}.")
        if type(batch_size) is not int or not 1 <= batch_size <= self.settings.run_max:
            raise AppError(f"Choose a batch size between 1 and {self.settings.run_max}.")
        if batch_size > cap:
            raise AppError("Batch size cannot exceed the post cap.")

    @staticmethod
    def get(db: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
        row = db.execute("SELECT * FROM run_schedule WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def wait(self, db: sqlite3.Connection, run_id: str, quota: dict[str, Any]) -> None:
        plan = self.get(db, run_id)
        assert plan is not None
        now = time.time()
        reset = quota["resets"] or plan["window_started"] + self.settings.quota_window
        db.execute(
            "UPDATE run_schedule SET waiting_until=?,cooldown_source=? WHERE run_id=?",
            (max(now + 1, reset), quota["source"], run_id),
        )
        db.execute(
            "UPDATE runs SET note='Waiting for the next X rate window.' WHERE id=? AND status='running'",
            (run_id,),
        )

    def admit(self, db: sqlite3.Connection, run_id: str, quota: dict[str, Any]) -> bool:
        plan = self.get(db, run_id)
        if not plan:
            return True
        now = time.time()
        if plan["waiting_until"]:
            if now < plan["waiting_until"]:
                return False
            db.execute(
                "UPDATE run_schedule SET batch_used=0,waiting_until=0,window_started=0 WHERE run_id=?",
                (run_id,),
            )
            plan["batch_used"] = 0
        if (
            plan["batch_used"] >= min(plan["batch_size"], self.settings.run_max)
            or quota["remaining"] <= 0
        ):
            self.wait(db, run_id, quota)
            return False
        db.execute("UPDATE runs SET note='' WHERE id=? AND status='running'", (run_id,))
        return True

    @staticmethod
    def claim(db: sqlite3.Connection, run_id: str) -> None:
        db.execute(
            "UPDATE run_schedule SET batch_used=batch_used+1,"
            "window_started=CASE WHEN batch_used=0 THEN ? ELSE window_started END WHERE run_id=?",
            (time.time(), run_id),
        )
