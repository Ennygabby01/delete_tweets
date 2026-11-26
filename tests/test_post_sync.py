import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from conftest import archive_bytes, signed_in

from app.errors import AppError
from app.storage import SCHEMA, Database


@pytest.fixture(autouse=True)
def consent_for_sync_contract_tests(client):
    with client.app.state.db.connect(write=True) as db:
        db.execute("INSERT INTO meta VALUES('allow_post_reads:12345','1')")


def post(uid="2000", text="A live post", author="12345"):
    return {
        "id": uid,
        "text": text,
        "author_id": author,
        "created_at": "2026-11-23T10:00:00.000Z",
        "entities": {"urls": [{"expanded_url": "https://example.com/live"}]},
    }


def timeline(rows, cursor=None):
    meta = {"result_count": len(rows)}
    if cursor:
        meta["next_token"] = cursor
    return httpx.Response(200, json={"data": rows, "meta": meta})


def allow_next(services):
    with services.db.connect(write=True) as db:
        db.execute("UPDATE post_sync SET attempted=0")


def test_no_archive_account_posts_appear_and_auto_only_runs_once(client, fake):
    client.app.state.post_sync.settings = replace(client.app.state.settings, auto_sync=True)
    headers = signed_in(client)
    fake.replies.append(timeline([post()], "older-page"))
    result = client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"})
    assert result.status_code == 200 and result.json()["status"] == "idle"
    data = client.get("/v1/posts").json()
    assert data["total"] == 1
    assert data["posts"][0]["remote_state"] == "present"
    assert data["posts"][0]["archive_seen"] == 0
    assert data["posts"][0]["live_seen"] == 1
    assert client.get("/v1/workspace").json()["sync"]["has_older"] is True
    assert client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"}).status_code == 200
    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.url.path == "/2/users/12345/tweets"
    assert request.url.params["exclude"] == "retweets"
    assert request.url.params["post.fields"] == "created_at,entities,note_post"


def test_live_and_archive_merge_without_dropping_out_of_window_posts(ready, fake):
    client, headers, services = ready
    fake.replies.append(timeline([post("1004", "Updated live content"), post()]))
    response = client.post("/v1/posts/sync", headers=headers, json={"mode": "recent"})
    assert response.json()["status"] == "idle"
    data = services.archive.list(1, 100, "")
    assert data["total"] == 6
    records = {row["id"]: row for row in data["posts"]}
    assert records["1004"]["text"] == "Updated live content"
    assert records["1004"]["archive_seen"] == records["1004"]["live_seen"] == 1
    assert records["1000"]["remote_state"] == "unchecked"
    services.archive.import_bytes(archive_bytes())
    assert {p["id"]: p for p in services.archive.list(1, 100, "")["posts"]}["1004"][
        "text"
    ] == "Updated live content"


def test_older_page_uses_persisted_cursor_and_refresh_starts_recent(ready, fake):
    _, _, services = ready
    fake.replies += [
        timeline([post()], "cursor_1"),
        timeline([post("1999")], "cursor_2"),
        timeline([post("2001")]),
    ]
    services.post_sync.sync("12345", "recent", [])
    allow_next(services)
    result = services.post_sync.sync("12345", "older", [])
    assert result["pages"] == 2 and result["fetched"] == 2
    assert fake.calls[-1].url.params["pagination_token"] == "cursor_1"
    allow_next(services)
    services.post_sync.sync("12345", "recent", [])
    assert "pagination_token" not in fake.calls[-1].url.params
    assert services.archive.stats()["total"] == 8


