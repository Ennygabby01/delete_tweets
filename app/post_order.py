"""Owner preference and immutable ordering for reviewed run snapshots."""

import sqlite3

from app.errors import AppError
from app.storage import Database


def direction(value: str) -> str:
    # SQL directions cannot be bound parameters; only these constants are allowed.
    if value not in {"newest", "oldest"}:
        raise AppError("Choose newest first or oldest first.")
    return "ASC" if value == "oldest" else "DESC"


class PostOrder:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def current(db: sqlite3.Connection, user_id: str) -> str:
        row = db.execute(
            "SELECT value FROM meta WHERE key=?", ("post_order:" + user_id,)
        ).fetchone()
        return row[0] if row else "newest"

    @staticmethod
    def for_run(db: sqlite3.Connection, run_id: str) -> str:
        row = db.execute("SELECT value FROM meta WHERE key=?", ("run_order:" + run_id,)).fetchone()
        return row[0] if row else "newest"

    def get(self, user_id: str) -> str:
        with self.db.connect() as db:
            return self.current(db, user_id)

    def save(self, user_id: str, value: str) -> dict[str, str]:
        direction(value)
        with self.db.connect(write=True) as db:
            db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("post_order:" + user_id, value),
            )
        return {"post_order": value}
