"""Validated archive import and parameterized queries over local post candidates."""

import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.errors import AppError
from app.post_order import direction
from app.storage import Database

ID_PATTERN = r"[0-9]{1,25}"


def valid_id(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(ID_PATTERN, value) is not None


def parse_archive(raw: bytes) -> list[tuple[str, str, str, str, str]]:
    try:
        content = raw.decode("utf-8-sig").strip()
        content = re.sub(r"^window\.YTD\.tweets\.\w+\s*=\s*", "", content).removesuffix(";").strip()
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise AppError("Choose a valid UTF-8 tweets.js file from your X archive.") from exc
    if not isinstance(payload, list) or len(payload) > 500_000:
        raise AppError("Archive must contain a supported list of posts.")
    posts, seen = [], set()
    for item in payload:
        tweet = item.get("tweet") if isinstance(item, dict) else None
        if not isinstance(tweet, dict):
            raise AppError("Archive contains an invalid post record.")
        uid, text, date = (
            tweet.get("id_str"),
            tweet.get("full_text", ""),
            tweet.get("created_at", ""),
        )
        if (
            not isinstance(uid, str)
            or not valid_id(uid)
            or not isinstance(text, str)
            or len(text) > 100_000
            or not isinstance(date, str)
            or len(date) > 128
        ):
            raise AppError("Archive contains an invalid post record.")
        entities = tweet.get("entities", {})
        urls = entities.get("urls", []) if isinstance(entities, dict) else None
        if not isinstance(urls, list) or len(urls) > 1000:
            raise AppError("Archive contains invalid links.")
        safe_urls = []
        for entry in urls:
            if not isinstance(entry, dict):
                raise AppError("Archive contains invalid links.")
            link = entry.get("expanded_url", "")
            if not isinstance(link, str) or len(link) > 8192:
                raise AppError("Archive contains invalid links.")
            try:
                parsed = urlsplit(link)
                if (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                ):
                    safe_urls.append(link)
            except ValueError:
                continue
        if uid in seen:
            continue
        seen.add(uid)
        posts.append(
            (uid, text, date, json.dumps(safe_urls), (text + " " + " ".join(safe_urls)).lower())
        )
    return posts


def like(value: str) -> str:
    return "%" + value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Archive:
    def __init__(self, db: Database) -> None:
        self.db = db

    def import_bytes(self, raw: bytes) -> int:
        posts = parse_archive(raw)
        with self.db.connect(write=True) as db:
            if db.execute(
                "SELECT 1 FROM runs WHERE status IN ('draft','running','paused')"
            ).fetchone():
                raise AppError("Finish or cancel the current run before importing an archive.", 409)
            # Re-import updates content but never resets known deletion state.
            db.executemany(
                "INSERT INTO posts(id,text,created_at,urls,search_text) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET text=excluded.text,created_at=excluded.created_at,"
                "urls=excluded.urls,search_text=excluded.search_text "
                "WHERE NOT EXISTS (SELECT 1 FROM post_sources WHERE post_id=posts.id AND live_seen=1)",
                posts,
            )
            db.executemany(
                "INSERT INTO post_sources(post_id,archive_seen) VALUES(?,1) "
                "ON CONFLICT(post_id) DO UPDATE SET archive_seen=1",
                [(post[0],) for post in posts],
            )
            db.execute("INSERT OR REPLACE INTO meta VALUES('archive_imported','1')")
        return len(posts)

    def migrate_legacy(self, root: Path, limit: int) -> None:
        with self.db.connect() as db:
            done = db.execute("SELECT 1 FROM meta WHERE key='legacy_imported'").fetchone()
        if done:
            return
        archive = root / "tweets.js"
        if archive.exists():
            if archive.stat().st_size > limit:
                raise AppError(
                    "Existing archive exceeds MAX_ARCHIVE_BYTES. Adjust your private configuration.",
                    503,
                )
            self.import_bytes(archive.read_bytes())
        path = root / "filters.json"
        if path.exists():
            try:
                if path.stat().st_size > 65536:
                    raise ValueError("oversized filters")
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("invalid filters")
                for source, kind in (("sites", "site"), ("keywords", "keyword")):
                    values = data.get(source, [])
                    if not isinstance(values, list) or len(values) > 100:
                        raise ValueError("invalid filters")
                    for value in values:
                        self.add_filter(kind, value)
            except (ValueError, AppError) as exc:
                raise AppError(
                    "Saved filters could not be imported. Check the local filters file.", 503
                ) from exc
        with self.db.connect(write=True) as db:
            db.execute("INSERT INTO meta VALUES('legacy_imported','1')")

    def filters(self) -> dict[str, list[str]]:
        with self.db.connect() as db:
            rows = db.execute("SELECT kind,value FROM filters ORDER BY kind,value").fetchall()
        return {
            "sites": [r["value"] for r in rows if r["kind"] == "site"],
            "keywords": [r["value"] for r in rows if r["kind"] == "keyword"],
        }

    def add_filter(self, kind: str, value: str) -> None:
        if (
            kind not in {"site", "keyword"}
            or not isinstance(value, str)
            or not 1 <= len(value.strip()) <= 253
        ):
            raise AppError("Enter a domain or keyword of 1–253 characters.")
        value = value.strip()
        if kind == "site":
            value = value.lower()
            if (
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value)
                or "." not in value
                or ".." in value
            ):
                raise AppError("Enter a domain such as example.com, without a path.")
        with self.db.connect(write=True) as db:
            if db.execute("SELECT count(*) FROM filters").fetchone()[0] >= 100:
                raise AppError("The maximum number of filters has been reached.")
            db.execute("INSERT OR IGNORE INTO filters VALUES(?,?)", (kind, value))

    def remove_filter(self, kind: str, value: str) -> None:
        with self.db.connect(write=True) as db:
            db.execute("DELETE FROM filters WHERE kind=? AND value=?", (kind, value))

    def where(self, db: sqlite3.Connection, search: str = "") -> tuple[str, list[str]]:
        filters = db.execute("SELECT value FROM filters").fetchall()
        clauses = [
            "status='available' AND NOT EXISTS (SELECT 1 FROM run_items i WHERE i.post_id=posts.id AND i.status IN ('sending','unknown'))"
        ]
        args: list[str] = []
        if filters:
            clauses.append(
                "(" + " OR ".join("search_text LIKE ? ESCAPE '\\'" for _ in filters) + ")"
            )
            args.extend(like(row[0]) for row in filters)
        if search:
            clauses.append("search_text LIKE ? ESCAPE '\\'")
            args.append(like(search))
        return " AND ".join(clauses), args

    def list(
        self, page: int, limit: int, search: str, post_order: str = "newest"
    ) -> dict[str, Any]:
        order = direction(post_order)
        with self.db.connect() as db:
            where, args = self.where(db, search)
            total = db.execute("SELECT count(*) FROM posts WHERE " + where, args).fetchone()[0]
            rows = db.execute(
                "SELECT id,text,created_at,urls, "
                "(SELECT archive_seen FROM post_sources WHERE post_id=posts.id) archive_seen, "
                "(SELECT live_seen FROM post_sources WHERE post_id=posts.id) live_seen, "
                "(SELECT remote_state FROM post_sources WHERE post_id=posts.id) remote_state, "
                "(SELECT checked_at FROM post_sources WHERE post_id=posts.id) checked_at FROM posts WHERE "
                + where
                + f" ORDER BY length(id) {order},id {order} LIMIT ? OFFSET ?",
                [*args, limit, (page - 1) * limit],
            ).fetchall()
        return {
            "total": total,
            "page": page,
            "pages": max(1, (total + limit - 1) // limit),
            "posts": [
                {**dict(row), "text": html.unescape(row["text"]), "urls": json.loads(row["urls"])}
                for row in rows
            ],
        }

    def stats(self) -> dict[str, int]:
        with self.db.connect() as db:
            where, args = self.where(db)
            return {
                "total": db.execute("SELECT count(*) FROM posts").fetchone()[0],
                "available": db.execute(
                    "SELECT count(*) FROM posts WHERE status='available'"
                ).fetchone()[0],
                "matched": db.execute("SELECT count(*) FROM posts WHERE " + where, args).fetchone()[
                    0
                ],
                "deleted": db.execute(
                    "SELECT count(*) FROM posts WHERE status='deleted'"
                ).fetchone()[0],
                "unavailable": db.execute(
                    "SELECT count(*) FROM posts WHERE status='unavailable'"
                ).fetchone()[0],
            }
