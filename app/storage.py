"""SQLite connection ownership and schema. Network calls never hold a transaction."""

import fcntl
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from app.scheduling import SCHEMA as SCHEDULE_SCHEMA

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY, username TEXT NOT NULL, name TEXT NOT NULL,
    tokens TEXT, token_expires REAL NOT NULL DEFAULT 0,
    refresh_state TEXT NOT NULL DEFAULT 'ready' CHECK(refresh_state IN ('ready','refreshing'))
);
CREATE TABLE IF NOT EXISTS sessions (
    digest TEXT PRIMARY KEY, csrf TEXT NOT NULL, user_id TEXT REFERENCES accounts(id),
    created REAL NOT NULL, touched REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth (
    state TEXT PRIMARY KEY, session TEXT NOT NULL REFERENCES sessions(digest) ON DELETE CASCADE,
    verifier TEXT NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS filters (
    kind TEXT NOT NULL CHECK(kind IN ('site','keyword')), value TEXT NOT NULL,
    PRIMARY KEY(kind,value)
);
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, created_at TEXT NOT NULL,
    urls TEXT NOT NULL, search_text TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','deleted','unavailable'))
);
CREATE TABLE IF NOT EXISTS quota (
    user_id TEXT PRIMARY KEY REFERENCES accounts(id),
    limit_value INTEGER NOT NULL CHECK(limit_value > 0),
    remaining INTEGER NOT NULL CHECK(remaining >= 0),
    resets REAL NOT NULL, observed REAL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES accounts(id),
    request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('draft','running','paused','completed','cancelled')),
    created REAL NOT NULL, requested INTEGER NOT NULL CHECK(requested > 0),
    note TEXT NOT NULL DEFAULT '', UNIQUE(user_id,request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_run ON runs(user_id)
    WHERE status IN ('draft','running','paused');
CREATE TABLE IF NOT EXISTS run_items (
    run_id TEXT NOT NULL REFERENCES runs(id), post_id TEXT NOT NULL REFERENCES posts(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','sending','deleted','unavailable','failed','unknown')),
    message TEXT NOT NULL DEFAULT '', PRIMARY KEY(run_id,post_id)
);
CREATE TABLE IF NOT EXISTS budgets (
    key TEXT PRIMARY KEY, window INTEGER NOT NULL, count INTEGER NOT NULL CHECK(count > 0)
);
"""


SYNC_SCHEMA = """
CREATE TABLE IF NOT EXISTS post_sources (
    post_id TEXT PRIMARY KEY REFERENCES posts(id),
    archive_seen INTEGER NOT NULL DEFAULT 0 CHECK(archive_seen IN (0,1)),
    live_seen INTEGER NOT NULL DEFAULT 0 CHECK(live_seen IN (0,1)),
    remote_state TEXT NOT NULL DEFAULT 'unchecked'
        CHECK(remote_state IN ('unchecked','present','unavailable','not_owned')),
    checked_at REAL
);
INSERT OR IGNORE INTO post_sources(post_id,archive_seen) SELECT id,1 FROM posts;
CREATE TABLE IF NOT EXISTS post_sync_auto (
    session TEXT PRIMARY KEY REFERENCES sessions(digest) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES accounts(id), attempted REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS post_sync (
    user_id TEXT PRIMARY KEY REFERENCES accounts(id),
    status TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle','running','error')),
    claim TEXT, cursor TEXT, attempted REAL, completed REAL,
    pages INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '', checked_count INTEGER NOT NULL DEFAULT 0,
    retry_after REAL NOT NULL DEFAULT 0
);
"""


class Database:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "tweet-deleter.sqlite3"

    def initialize(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3):
                raise ValueError("Unsupported database schema version")
            db.execute("PRAGMA journal_mode=WAL")
            if version in (1, 2):
                self._backup_before_upgrade(db)
            db.executescript(
                "BEGIN IMMEDIATE;\n"
                + SCHEMA
                + SYNC_SCHEMA
                + SCHEDULE_SCHEMA
                + "\nPRAGMA user_version=3;\nCOMMIT;"
            )

    def _backup_before_upgrade(self, source: sqlite3.Connection) -> None:
        directory = self.directory / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"before-schema-v3-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        destination = sqlite3.connect(path)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Private pre-upgrade backup validation failed")
        finally:
            destination.close()

    @contextmanager
    def runtime(self) -> Iterator[None]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory.chmod(0o700)
        fd = os.open(self.directory / "runtime.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(
                    "Another Tweet Deleter process is using this data directory."
                ) from exc
            yield
        finally:
            os.close(fd)

    @contextmanager
    def connect(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def budget(self, key: str, maximum: int, seconds: int, now: float) -> bool:
        window = int(now // seconds)
        with self.connect(write=True) as db:
            db.execute(
                "INSERT INTO budgets VALUES(?,?,1) ON CONFLICT(key) DO UPDATE SET "
                "count=CASE WHEN budgets.window=excluded.window THEN count+1 ELSE 1 END, "
                "window=excluded.window",
                (key, window),
            )
            count = db.execute("SELECT count FROM budgets WHERE key=?", (key,)).fetchone()[0]
            db.execute("DELETE FROM budgets WHERE window < ?", (int(now // 600) - 2,))
        return bool(count <= maximum)