def test_explicit_checks_mark_missing_and_foreign_but_not_unreturned(ready, fake):
    client, headers, services = ready
    fake.replies.append(
        httpx.Response(
            200,
            json={
                "data": [post("1000"), post("1001", author="99999")],
                "errors": [
                    {
                        "resource_id": "1002",
                        "type": "https://api.x.com/2/problems/resource-not-found",
                        "status": 404,
                    }
                ],
            },
        )
    )
    result = client.post(
        "/v1/posts/sync",
        headers=headers,
        json={"mode": "check", "ids": ["1000", "1001", "1002", "1003"]},
    ).json()
    assert result["status"] == "idle" and result["checked_count"] == 3
    assert result["unavailable_ids"] == ["1001", "1002"]
    assert services.archive.stats()["deleted"] == 0
    assert services.archive.stats()["unavailable"] == 2
    records = {row["id"]: row for row in services.archive.list(1, 100, "")["posts"]}
    assert records["1000"]["remote_state"] == "present"
    assert records["1003"]["remote_state"] == "unchecked"
    assert "ids=1000" in str(fake.calls[0].url)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"detail": "SYNTHETIC_PRIVATE_SENTINEL"}),
        httpx.Response(500, json={"detail": "SYNTHETIC_PRIVATE_SENTINEL"}),
        httpx.Response(200, json={}),
        timeline([post(author="other")]),
        httpx.Response(
            200,
            json={"data": [post()], "meta": {"result_count": 1}, "errors": [{"title": "partial"}]},
        ),
    ],
)
def test_failed_or_partial_timeline_never_claims_empty_success_or_changes_local(
    ready, fake, response
):
    _, _, services = ready
    before = services.archive.stats()
    fake.replies.append(response)
    result = services.post_sync.sync("12345", "recent", [])
    assert result["status"] == "error"
    assert result["completed"] is None
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in result["message"]
    assert services.archive.stats() == before


def test_429_read_backoff_separate_from_deletion_budget(ready, fake):
    _, _, services = ready
    reset = int(time.time()) + 600
    fake.replies.append(httpx.Response(429, json={}, headers={"x-rate-limit-reset": str(reset)}))
    result = services.post_sync.sync("12345", "recent", [])
    assert result["status"] == "error" and result["retry_after"] == reset
    assert services.quota.get("12345")["remaining"] == 3
    with pytest.raises(AppError, match="paused"):
        services.post_sync.sync("12345", "recent", [])
    assert len(fake.calls) == 1


def test_deletion_tombstones_are_not_resurrected_by_older_read(ready, fake):
    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute("UPDATE posts SET status='deleted' WHERE id='1004'")
    fake.replies.append(timeline([post("1004")]))
    services.post_sync.sync("12345", "recent", [])
    assert services.archive.stats()["deleted"] == 1
    assert "1004" not in {row["id"] for row in services.archive.list(1, 100, "")["posts"]}


