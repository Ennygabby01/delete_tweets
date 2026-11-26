import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import httpx
import pytest

from app.errors import AppError
from app.post_reads import PostReads


def test_read_permission_defaults_off_and_deletion_uses_archive_without_reads(ready, fake):
    client, headers, services = ready
    assert client.get("/v1/workspace").json()["preferences"] == {
        "allow_post_reads": False,
        "post_order": "newest",
    }
    for mode in ("recent", "older", "check"):
        body = {"mode": mode, "ids": ["1000"] if mode == "check" else []}
        assert client.post("/v1/posts/sync", headers=headers, json=body).status_code == 403
    assert not fake.calls
    detail = services.runs.preview("12345", ["1000"], False, "", "no-read-preview-12345")
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    assert [(r.method, r.url.path) for r in fake.calls] == [("DELETE", "/2/tweets/1000")]


def test_permission_on_off_is_persistent_and_enabling_does_not_fetch(ready, fake):
    client, headers, services = ready
    for allowed in (True, False, True):
        result = client.put(
            "/v1/preferences/post-reads", headers=headers, json={"allow_post_reads": allowed}
        )
        assert result.status_code == 200
        assert PostReads(services.db).allowed("12345") is allowed
        assert client.get("/v1/workspace").json()["preferences"]["allow_post_reads"] is allowed
    assert not fake.calls
    fake.replies.append(httpx.Response(200, json={"data": [], "meta": {"result_count": 0}}))
    assert (
        client.post("/v1/posts/sync", headers=headers, json={"mode": "recent"}).json()["status"]
        == "idle"
    )
    assert len(fake.calls) == 1
    client.put("/v1/preferences/post-reads", headers=headers, json={"allow_post_reads": False})
    assert (
        client.post("/v1/posts/sync", headers=headers, json={"mode": "recent"}).status_code == 403
    )
    assert len(fake.calls) == 1


def test_preference_owner_csrf_and_strict_boolean_contract(ready):
    client, headers, services = ready
    path = "/v1/preferences/post-reads"
    assert client.put(path, json={"allow_post_reads": True}).status_code == 403
    for value in ("true", "false", 1, 0, None):
        assert (
            client.put(path, headers=headers, json={"allow_post_reads": value}).status_code == 422
        )
    assert (
        client.put(
            path, headers=headers, json={"allow_post_reads": True, "user_id": "other"}
        ).status_code
        == 422
    )
    with pytest.raises(AppError, match="installation owner"):
        services.post_reads.set_allowed("99999", True)
    client.cookies.clear()
    assert client.put(path, headers=headers, json={"allow_post_reads": True}).status_code == 401


def test_read_disable_waits_for_admitted_request_and_blocks_next(ready, fake, monkeypatch):
    _, _, services = ready
    services.post_reads.set_allowed("12345", True)
    entered, release, disabling = threading.Event(), threading.Event(), threading.Event()

    def timeline(token, user_id, cursor):
        entered.set()
        assert release.wait(3)
        return services.provider.request("GET", "/users/12345/tweets", token=token)

    def disable():
        disabling.set()
        return services.post_reads.set_allowed("12345", False)

    monkeypatch.setattr(services.provider, "timeline", timeline)
    fake.replies.append(httpx.Response(200, json={"data": [], "meta": {"result_count": 0}}))
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(services.post_sync.sync, "12345", "recent", [])
        assert entered.wait(2)
        writer = pool.submit(disable)
        assert disabling.wait(2)
        with pytest.raises(TimeoutError):
            writer.result(timeout=0.05)
        release.set()
        assert writer.result(timeout=2) == {"allow_post_reads": False}
        assert reader.result(timeout=2)["status"] == "idle"
    with pytest.raises(AppError, match="blocked"):
        services.post_sync.sync("12345", "recent", [])
    assert len(fake.calls) == 1


def test_disabled_reads_preserve_unknown_results_and_do_not_refresh_token(ready, fake):
    client, headers, services = ready
    detail = services.runs.preview("12345", ["1000"], False, "", "unknown-read-off-12345")
    with services.db.connect(write=True) as db:
        db.execute("UPDATE runs SET status='paused' WHERE id=?", (detail["id"],))
        db.execute("UPDATE run_items SET status='unknown' WHERE run_id=?", (detail["id"],))
        db.execute("UPDATE accounts SET token_expires=0")
    assert client.post(f"/v1/runs/{detail['id']}/reconcile", headers=headers).status_code == 403
    assert not fake.calls
    assert services.runs.detail("12345", detail["id"])["counts"] == {"unknown": 1}
    with pytest.raises(AppError, match="uncertain"):
        services.runs.confirm("12345", detail["id"], "DELETE")


def test_disabled_preference_overrides_legacy_auto_configuration(ready, fake):
    from dataclasses import replace

    client, headers, services = ready
    services.post_sync.settings = replace(services.settings, auto_sync=True)
    assert client.get("/v1/workspace").json()["sync"]["can_auto_sync"] is False
    assert client.post("/v1/posts/sync", headers=headers, json={"mode": "auto"}).status_code == 403
    assert not fake.calls
