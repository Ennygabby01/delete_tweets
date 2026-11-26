import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import archive_bytes

from app.errors import AppError


def preview(services, key="a" * 20, ids=None):
    return services.runs.preview("12345", ids or [], ids is None, "", key)


def test_preview_caps_selection_is_durable_and_confirms_only_on_server(ready, fake):
    client, headers, services = ready
    detail = preview(services)
    assert (detail["planned"], detail["remaining_selection"], detail["requested"]) == (3, 2, 5)
    services.runs.tick()
    assert not fake.calls
    assert services.runs.detail("12345", detail["id"])["status"] == "draft"
    assert (
        client.post(
            f"/v1/runs/{detail['id']}/confirm", headers=headers, json={"confirmation": "yes"}
        ).status_code
        == 422
    )
    services.runs.confirm("12345", detail["id"], "DELETE")
    for _ in range(3):
        services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "completed"
    assert result["counts"] == {"deleted": 3}
    assert services.archive.stats()["deleted"] == 3
    assert len(fake.calls) == 3
    assert all(call.method == "DELETE" for call in fake.calls)
    assert services.quota.get("12345")["remaining"] == 0
    services.runs.tick()
    assert len(fake.calls) == 3


def test_duplicate_preview_and_concurrent_unique_active_run(ready):
    _, _, services = ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: preview(services), range(2)))
    assert results[0]["id"] == results[1]["id"]
    with pytest.raises(AppError, match="another selection"):
        preview(services, ids=["1000"])
    with pytest.raises(AppError, match="current run"):
        preview(services, key="b" * 20)
    with services.db.connect(write=True) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO runs VALUES('other','12345','different','fingerprint','running',0,1,'')"
            )


def test_untrusted_ids_and_cross_account_access(ready):
    _, _, services = ready
    with pytest.raises(AppError, match="no longer eligible"):
        preview(services, ids=["999999999"])
    detail = preview(services, ids=["1000"])
    with pytest.raises(AppError, match="not found"):
        services.runs.detail("99999", detail["id"])
    with pytest.raises(AppError, match="not found"):
        services.runs.confirm("99999", detail["id"], "DELETE")


def test_x429_pauses_and_observes_actual_quota(ready, fake):
    _, _, services = ready
    reset = int(time.time()) + 600
    fake.replies.append(
        httpx.Response(
            429,
            json={"error": "limited"},
            headers={
                "x-rate-limit-limit": "50",
                "x-rate-limit-remaining": "0",
                "x-rate-limit-reset": str(reset),
            },
        )
    )
    detail = preview(services)
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "paused" and result["counts"] == {"pending": 3}
    assert result["quota"]["source"] == "x_response"
    assert result["quota"]["remaining"] == 0 and len(fake.calls) == 1
    with pytest.raises(AppError, match="too small"):
        services.runs.confirm("12345", detail["id"], "DELETE")


def test_timeout_unknown_never_retried_and_reconciled(ready, fake):
    _, _, services = ready
    fake.replies.append(httpx.ReadTimeout("synthetic timeout"))
    detail = preview(services, ids=["1000"])
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    assert services.runs.detail("12345", detail["id"])["counts"] == {"unknown": 1}
    with pytest.raises(AppError, match="uncertain"):
        services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    assert len(fake.calls) == 1
    services.runs.stop("12345", detail["id"], cancel=True)
    assert services.archive.list(1, 50, "")["total"] == 4
    fake.replies.append(httpx.Response(404, json={"error": "not found"}))
    services.post_reads.set_allowed("12345", True)
    result = services.runs.reconcile("12345", detail["id"])
    assert result["counts"] == {"unavailable": 1}
    assert fake.calls[-1].method == "GET"
    assert services.archive.stats()["deleted"] == 0


def test_restart_recovery_does_not_repeat_sending(ready, fake):
    _, _, services = ready
    detail = preview(services)
    services.runs.confirm("12345", detail["id"], "DELETE")
    with services.db.connect(write=True) as db:
        db.execute(
            "UPDATE run_items SET status='sending' WHERE run_id=? AND post_id='1004'",
            (detail["id"],),
        )
    services.runs.recover()
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "paused" and result["counts"]["unknown"] == 1
    assert not fake.calls


def test_404_is_not_a_confirmed_deletion_and_reimport_preserves_outcome(ready, fake):
    _, _, services = ready
    fake.replies.append(httpx.Response(404, json={}))
    detail = preview(services, ids=["1000"])
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    assert services.runs.detail("12345", detail["id"])["counts"] == {"unavailable": 1}
    services.archive.import_bytes(archive_bytes())
    assert services.archive.stats()["unavailable"] == 1
    assert services.archive.stats()["deleted"] == 0


def test_refresh_rotation_is_serialized_and_encrypted(ready, fake):
    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute("UPDATE accounts SET token_expires=0")
    with ThreadPoolExecutor(max_workers=2) as pool:
        tokens = list(pool.map(lambda _: services.auth.access_token("12345"), range(2)))
    assert tokens == ["synthetic-access"] * 2
    assert len(fake.calls) == 1
    with services.db.connect() as db:
        stored = db.execute("SELECT tokens,refresh_state FROM accounts").fetchone()
        assert stored["refresh_state"] == "ready" and "synthetic-refresh" not in stored["tokens"]


def test_interrupted_refresh_requires_reconnect(ready, fake):
    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute("UPDATE accounts SET refresh_state='refreshing'")
    with pytest.raises(AppError, match="interrupted"):
        services.auth.access_token("12345")
    assert not fake.calls


