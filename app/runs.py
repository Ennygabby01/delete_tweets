"""Durable deletion previews, claims, outcomes and explicit recovery."""

import hashlib
import html
import json
import logging
import threading
import time
import uuid
from typing import Any

from app.archive import Archive
from app.auth import Auth
from app.config import Settings
from app.errors import AppError, ProviderError
from app.post_order import PostOrder, direction
from app.post_reads import PostReads
from app.provider import Reply, XClient
from app.quota import Quota
from app.scheduling import Schedules
from app.storage import Database
from app.x_errors import deletion_denial, public_run_note

logger = logging.getLogger(__name__)


class Runs:
    def __init__(
        self,
        db: Database,
        archive: Archive,
        auth: Auth,
        quota: Quota,
        provider: XClient,
        settings: Settings,
        reads: PostReads,
    ) -> None:
        self.db, self.archive, self.auth = db, archive, auth
        self.quota, self.provider, self.settings = quota, provider, settings
        self.worker_lock = threading.Lock()
        self.reads = reads
        self.schedules = Schedules(settings)

    def recover(self) -> None:
        with self.db.connect(write=True) as db:
            db.execute(
                "UPDATE run_items SET status='unknown',message='App stopped before the result was recorded.' "
                "WHERE status='sending'"
            )
            db.execute(
                "UPDATE runs SET status='paused',note='App restarted. Review results before continuing.' "
                "WHERE status='running'"
            )

    def preview(
        self,
        user_id: str,
        ids: list[str],
        all_matching: bool,
        search: str,
        request_key: str,
        *,
        schedule: tuple[int, int] | None = None,
        post_order: str | None = None,
    ) -> dict[str, Any]:
        if schedule:
            self.schedules.validate(*schedule)
        if post_order is not None:
            direction(post_order)
        with self.db.connect(write=True) as db:
            if db.execute(
                "SELECT 1 FROM post_sync WHERE user_id=? AND status='running'", (user_id,)
            ).fetchone():
                raise AppError("Wait for the X post sync to finish before reviewing a run.", 409)
            previous = db.execute(
                "SELECT * FROM runs WHERE user_id=? AND request_key=?", (user_id, request_key)
            ).fetchone()
            order = post_order or (
                PostOrder.for_run(db, previous["id"])
                if previous
                else PostOrder.current(db, user_id)
            )
            sort = direction(order)
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        sorted(set(ids)),
                        all_matching,
                        search,
                        *([schedule] if schedule else []),
                        *([order] if order != "newest" else []),
                    ],
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise AppError("This request identifier belongs to another selection.", 409)
                run_id = previous["id"]
            else:
                if db.execute(
                    "SELECT 1 FROM runs WHERE user_id=? AND status IN ('draft','running','paused')",
                    (user_id,),
                ).fetchone():
                    raise AppError("Review or cancel the current run before planning another.", 409)
                budget = self.quota.current(db, user_id)
                capacity = (
                    schedule[0] if schedule else min(budget["remaining"], self.settings.run_max)
                )
                if capacity <= 0:
                    raise AppError(
                        "The current X deletion window is full. Wait until it resets.", 429
                    )
                where, args = self.archive.where(db, search)
                if not all_matching:
                    if not ids:
                        raise AppError("Select at least one post.")
                    where += " AND id IN (" + ",".join("?" for _ in ids) + ")"
                    args += ids
                requested = db.execute(
                    "SELECT count(*) FROM posts WHERE " + where, args
                ).fetchone()[0]
                if not all_matching and requested != len(set(ids)):
                    raise AppError(
                        "Some selected posts are no longer eligible. Refresh the list.", 409
                    )
                if requested == 0:
                    raise AppError("No eligible posts match this selection.", 409)
                planned = db.execute(
                    "SELECT id FROM posts WHERE "
                    + where
                    + f" ORDER BY length(id) {sort},id {sort} LIMIT ?",
                    [*args, capacity],
                ).fetchall()
                run_id = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO runs(id,user_id,request_key,fingerprint,status,created,requested) "
                    "VALUES(?,?,?,?, 'draft',?,?)",
                    (run_id, user_id, request_key, fingerprint, time.time(), requested),
                )
                db.executemany(
                    "INSERT INTO run_items(run_id,post_id) VALUES(?,?)",
                    [(run_id, row[0]) for row in planned],
                )
                db.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?)", ("run_order:" + run_id, order)
                )
                if schedule:
                    db.execute(
                        "INSERT INTO run_schedule(run_id,cap,batch_size) VALUES(?,?,?)",
                        (run_id, *schedule),
                    )
        return self.detail(user_id, run_id)

    def detail(self, user_id: str, run_id: str) -> dict[str, Any]:
        with self.db.connect() as db:
            run = db.execute(
                "SELECT id,status,created,requested,note FROM runs WHERE id=? AND user_id=?",
                (run_id, user_id),
            ).fetchone()
            if not run:
                raise AppError("Run not found.", 404)
            counts = {
                row["status"]: row["n"]
                for row in db.execute(
                    "SELECT status,count(*) n FROM run_items WHERE run_id=? GROUP BY status",
                    (run_id,),
                )
            }
            post_order = PostOrder.for_run(db, run_id)
            sort = direction(post_order)
            items = db.execute(
                "SELECT i.post_id id,i.status,i.message,p.text,p.created_at FROM run_items i "
                f"JOIN posts p ON p.id=i.post_id WHERE i.run_id=? ORDER BY length(p.id) {sort},p.id {sort}",
                (run_id,),
            ).fetchall()
            schedule = self.schedules.get(db, run_id)
        return {
            **dict(run),
            "note": public_run_note(run["note"]),
            "counts": counts,
            "planned": sum(counts.values()),
            "remaining_selection": run["requested"] - sum(counts.values()),
            "items": [
                {
                    **dict(row),
                    "text": html.unescape(row["text"]),
                    "message": public_run_note(row["message"]),
                }
                for row in items
            ],
            "quota": self.quota.get(user_id),
            "schedule": schedule,
            "post_order": post_order,
        }

    def history(
        self, user_id: str, page: int, limit: int, focus: str | None = None
    ) -> dict[str, Any]:
        with self.db.connect() as db:
            total = db.execute("SELECT count(*) FROM runs WHERE user_id=?", (user_id,)).fetchone()[
                0
            ]
            if focus:
                row = db.execute(
                    "SELECT created,id FROM runs WHERE id=? AND user_id=?", (focus, user_id)
                ).fetchone()
                if not row:
                    raise AppError("Run not found.", 404)
                preceding = db.execute(
                    "SELECT count(*) FROM runs WHERE user_id=? AND (created>? OR (created=? AND id>?))",
                    (user_id, row["created"], row["created"], row["id"]),
                ).fetchone()[0]
                page = preceding // limit + 1
            pages = max(1, (total + limit - 1) // limit)
            page = min(page, pages)
            rows = db.execute(
                "SELECT id,status,created,requested,note FROM runs WHERE user_id=? "
                "ORDER BY created DESC,id DESC LIMIT ? OFFSET ?",
                (user_id, limit, (page - 1) * limit),
            ).fetchall()
            counts: dict[str, dict[str, int]] = {}
            if rows:
                marks = ",".join("?" for _ in rows)
                for item in db.execute(
                    f"SELECT run_id,status,count(*) n FROM run_items WHERE run_id IN ({marks}) GROUP BY run_id,status",
                    [row["id"] for row in rows],
                ):
                    counts.setdefault(item["run_id"], {})[item["status"]] = item["n"]
            result = [
                {
                    **dict(row),
                    "note": public_run_note(row["note"]),
                    "counts": counts.get(row["id"], {}),
                    "planned": sum(counts.get(row["id"], {}).values()),
                    "schedule": self.schedules.get(db, row["id"]),
                    "post_order": PostOrder.for_run(db, row["id"]),
                }
                for row in rows
            ]
        return {"runs": result, "total": total, "page": page, "pages": pages}

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return self.history(user_id, 1, 50)["runs"]

    def confirm(self, user_id: str, run_id: str, confirmation: str) -> None:
        if confirmation != "DELETE":
            raise AppError("Type DELETE to confirm this run.")
        with self.db.connect(write=True) as db:
            run = db.execute(
                "SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user_id)
            ).fetchone()
            if not run:
                raise AppError("Run not found.", 404)
            if run["status"] == "running":
                return
            if run["status"] not in {"draft", "paused"}:
                raise AppError("This run is already closed.", 409)
            if run["status"] == "draft" and run["created"] < time.time() - 600:
                db.execute(
                    "UPDATE runs SET status='cancelled',note='Preview expired.' WHERE id=?",
                    (run_id,),
                )
                # Commit expiry; caller can create a fresh preview after this response.
                return
            if db.execute(
                "SELECT 1 FROM run_items WHERE run_id=? AND status IN ('unknown','sending')",
                (run_id,),
            ).fetchone():
                raise AppError("Check the uncertain results before continuing this run.", 409)
            pending = db.execute(
                "SELECT count(*) FROM run_items WHERE run_id=? AND status='pending'", (run_id,)
            ).fetchone()[0]
            if not pending:
                raise AppError("There are no pending posts in this run.", 409)
            if (
                not self.schedules.get(db, run_id)
                and pending > self.quota.current(db, user_id)["remaining"]
            ):
                raise AppError(
                    "The remaining X allowance is too small for this preview. Wait for reset or cancel it.",
                    409,
                )
            db.execute("UPDATE runs SET status='running',note='' WHERE id=?", (run_id,))

    def stop(self, user_id: str, run_id: str, *, cancel: bool = False) -> None:
        with self.db.connect(write=True) as db:
            changed = db.execute(
                "UPDATE runs SET status=?,note=? WHERE id=? AND user_id=? "
                "AND status IN ('draft','running','paused')",
                (
                    "cancelled" if cancel else "paused",
                    "Stopped by you. An in-flight request may still finish.",
                    run_id,
                    user_id,
                ),
            ).rowcount
            if not changed:
                raise AppError("Run not found or already closed.", 409)

    def tick(self) -> None:
        if not self.worker_lock.acquire(blocking=False):
            return
        try:
            self._tick()
        finally:
            self.worker_lock.release()

    def _tick(self) -> None:
        with self.db.connect() as db:
            run = db.execute(
                "SELECT r.id,r.user_id FROM runs r JOIN accounts a ON a.id=r.user_id "
                "WHERE r.status='running' AND a.tokens IS NOT NULL LIMIT 1"
            ).fetchone()
        if not run:
            return
        with self.db.connect(write=True) as db:
            if not self.schedules.admit(db, run["id"], self.quota.current(db, run["user_id"])):
                return
        try:
            token = self.auth.access_token(run["user_id"])
        except AppError as exc:
            self._pause(run["id"], exc.message)
            return
        with self.db.connect(write=True) as db:
            active = db.execute(
                "SELECT 1 FROM runs WHERE id=? AND status='running'", (run["id"],)
            ).fetchone()
            if not active:
                return
            if not self.schedules.admit(db, run["id"], self.quota.current(db, run["user_id"])):
                return
            if db.execute(
                "SELECT 1 FROM run_items WHERE run_id=? AND status='unknown'", (run["id"],)
            ).fetchone():
                db.execute("UPDATE runs SET status='paused' WHERE id=?", (run["id"],))
                return
            if db.execute(
                "SELECT 1 FROM run_items WHERE run_id=? AND status='sending'", (run["id"],)
            ).fetchone():
                return
            if not db.execute(
                "SELECT 1 FROM accounts WHERE id=? AND tokens IS NOT NULL", (run["user_id"],)
            ).fetchone():
                return
            sort = direction(PostOrder.for_run(db, run["id"]))
            item = db.execute(
                "SELECT post_id FROM run_items WHERE run_id=? AND status='pending' "
                f"ORDER BY length(post_id) {sort},post_id {sort} LIMIT 1",
                (run["id"],),
            ).fetchone()
            if not item:
                db.execute(
                    "UPDATE runs SET status='completed',note='Run finished.' WHERE id=?",
                    (run["id"],),
                )
                return
            if not self.quota.consume(db, run["user_id"]):
                db.execute(
                    "UPDATE runs SET status='paused',note='X deletion allowance exhausted.' WHERE id=?",
                    (run["id"],),
                )
                return
            db.execute(
                "UPDATE run_items SET status='sending' WHERE run_id=? AND post_id=? AND status='pending'",
                (run["id"], item["post_id"]),
            )
            self.schedules.claim(db, run["id"])
        try:
            reply = self.provider.delete(token, item["post_id"])
        except ProviderError:
            self._record(
                run["id"],
                item["post_id"],
                "unknown",
                "The X result is uncertain. Check status before continuing.",
                pause=True,
            )
            logger.warning("x_delete_uncertain")
            return
        self._outcome(run["id"], run["user_id"], item["post_id"], reply)

    def _pause(self, run_id: str, message: str) -> None:
        with self.db.connect(write=True) as db:
            db.execute(
                "UPDATE runs SET status='paused',note=? WHERE id=? AND status='running'",
                (message, run_id),
            )

    def _record(
        self, run_id: str, post_id: str, status: str, message: str, *, pause: bool = False
    ) -> None:
        with self.db.connect(write=True) as db:
            db.execute(
                "UPDATE run_items SET status=?,message=? WHERE run_id=? AND post_id=? AND status='sending'",
                (status, message, run_id, post_id),
            )
            if status in {"deleted", "unavailable"}:
                db.execute("UPDATE posts SET status=? WHERE id=?", (status, post_id))
            if pause:
                db.execute(
                    "UPDATE runs SET status='paused',note=? WHERE id=? AND status='running'",
                    (message, run_id),
                )
            elif not db.execute(
                "SELECT 1 FROM run_items WHERE run_id=? AND status IN ('pending','sending','unknown')",
                (run_id,),
            ).fetchone():
                db.execute(
                    "UPDATE runs SET status='completed',note='Run finished.' WHERE id=? AND status='running'",
                    (run_id,),
                )

    def _outcome(self, run_id: str, user_id: str, post_id: str, reply: Reply) -> None:
        with self.db.connect(write=True) as db:
            self.quota.observe(db, user_id, reply.headers, reply.status)
        if (
            reply.status == 200
            and isinstance(reply.data.get("data"), dict)
            and reply.data["data"].get("deleted") is True
        ):
            self._record(run_id, post_id, "deleted", "Deletion confirmed by X.")
        elif reply.status == 404:
            self._record(
                run_id, post_id, "unavailable", "Not available on X; not counted as a new deletion."
            )
        elif reply.status == 429:
            with self.db.connect() as db:
                scheduled = self.schedules.get(db, run_id) is not None
            self._record(
                run_id,
                post_id,
                "pending",
                "Waiting for the X rate window to reset.",
                pause=not scheduled,
            )
            if scheduled:
                with self.db.connect(write=True) as db:
                    self.schedules.wait(db, run_id, self.quota.current(db, user_id))
        elif reply.status in {401, 403, 402}:
            denial = deletion_denial(reply)
            logger.warning("x_delete_declined status=%s reason=%s", reply.status, denial.reason)
            self._record(
                run_id,
                post_id,
                "pending",
                denial.message,
                pause=True,
            )
            if reply.status == 401:
                self.auth.disconnect(user_id)
        elif reply.status >= 500 or reply.status == 200:
            self._record(
                run_id,
                post_id,
                "unknown",
                "The X result is uncertain. Check status before continuing.",
                pause=True,
            )
        else:
            self._record(run_id, post_id, "failed", "X did not accept this post deletion.")

    def reconcile(self, user_id: str, run_id: str) -> dict[str, Any]:
        # Serialized with the sending worker; never repeat a DELETE on an unknown outcome.
        with self.worker_lock:
            detail = self.detail(user_id, run_id)
            if detail["status"] not in {"paused", "cancelled"}:
                raise AppError("Pause the run before checking uncertain results.", 409)
            self.reads.require(user_id)
            for item in detail["items"]:
                if item["status"] != "unknown":
                    continue
                with self.reads.request(user_id):
                    token = self.auth.access_token(user_id)
                    reply = self.provider.lookup(token, item["id"])
                data = reply.data.get("data")
                if reply.status == 404:
                    status, message = (
                        "unavailable",
                        "Not available on X; prior deletion outcome was uncertain.",
                    )
                elif (
                    reply.status == 200
                    and isinstance(data, dict)
                    and data.get("author_id") == user_id
                ):
                    status, message = "pending", "Still on X. Review and confirm before retrying."
                else:
                    continue
                with self.db.connect(write=True) as db:
                    db.execute(
                        "UPDATE run_items SET status=?,message=? WHERE run_id=? AND post_id=? AND status='unknown'",
                        (status, message, run_id, item["id"]),
                    )
                    if status == "unavailable":
                        db.execute(
                            "UPDATE posts SET status='unavailable' WHERE id=?", (item["id"],)
                        )
        return self.detail(user_id, run_id)
