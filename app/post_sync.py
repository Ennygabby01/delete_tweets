"""Bounded account timeline import and explicit local-post verification."""

import json
import time
import uuid
from typing import Any, Literal

from app.archive import parse_archive, valid_id
from app.auth import Auth
from app.config import Settings
from app.errors import AppError
from app.post_reads import PostReads
from app.provider import Reply, XClient
from app.storage import Database

SyncMode = Literal["auto", "recent", "older", "check"]


class PostSync:
    def __init__(
        self, db: Database, auth: Auth, provider: XClient, settings: Settings, reads: PostReads
    ) -> None:
        self.db, self.auth, self.provider, self.settings = db, auth, provider, settings
        self.reads = reads

    def recover(self) -> None:
        with self.db.connect(write=True) as db:
            db.execute(
                "UPDATE post_sync SET status='error',claim=NULL,"
                "message='The app restarted during sync. Choose Sync from X to try again.' "
                "WHERE status='running'"
            )

    def state(self, user_id: str, session_digest: str | None = None) -> dict[str, Any]:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM post_sync WHERE user_id=?", (user_id,)).fetchone()
            auto_attempt = db.execute(
                "SELECT 1 FROM post_sync_auto WHERE session=? AND user_id=?",
                (session_digest, user_id),
            ).fetchone()
            counts = db.execute(
                "SELECT count(*) total, "
                "sum(remote_state='present' AND p.status='available') confirmed, "
                "sum(remote_state='unchecked' AND p.status='available') unchecked "
                "FROM post_sources s JOIN posts p ON p.id=s.post_id"
            ).fetchone()
        return {
            "status": row["status"] if row else "idle",
            "can_auto_sync": self.reads.allowed(user_id)
            and self.settings.auto_sync
            and session_digest is not None
            and not auto_attempt,
            "has_older": bool(row and row["cursor"]),
            "attempted": row["attempted"] if row else None,
            "completed": row["completed"] if row else None,
            "message": row["message"] if row else "",
            "fetched": row["fetched"] if row else 0,
            "pages": row["pages"] if row else 0,
            "checked_count": row["checked_count"] if row else 0,
            "retry_after": row["retry_after"] if row else 0,
            "page_size": self.settings.sync_page_size,
            "confirmed": counts["confirmed"] or 0,
            "unchecked": counts["unchecked"] or 0,
        }

    def _claim(
        self, user_id: str, mode: SyncMode, session_digest: str | None
    ) -> tuple[str, str | None] | None:
        now = time.time()
        with self.db.connect(write=True) as db:
            if not db.execute(
                "SELECT 1 FROM accounts WHERE id=? AND tokens IS NOT NULL", (user_id,)
            ).fetchone():
                raise AppError("Connect your X account again.", 401)
            db.execute("INSERT OR IGNORE INTO post_sync(user_id) VALUES(?)", (user_id,))
            row = db.execute("SELECT * FROM post_sync WHERE user_id=?", (user_id,)).fetchone()
            if mode == "auto":
                if not self.settings.auto_sync:
                    return None
                if (
                    not session_digest
                    or not db.execute(
                        "SELECT 1 FROM sessions WHERE digest=? AND user_id=? AND expires>?",
                        (session_digest, user_id, now),
                    ).fetchone()
                ):
                    raise AppError("Sign in before syncing posts.", 401)
                if db.execute(
                    "SELECT 1 FROM post_sync_auto WHERE session=?", (session_digest,)
                ).fetchone():
                    return None
            if db.execute(
                "SELECT 1 FROM runs WHERE status IN ('draft','running','paused')"
            ).fetchone():
                if mode == "auto":
                    return None
                raise AppError("Finish or cancel the current run before syncing posts.", 409)
            if row["status"] == "running":
                raise AppError("A post sync is already in progress.", 409)
            if row["retry_after"] > now:
                raise AppError("X read allowance is paused. Wait before syncing again.", 429)
            if row["attempted"] and row["attempted"] + self.settings.sync_interval > now:
                raise AppError("Wait a moment before syncing again.", 429)
            if mode == "older" and not row["cursor"]:
                raise AppError(
                    "There are no more timeline pages to load. An archive can add older history.",
                    409,
                )
            if mode == "auto":
                db.execute(
                    "INSERT INTO post_sync_auto VALUES(?,?,?)", (session_digest, user_id, now)
                )
            claim = uuid.uuid4().hex
            db.execute(
                "UPDATE post_sync SET status='running',claim=?,attempted=?,message='' WHERE user_id=?",
                (claim, now, user_id),
            )
            return claim, row["cursor"] if mode == "older" else None

    def sync(
        self, user_id: str, mode: SyncMode, ids: list[str], *, session_digest: str | None = None
    ) -> dict[str, Any]:
        if mode not in {"auto", "recent", "older", "check"}:
            raise AppError("Choose a supported sync action.")
        if mode == "auto" and not self.settings.auto_sync:
            return self.state(user_id, session_digest)
        self.reads.require(user_id)
        if mode == "check":
            if (
                not 1 <= len(ids) <= 100
                or len(set(ids)) != len(ids)
                or not all(valid_id(uid) for uid in ids)
            ):
                raise AppError("Choose up to 100 distinct local post IDs to check.")
            with self.db.connect() as db:
                found = db.execute(
                    "SELECT count(*) FROM posts WHERE status='available' AND id IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    ids,
                ).fetchone()[0]
            if found != len(ids):
                raise AppError("Some posts are no longer available locally. Refresh the list.", 409)
        elif ids:
            raise AppError("A timeline sync does not accept a list of post IDs.")
        claimed = self._claim(user_id, mode, session_digest)
        if claimed is None:
            return self.state(user_id, session_digest)
        claim, cursor = claimed
        unavailable: list[str] = []
        try:
            with self.reads.request(user_id):
                token = self.auth.access_token(user_id)
                reply = (
                    self.provider.lookup_many(token, ids)
                    if mode == "check"
                    else self.provider.timeline(token, user_id, cursor)
                )
            if reply.status == 429:
                now = time.time()
                try:
                    retry = float(reply.headers.get("x-rate-limit-reset", "0"))
                except ValueError:
                    retry = 0
                if not now < retry <= now + 86400:
                    retry = now + self.settings.sync_retry
                with self.db.connect(write=True) as db:
                    db.execute(
                        "UPDATE post_sync SET retry_after=? WHERE user_id=? AND claim=?",
                        (retry, user_id, claim),
                    )
            if reply.status == 401:
                self.auth.disconnect(user_id)
            self._require_success(reply)
            unavailable = self._merge(user_id, claim, mode, reply, ids, cursor)
        except AppError as exc:
            with self.db.connect(write=True) as db:
                db.execute(
                    "UPDATE post_sync SET status='error',claim=NULL,message=? WHERE user_id=? AND claim=?",
                    (exc.message, user_id, claim),
                )
            return self.state(user_id, session_digest)
        except Exception:
            with self.db.connect(write=True) as db:
                db.execute(
                    "UPDATE post_sync SET status='error',claim=NULL,"
                    "message='Posts could not be synced. Try again.' WHERE user_id=? AND claim=?",
                    (user_id, claim),
                )
            raise
        result = self.state(user_id, session_digest)
        result["unavailable_ids"] = unavailable if result["status"] != "error" else []
        return result

    def _require_success(self, reply: Reply) -> None:
        if reply.status == 429:
            raise AppError("X read allowance is exhausted. Try again after the reset.", 429)
        if reply.status in {401, 402, 403}:
            raise AppError(
                "X could not read your posts. Check app permissions and API credits, then reconnect if needed.",
                403,
            )
        if reply.status != 200:
            raise AppError("X could not return your posts. Try again later.", 502)

    def _merge(
        self,
        user_id: str,
        claim: str,
        mode: SyncMode,
        reply: Reply,
        ids: list[str],
        cursor: str | None,
    ) -> list[str]:
        data, meta = reply.data.get("data", []), reply.data.get("meta", {})
        errors = reply.data.get("errors", [])
        if not isinstance(data, list) or not isinstance(meta, dict) or not isinstance(errors, list):
            raise AppError("X returned an unexpected post list. No local posts were changed.", 502)
        if len(data) > (100 if mode == "check" else self.settings.sync_page_size):
            raise AppError("X returned too many posts. No local posts were changed.", 502)
        if mode != "check" and (
            type(meta.get("result_count")) is not int or meta["result_count"] != len(data)
        ):
            raise AppError("X returned an incomplete timeline. No local posts were changed.", 502)
        if mode == "check" and not data and not errors:
            raise AppError(
                "X did not return any availability results. No local posts were changed.", 502
            )
        if mode != "check" and errors:
            raise AppError("X returned an incomplete timeline. Try syncing again.", 502)
        next_cursor = meta.get("next_token")
        if next_cursor is not None and (
            not isinstance(next_cursor, str)
            or not 1 <= len(next_cursor) <= 2048
            or next_cursor == cursor
        ):
            raise AppError(
                "X returned an invalid timeline cursor. No local posts were changed.", 502
            )
        wrapped, returned, foreign = [], set(), set()
        for post in data:
            if not isinstance(post, dict) or not valid_id(post.get("id")) or post["id"] in returned:
                raise AppError("X returned an invalid post. No local posts were changed.", 502)
            uid = post["id"]
            if mode == "check" and uid not in ids:
                raise AppError(
                    "X returned an unexpected post ID. No local posts were changed.", 502
                )
            returned.add(uid)
            author = post.get("author_id")
            if mode == "check" and author is None:
                raise AppError(
                    "X did not verify the post author. No local posts were changed.", 502
                )
            if author is not None and author != user_id:
                if mode != "check":
                    raise AppError(
                        "X returned a post from another account. No local posts were changed.", 502
                    )
                foreign.add(uid)
                continue
            note = post.get("note_post")
            source = note if isinstance(note, dict) and "text" in note else post
            wrapped.append(
                {
                    "tweet": {
                        "id_str": uid,
                        "full_text": source.get("text"),
                        "created_at": post.get("created_at", ""),
                        "entities": source.get("entities", {}),
                    }
                }
            )
        try:
            rows = parse_archive(json.dumps(wrapped).encode())
        except AppError as exc:
            raise AppError(
                "X returned invalid post content. No local posts were changed.", 502
            ) from exc
        missing = set()
        for error in errors:
            if not isinstance(error, dict):
                raise AppError("X returned an invalid availability result.", 502)
            uid = error.get("resource_id")
            if uid not in ids or uid in returned:
                raise AppError("X returned an ambiguous availability result.", 502)
            if (
                error.get("type") == "https://api.x.com/2/problems/resource-not-found"
                and error.get("status", 404) == 404
            ):
                missing.add(uid)
        now = time.time()
        with self.db.connect(write=True) as db:
            lease = db.execute(
                "SELECT 1 FROM post_sync WHERE user_id=? AND claim=? AND status='running'",
                (user_id, claim),
            ).fetchone()
            connected = db.execute(
                "SELECT 1 FROM accounts WHERE id=? AND tokens IS NOT NULL", (user_id,)
            ).fetchone()
            if not lease or not connected:
                raise AppError("The X connection changed. Connect again before syncing.", 409)
            db.executemany(
                "INSERT INTO posts(id,text,created_at,urls,search_text) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET text=excluded.text,created_at=excluded.created_at,"
                "urls=excluded.urls,search_text=excluded.search_text WHERE posts.status='available'",
                rows,
            )
            for row in rows:
                db.execute(
                    "INSERT INTO post_sources(post_id,live_seen,remote_state,checked_at) VALUES(?,1,'present',?) "
                    "ON CONFLICT(post_id) DO UPDATE SET live_seen=1,remote_state='present',checked_at=excluded.checked_at "
                    "WHERE (SELECT status FROM posts WHERE id=excluded.post_id)='available'",
                    (row[0], now),
                )
            for values, state in ((missing, "unavailable"), (foreign, "not_owned")):
                for uid in values:
                    db.execute(
                        "UPDATE post_sources SET remote_state=?,checked_at=? WHERE post_id=? "
                        "AND (SELECT status FROM posts WHERE id=post_id)='available'",
                        (state, now, uid),
                    )
                    db.execute(
                        "UPDATE posts SET status='unavailable' WHERE id=? AND status='available'",
                        (uid,),
                    )
            checked = len(rows) + len(missing) + len(foreign)
            if mode == "check":
                pending = len(ids) - checked
                message = f"{checked} posts checked on X." + (
                    f" {pending} could not be verified." if pending else ""
                )
                db.execute(
                    "UPDATE post_sync SET status='idle',claim=NULL,completed=?,message=?,checked_count=?,retry_after=0 WHERE user_id=?",
                    (now, message, checked, user_id),
                )
            else:
                message = f"{len(rows)} posts synced from X."
                db.execute(
                    "UPDATE post_sync SET status='idle',claim=NULL,cursor=?,completed=?,message=?,"
                    "pages=CASE WHEN ?='older' THEN pages+1 ELSE 1 END,"
                    "fetched=CASE WHEN ?='older' THEN fetched+? ELSE ? END,retry_after=0 WHERE user_id=?",
                    (next_cursor, now, message, mode, mode, len(rows), len(rows), user_id),
                )

        return sorted(missing | foreign)