def test_parallel_auto_requests_make_only_one_provider_read(ready, fake, monkeypatch):
    _, _, services = ready
    services.post_sync.settings = replace(services.settings, auto_sync=True)
    with services.db.connect() as db:
        session_digest = db.execute("SELECT digest FROM sessions WHERE user_id='12345'").fetchone()[
            0
        ]
    entered, release = threading.Event(), threading.Event()

    def respond(token, user_id, cursor):
        entered.set()
        assert release.wait(3)
        return services.provider.request("GET", "/users/12345/tweets", token=token)

    monkeypatch.setattr(services.provider, "timeline", respond)
    fake.replies.append(timeline([post()]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            services.post_sync.sync, "12345", "auto", [], session_digest=session_digest
        )
        assert entered.wait(2)
        second = pool.submit(
            services.post_sync.sync, "12345", "auto", [], session_digest=session_digest
        )
        assert second.result(timeout=2)["status"] == "running"
        release.set()
        assert first.result(timeout=2)["status"] == "idle"
    assert len(fake.calls) == 1


def test_sync_blocks_run_preview_and_existing_run_blocks_sync(ready):
    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute(
            "INSERT INTO post_sync(user_id,status,claim,attempted) VALUES('12345','running','synthetic',1)"
        )
    with pytest.raises(AppError, match="sync"):
        services.runs.preview("12345", [], True, "", "a" * 20)
    services.post_sync.recover()
    assert services.post_sync.state("12345")["status"] == "error"
    services.runs.preview("12345", [], True, "", "a" * 20)
    with pytest.raises(AppError, match="current run"):
        services.post_sync.sync("12345", "recent", [])


def test_sync_auth_csrf_and_ids_are_enforced(client, ready):
    client, headers, services = ready
    assert client.post("/v1/posts/sync", json={"mode": "recent"}).status_code == 403
    assert (
        client.post(
            "/v1/posts/sync", headers=headers, json={"mode": "check", "ids": ["../invalid"]}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/posts/sync", headers=headers, json={"mode": "check", "ids": ["99999"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/v1/posts/sync", headers=headers, json={"mode": "recent", "user_id": "other"}
        ).status_code
        == 422
    )
    client.cookies.clear()
    assert (
        client.post("/v1/posts/sync", headers=headers, json={"mode": "recent"}).status_code == 401
    )


def test_logout_during_read_prevents_merging_late_response(ready, fake, monkeypatch):
    _, _, services = ready

    def respond(token, user_id, cursor):
        services.auth.disconnect("12345")
        return services.provider.request("GET", "/users/12345/tweets", token=token)

    monkeypatch.setattr(services.provider, "timeline", respond)
    fake.replies.append(timeline([post()]))
    result = services.post_sync.sync("12345", "recent", [])
    assert result["status"] == "error"
    assert services.archive.stats()["total"] == 5


def test_schema_v1_migration_backs_up_and_preserves_source(tmp_path):
    directory = tmp_path / "state"
    directory.mkdir()
    db = Database(directory)
    old = sqlite3.connect(db.path)
    old.executescript(SCHEMA)
    old.execute("INSERT INTO posts VALUES('1000','old post','date','[]','old post','deleted')")
    old.execute("PRAGMA user_version=1")
    old.commit()
    old.close()
    db.initialize()
    with db.connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT archive_seen FROM post_sources").fetchone()[0] == 1
        assert conn.execute("SELECT status FROM posts").fetchone()[0] == "deleted"
    backups = list((directory / "backups").glob("*.sqlite3"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    with (
        sqlite3.connect(backups[0]) as source,
        sqlite3.connect(tmp_path / "restored.sqlite3") as restored,
    ):
        source.backup(restored)
        assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
        assert restored.execute("SELECT status FROM posts").fetchone()[0] == "deleted"


def test_new_sign_in_gets_one_fresh_auto_page(client, fake):
    from app.sessions import COOKIE

    client.app.state.post_sync.settings = replace(client.app.state.settings, auto_sync=True)
    headers = signed_in(client)
    assert client.get("/v1/workspace").json()["sync"]["can_auto_sync"] is True
    fake.replies.append(timeline([post()]))
    result = client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"}).json()
    assert result["can_auto_sync"] is False
    token, session = client.app.state.sessions.create("12345")
    client.cookies.clear()
    client.cookies.set(COOKIE, token, domain="127.0.0.1", path="/")
    headers["X-CSRF-Token"] = session.csrf
    allow_next(client.app.state)
    assert client.get("/v1/workspace").json()["sync"]["can_auto_sync"] is True
    fake.replies.append(timeline([post("2001")]))
    assert (
        client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"}).json()["status"]
        == "idle"
    )
    assert len(fake.calls) == 2


def test_empty_timeline_does_not_remove_archive_history(ready, fake):
    _, _, services = ready
    fake.replies.append(timeline([]))
    result = services.post_sync.sync("12345", "recent", [])
    assert result["status"] == "idle" and result["fetched"] == 0
    assert services.archive.stats()["available"] == 5
    assert services.archive.stats()["unavailable"] == 0


def test_long_posts_preserve_full_text_and_unsafe_links_are_not_stored(ready, fake):
    _, _, services = ready
    value = post()
    value["note_post"] = {
        "text": "Full long post <script>untrusted</script>",
        "entities": {"urls": [{"expanded_url": "javascript:alert(1)"}]},
    }
    fake.replies.append(timeline([value]))
    services.post_sync.sync("12345", "recent", [])
    found = services.archive.list(1, 100, "Full long")["posts"]
    assert len(found) == 1 and found[0]["text"] == "Full long post <script>untrusted</script>"
    assert found[0]["urls"] == []


def test_default_workspace_and_legacy_auto_make_no_post_reads(ready, fake):
    client, headers, services = ready
    services.post_reads.set_allowed("12345", False)
    assert services.settings.auto_sync is False
    for _ in range(3):
        assert client.get("/v1/session").status_code == 200
        assert client.get("/v1/workspace").json()["sync"]["can_auto_sync"] is False
        assert client.get("/v1/posts").json()["total"] == 5
    result = client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"})
    assert result.json()["status"] == "idle"
    assert not fake.calls
