"""Durable owner consent for optional post reads, serialized with read requests."""

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from app.errors import AppError
from app.storage import Database


class PostReads:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.lock = threading.RLock()

    def _allowed(self, connection: sqlite3.Connection, user_id: str) -> bool:
        owner = connection.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
        if not owner or owner[0] != user_id:
            raise AppError("This setting belongs to the installation owner.", 403)
        row = connection.execute(
            "SELECT value FROM meta WHERE key=?", ("allow_post_reads:" + user_id,)
        ).fetchone()
        return bool(row and row[0] == "1")

    def allowed(self, user_id: str) -> bool:
        with self.db.connect() as connection:
            return self._allowed(connection, user_id)

    def set_allowed(self, user_id: str, value: bool) -> dict[str, bool]:
        with self.lock, self.db.connect(write=True) as connection:
            self._allowed(connection, user_id)
            connection.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("allow_post_reads:" + user_id, "1" if value else "0"),
            )
        return {"allow_post_reads": value}

    def require(self, user_id: str) -> None:
        if not self.allowed(user_id):
            raise AppError("X post reads are blocked. Enable them in Settings to continue.", 403)

    @contextmanager
    def request(self, user_id: str) -> Iterator[None]:
        # Disabling waits for an admitted read; no database transaction spans HTTP.
        with self.lock:
            self.require(user_id)
            yield