def test_sql_parameters_literal_search_and_invalid_links(ready):
    _, _, services = ready
    services.archive.add_filter("keyword", "' OR 1=1 --")
    assert services.archive.list(1, 50, "")["total"] == 0
    services.archive.remove_filter("keyword", "' OR 1=1 --")
    assert services.archive.list(1, 50, "%")["total"] == 0
    payload = json.dumps(
        [
            {
                "tweet": {
                    "id_str": "999",
                    "full_text": "<img src=x onerror=alert(1)>",
                    "entities": {"urls": [{"expanded_url": "javascript:alert(1)"}]},
                }
            }
        ]
    ).encode()
    services.archive.import_bytes(payload)
    posts = services.archive.list(1, 100, "<img")["posts"]
    assert len(posts) == 1 and posts[0]["urls"] == []


def test_failed_begin_does_not_call_provider(ready, fake):
    _, _, services = ready
    detail = preview(services)
    services.runs.confirm("12345", detail["id"], "DELETE")
    with services.db.connect(write=True):
        with pytest.raises(sqlite3.OperationalError):
            services.runs.tick()
    assert not fake.calls


def test_expired_quota_is_labelled_estimate_and_untrusted_headers_do_not_expand_it(ready):
    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute(
            "INSERT INTO quota VALUES(?,?,?,?,?)",
            ("12345", 3, 0, time.time() - 10, time.time() - 1000),
        )
        assert services.quota.current(db, "12345")["source"] == "estimate"
        assert services.quota.consume(db, "12345")
        services.quota.observe(db, "12345", httpx.Headers({"x-rate-limit-limit": "invalid"}), 200)
    value = services.quota.get("12345")
    assert value["remaining"] == 2 and value["source"] == "estimate"


def test_runtime_lock_rejects_second_process_owner(ready):
    _, _, services = ready
    with pytest.raises(RuntimeError, match="Another Tweet Deleter"):
        with services.db.runtime():
            pass


def test_pause_and_disconnect_stop_new_requests(ready, fake):
    _, _, services = ready
    detail = preview(services)
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.stop("12345", detail["id"])
    services.runs.tick()
    assert not fake.calls
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.auth.disconnect("12345")
    services.runs.tick()
    assert not fake.calls


def test_claim_failure_rolls_back_budget_and_item(ready, monkeypatch):
    _, _, services = ready
    detail = preview(services)
    services.runs.confirm("12345", detail["id"], "DELETE")
    original = services.quota.consume

    def fail_after_consume(db, user_id):
        original(db, user_id)
        raise sqlite3.OperationalError("synthetic failure")

    monkeypatch.setattr(services.quota, "consume", fail_after_consume)
    with pytest.raises(sqlite3.OperationalError):
        services.runs.tick()
    assert services.quota.get("12345")["remaining"] == 3
    assert services.runs.detail("12345", detail["id"])["counts"] == {"pending": 3}


@pytest.mark.parametrize("read_status", [402, 403])
def test_archive_deletion_after_read_access_failure_needs_no_preflight_read(
    ready, fake, read_status
):
    client, headers, services = ready
    services.post_reads.set_allowed("12345", True)
    fake.replies.append(httpx.Response(read_status, json={"detail": "synthetic-credit-error"}))
    assert (
        client.post("/v1/posts/sync", headers=headers, json={"mode": "recent"}).json()["status"]
        == "error"
    )
    fake.calls.clear()
    services.post_reads.set_allowed("12345", False)
    fake.replies += [
        httpx.Response(404, json={}),
        httpx.Response(200, json={"data": {"deleted": True}}),
    ]
    detail = preview(services, ids=["1000", "1001"])
    assert not fake.calls
    services.runs.confirm("12345", detail["id"], "DELETE")
    services.runs.tick()
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["counts"] == {"unavailable": 1, "deleted": 1}
    assert [(r.method, r.url.path) for r in fake.calls] == [
        ("DELETE", "/2/tweets/1001"),
        ("DELETE", "/2/tweets/1000"),
    ]
    assert services.archive.stats()["available"] == 3


def test_transient_refresh_keeps_local_session_without_reusing_uncertain_refresh(
    ready, monkeypatch
):
    client, _, services = ready
    from app.errors import ProviderError

    calls = []

    def unavailable(form):
        calls.append(form["grant_type"])
        raise ProviderError("X is temporarily unavailable.", 503)

    monkeypatch.setattr(services.provider, "tokens", unavailable)
    with services.db.connect(write=True) as db:
        db.execute("UPDATE accounts SET token_expires=0")
    with pytest.raises(ProviderError):
        services.auth.access_token("12345")
    assert client.get("/v1/session").json()["authenticated"] is True
    with pytest.raises(AppError) as interrupted:
        services.auth.access_token("12345")
    assert interrupted.value.status == 409
    assert calls == ["refresh_token"]


def test_confirmed_revoked_refresh_still_revokes_browser_sessions(ready, monkeypatch):
    client, _, services = ready
    from app.errors import ProviderError

    def revoked(form):
        raise ProviderError("Authorization revoked.", 401)

    monkeypatch.setattr(services.provider, "tokens", revoked)
    with services.db.connect(write=True) as db:
        db.execute("UPDATE accounts SET token_expires=0")
    with pytest.raises(ProviderError):
        services.auth.access_token("12345")
    assert client.get("/v1/session").json()["authenticated"] is False
