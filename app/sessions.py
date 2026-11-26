"""Opaque, revocable browser sessions. Only token digests are stored."""

import hashlib
import secrets
import time
from dataclasses import dataclass

from app.config import Settings
from app.storage import Database

COOKIE = "td_session"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class Session:
    digest: str
    csrf: str
    user_id: str | None
    created: float


class Sessions:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def create(
        self, user_id: str | None = None, previous: str | None = None
    ) -> tuple[str, Session]:
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        key = digest(token)
        with self.db.connect(write=True) as db:
            db.execute(
                "DELETE FROM sessions WHERE expires < ? OR touched < ?",
                (now, now - self.settings.session_idle),
            )
            if previous:
                db.execute("DELETE FROM sessions WHERE digest=?", (previous,))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?)",
                (key, csrf, user_id, now, now, now + self.settings.session_lifetime),
            )
        return token, Session(key, csrf, user_id, now)

    def get(self, token: str | None, *, activity: bool = False) -> Session | None:
        if not token or len(token) > 128:
            return None
        now = time.time()
        with self.db.connect(write=activity) as db:
            row = db.execute(
                "SELECT s.* FROM sessions s LEFT JOIN accounts a ON a.id=s.user_id "
                "WHERE digest=? AND expires>? AND touched>? "
                "AND (s.user_id IS NULL OR a.tokens IS NOT NULL)",
                (digest(token), now, now - self.settings.session_idle),
            ).fetchone()
            if not row:
                return None
            if activity:
                db.execute("UPDATE sessions SET touched=? WHERE digest=?", (now, row["digest"]))
        return Session(row["digest"], row["csrf"], row["user_id"], row["created"])
