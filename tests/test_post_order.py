import json
import time

import pytest

from app.errors import AppError
from app.post_order import PostOrder, direction


def test_order_preference_is_persistent_owner_scoped_and_protected(ready, fake):
    client, headers, services = ready
    assert services.post_order.get("12345") == "newest"
    assert (
        client.put("/v1/preferences/post-order", json={"post_order": "oldest"}).status_code == 403
    )
    assert (
        client.put(
            "/v1/preferences/post-order", json={"post_order": "oldest"}, headers=headers
        ).status_code
        == 200
    )
    assert PostOrder(services.db).get("12345") == "oldest"
    assert services.post_order.get("different-owner") == "newest"
    assert client.get("/v1/workspace").json()["preferences"]["post_order"] == "oldest"
    assert not fake.calls
    for value in ("sideways", "ASC; DROP TABLE posts", True):
        assert (
            client.put(
                "/v1/preferences/post-order", json={"post_order": value}, headers=headers
            ).status_code
            == 422
        )
    client.cookies.clear()
    assert (
        client.put(
            "/v1/preferences/post-order",
            json={"post_order": "newest"},
            headers={"Origin": str(client.base_url).rstrip("/")},
        ).status_code
        == 401
    )


def test_list_order_pagination_and_numeric_ids_are_consistent(ready):
    client, _, services = ready
    services.archive.import_bytes(
        json.dumps(
            [{"tweet": {"id_str": uid, "full_text": "order sample"}} for uid in ["9", "10", "100"]]
        ).encode()
    )
    services.post_order.save("12345", "oldest")
    first = client.get("/v1/posts", params={"limit": 2, "search": "order sample"}).json()
    second = client.get(
        "/v1/posts", params={"limit": 2, "page": 2, "search": "order sample"}
    ).json()
    assert [p["id"] for p in first["posts"]] == ["9", "10"]
    assert [p["id"] for p in second["posts"]] == ["100"]
    services.post_order.save("12345", "newest")
    assert [
        p["id"] for p in client.get("/v1/posts", params={"search": "order sample"}).json()["posts"]
    ] == ["100", "10", "9"]
    with pytest.raises(AppError):
        direction("ASC; DROP TABLE posts")


def test_manual_preview_and_worker_freeze_oldest_order(ready, fake):
    _, _, services = ready
    services.post_order.save("12345", "oldest")
    detail = services.runs.preview("12345", [], True, "", "oldest-preview-key-123")
    assert detail["post_order"] == "oldest"
    assert [p["id"] for p in detail["items"]] == ["1000", "1001", "1002"]
    services.post_order.save("12345", "newest")
    replay = services.runs.preview("12345", [], True, "", "oldest-preview-key-123")
    assert replay["id"] == detail["id"] and replay["post_order"] == "oldest"
    with pytest.raises(AppError, match="another selection"):
        services.runs.preview("12345", [], True, "", "oldest-preview-key-123", post_order="newest")
    services.runs.confirm("12345", detail["id"], "DELETE")
    for _ in range(3):
        services.runs.tick()
    assert [c.url.path.rsplit("/", 1)[-1] for c in fake.calls] == ["1000", "1001", "1002"]
    assert services.runs.list("12345")[0]["post_order"] == "oldest"


def test_scheduled_order_survives_cooldown_and_preference_change(ready, fake, monkeypatch):
    _, _, services = ready
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    services.post_order.save("12345", "oldest")
    detail = services.runs.preview("12345", [], True, "", "oldest-scheduled-key", schedule=(4, 2))
    assert [p["id"] for p in detail["items"]] == ["1000", "1001", "1002", "1003"]
    services.runs.confirm("12345", detail["id"], "DELETE")
    for _ in range(4):
        services.runs.tick()
    waiting = services.runs.detail("12345", detail["id"])["schedule"]["waiting_until"]
    services.post_order.save("12345", "newest")
    clock[0] = waiting + 1
    for _ in range(3):
        services.runs.tick()
    assert [c.url.path.rsplit("/", 1)[-1] for c in fake.calls] == ["1000", "1001", "1002", "1003"]
    assert all(c.method == "DELETE" for c in fake.calls)


def test_existing_runs_without_metadata_retain_newest_order(ready, fake):
    _, _, services = ready
    detail = services.runs.preview("12345", [], True, "", "existing-preview-key")
    with services.db.connect(write=True) as db:
        db.execute("DELETE FROM meta WHERE key=?", ("run_order:" + detail["id"],))
    services.post_order.save("12345", "oldest")
    result = services.runs.detail("12345", detail["id"])
    assert result["post_order"] == "newest"
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    assert fake.calls[0].url.path.endswith("/1004")


def test_explicit_subset_and_post_order_are_bound_by_preview(ready):
    client, headers, _ = ready
    body = {
        "ids": ["1000", "1004", "1001", "1003"],
        "all_matching": False,
        "search": "",
        "request_key": "explicit-subset-key-123",
        "post_order": "oldest",
    }
    result = client.post("/v1/runs/preview", json=body, headers=headers).json()
    assert [p["id"] for p in result["items"]] == ["1000", "1001", "1003"]
    assert (
        client.post(
            "/v1/runs/preview", json=body | {"post_order": "newest"}, headers=headers
        ).status_code
        == 409
    )
