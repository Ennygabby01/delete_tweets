import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest
from conftest import archive_bytes, signed_in
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import create_app
from app.provider import XClient


def plan(services, cap=5, batch=2, key="scheduled-preview-1234"):
    return services.runs.preview("12345", [], True, "", key, schedule=(cap, batch))


def confirm(services, detail):
    services.runs.confirm("12345", detail["id"], "DELETE")


def test_schedule_freezes_cap_and_waits_without_reads_or_extra_deletes(ready, fake, monkeypatch):
    _, _, services = ready
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    detail = plan(services, cap=4)
    assert detail["planned"] == 4 and detail["remaining_selection"] == 1
    services.runs.tick()
    assert not fake.calls
    confirm(services, detail)
    for _ in range(4):
        services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["counts"] == {"deleted": 3, "pending": 1}
    assert result["status"] == "running" and result["schedule"]["waiting_until"] > clock[0]
    for _ in range(5):
        services.runs.tick()
    assert len(fake.calls) == 3
    clock[0] = result["schedule"]["waiting_until"] + 1
    for _ in range(4):
        services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "completed" and result["counts"] == {"deleted": 4}
    assert services.archive.stats()["available"] == 1
    assert len(fake.calls) == 4 and all(call.method == "DELETE" for call in fake.calls)


def test_schedule_obeys_x_reset_headers_and_429_but_billing_requires_resume(
    ready, fake, monkeypatch
):
    _, _, services = ready
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    reset = int(clock[0]) + 120
    fake.replies.append(
        httpx.Response(
            429,
            json={},
            headers={
                "x-rate-limit-limit": "50",
                "x-rate-limit-remaining": "0",
                "x-rate-limit-reset": str(reset),
            },
        )
    )
    detail = plan(services)
    confirm(services, detail)
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "running" and result["schedule"]["waiting_until"] == reset
    assert result["schedule"]["cooldown_source"] == "x_response"
    services.runs.tick()
    assert len(fake.calls) == 1
    clock[0] = reset + 1
    fake.replies.append(httpx.Response(402, json={}))
    services.runs.tick()
    assert services.runs.detail("12345", detail["id"])["status"] == "paused"
    clock[0] += 10000
    services.runs.tick()
    assert len(fake.calls) == 2


def test_pause_stop_and_restart_never_silently_resume(ready, fake, monkeypatch):
    _, _, services = ready
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    detail = plan(services)
    confirm(services, detail)
    services.runs.tick()
    services.runs.stop("12345", detail["id"])
    services.runs.tick()
    assert len(fake.calls) == 1
    confirm(services, detail)
    services.runs.tick()
    services.runs.tick()
    services.runs.recover()
    clock[0] += 1000
    services.runs.tick()
    assert len(fake.calls) == 3
    assert services.runs.detail("12345", detail["id"])["status"] == "paused"
    services.runs.stop("12345", detail["id"], cancel=True)
    with pytest.raises(AppError, match="closed"):
        confirm(services, detail)
    services.runs.tick()
    assert len(fake.calls) == 3


def test_uncertain_schedule_result_is_not_retried_or_replaced(ready, fake):
    _, _, services = ready
    detail = plan(services)
    confirm(services, detail)
    fake.replies.append(httpx.ReadTimeout("synthetic timeout"))
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "paused" and result["counts"] == {"unknown": 1, "pending": 4}
    with pytest.raises(AppError, match="uncertain"):
        confirm(services, detail)
    services.runs.tick()
    assert len(fake.calls) == 1


def test_schedule_preview_is_idempotent_and_binds_cap_and_batch(ready):
    _, _, services = ready
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: plan(services), range(2)))
    assert results[0]["id"] == results[1]["id"]
    for kwargs in ({"cap": 4}, {"batch": 1}):
        with pytest.raises(AppError, match="another selection"):
            plan(services, **kwargs)
    with pytest.raises(AppError, match="current run"):
        services.runs.preview("12345", [], True, "", "other-manual-preview")
    with pytest.raises(AppError, match="not found"):
        services.runs.confirm("other-owner", results[0]["id"], "DELETE")


def test_schedule_api_validates_before_work_and_requires_authentication(client, ready):
    client, headers, _ = ready
    body = {
        "all_matching": True,
        "cap": 5,
        "batch_size": 2,
        "request_key": "valid-scheduled-key-123",
    }
    assert client.post("/v1/runs/scheduled/preview", json=body).status_code == 403
    for changes in ({"cap": True}, {"cap": 0}, {"cap": 10001}, {"batch_size": 0}):
        assert (
            client.post(
                "/v1/runs/scheduled/preview", json=body | changes, headers=headers
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/v1/runs/scheduled/preview", json=body | {"batch_size": 4}, headers=headers
        ).status_code
        == 400
    )
    assert client.post("/v1/runs/scheduled/preview", json=body, headers=headers).status_code == 200
    client.cookies.clear()
    assert (
        client.post(
            "/v1/runs/scheduled/preview",
            json=body,
            headers={"Origin": str(client.base_url).rstrip("/")},
        ).status_code
        == 401
    )


def test_500_post_snapshot_runs_in_50_request_batches(settings, fake, monkeypatch):
    settings = replace(settings, run_max=50, fallback_limit=50)
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    with TestClient(app, base_url=settings.origin) as client:
        signed_in(client)
        services = app.state
        services.archive.import_bytes(archive_bytes(600))
        detail = plan(services, cap=500, batch=50)
        confirm(services, detail)
        waits = 0
        for _ in range(520):
            services.runs.tick()
            result = services.runs.detail("12345", detail["id"])
            if result["status"] == "completed":
                break
            if result["schedule"]["waiting_until"] > clock[0]:
                waits += 1
                clock[0] = result["schedule"]["waiting_until"] + 1
        assert result["status"] == "completed" and waits == 9
        deleted = [call.url.path for call in fake.calls if call.method == "DELETE"]
        assert len(deleted) == len(set(deleted)) == 500
        assert not any(call.method == "GET" for call in fake.calls)
        assert services.archive.stats()["available"] == 100


def test_stop_during_inflight_result_never_restarts_schedule(ready, fake):
    _, _, services = ready
    detail = plan(services)
    confirm(services, detail)

    def stop_inflight(*args):
        services.runs.stop("12345", detail["id"], cancel=True)
        return httpx.Response(200, json={"data": {"deleted": True}})

    fake.replies.append(stop_inflight)
    original = fake.respond

    def respond(request):
        if fake.replies and callable(fake.replies[0]):
            fake.calls.append(request)
            return fake.replies.pop(0)()
        return original(request)

    services.provider.client.close()
    services.provider.client = httpx.Client(transport=httpx.MockTransport(respond))
    services.runs.tick()
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "cancelled" and result["counts"] == {"deleted": 1, "pending": 4}
    assert len(fake.calls) == 1


def test_schedule_rollback_does_not_leave_a_run_or_partial_snapshot(ready):
    import sqlite3

    _, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute(
            "CREATE TRIGGER reject_schedule BEFORE INSERT ON run_schedule BEGIN SELECT RAISE(ABORT,'synthetic'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        plan(services)
    with services.db.connect() as db:
        assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM run_items").fetchone()[0] == 0


def test_schedule_claim_survives_crash_without_automatic_resend(ready, monkeypatch):
    _, _, services = ready
    detail = plan(services)
    confirm(services, detail)

    def crash(*args):
        with services.db.connect() as db:
            assert (
                db.execute("SELECT count(*) FROM run_items WHERE status='sending'").fetchone()[0]
                == 1
            )
            assert db.execute("SELECT batch_used FROM run_schedule").fetchone()[0] == 1
        raise RuntimeError("synthetic process interruption")

    monkeypatch.setattr(services.provider, "delete", crash)
    with pytest.raises(RuntimeError):
        services.runs.tick()
    services.runs.recover()
    services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "paused" and result["counts"]["unknown"] == 1


def test_independent_worker_claims_cannot_duplicate_a_scheduled_request(ready, monkeypatch):
    from threading import Event

    from app.runs import Runs

    _, _, services = ready
    detail = plan(services)
    confirm(services, detail)
    entered, release = Event(), Event()
    calls = []
    original = services.provider.delete

    def blocked(token, post_id):
        calls.append(post_id)
        entered.set()
        assert release.wait(5)
        return original(token, post_id)

    monkeypatch.setattr(services.provider, "delete", blocked)
    other = Runs(
        services.db,
        services.archive,
        services.auth,
        services.quota,
        services.provider,
        services.settings,
        services.post_reads,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(services.runs.tick)
        try:
            assert entered.wait(5)
            pool.submit(other.tick).result(timeout=5)
            assert len(calls) == 1
        finally:
            release.set()
        first.result(timeout=5)
    assert services.runs.detail("12345", detail["id"])["counts"] == {"deleted": 1, "pending": 4}


def test_v2_upgrade_preserves_history_and_backs_up_before_schedule_schema(tmp_path):
    import sqlite3

    from app.storage import SCHEMA, SYNC_SCHEMA, Database

    db = Database(tmp_path / "state")
    db.directory.mkdir()
    with sqlite3.connect(db.path) as connection:
        connection.executescript(SCHEMA + SYNC_SCHEMA)
        connection.execute(
            "INSERT INTO posts VALUES('1000','preserved','date','[]','preserved','deleted')"
        )
        connection.execute("PRAGMA user_version=2")
    db.initialize()
    with db.connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert (
            connection.execute("SELECT status FROM posts WHERE id='1000'").fetchone()[0]
            == "deleted"
        )
        assert connection.execute("SELECT count(*) FROM run_schedule").fetchone()[0] == 0
    backups = list((db.directory / "backups").glob("before-schema-v3-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    db.initialize()
    assert len(list((db.directory / "backups").glob("*.sqlite3"))) == 1


@pytest.mark.parametrize("legacy_restart", [False, True])
def test_25_post_batches_use_the_full_50_request_window_and_clear_legacy_wait(
    settings, fake, monkeypatch, legacy_restart
):
    settings = replace(settings, run_max=50, fallback_limit=50)
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    reset = int(clock[0]) + 900
    fake.replies.extend(
        httpx.Response(
            200,
            json={"data": {"deleted": True}},
            headers={
                "x-rate-limit-limit": "50",
                "x-rate-limit-remaining": str(49 - index),
                "x-rate-limit-reset": str(reset),
            },
        )
        for index in range(50)
    )
    with TestClient(app, base_url=settings.origin) as client:
        signed_in(client)
        services = app.state
        services.archive.import_bytes(archive_bytes(60))
        detail = plan(services, cap=50, batch=25)
        confirm(services, detail)
        for _ in range(25):
            services.runs.tick()
        half = services.runs.detail("12345", detail["id"])
        assert half["quota"]["remaining"] == 25
        assert half["schedule"]["waiting_until"] == 0
        if legacy_restart:
            with services.db.connect(write=True) as db:
                db.execute(
                    "UPDATE run_schedule SET waiting_until=? WHERE run_id=?", (reset, detail["id"])
                )
            services.runs.recover()
            services.runs.tick()
            assert len(fake.calls) == 25
            assert services.runs.detail("12345", detail["id"])["status"] == "paused"
            confirm(services, detail)
        for _ in range(25):
            services.runs.tick()
        result = services.runs.detail("12345", detail["id"])
        assert result["status"] == "completed" and result["counts"] == {"deleted": 50}
        assert len(fake.calls) == 50 and all(c.method == "DELETE" for c in fake.calls)
        assert clock[0] < reset
        assert services.archive.stats()["available"] == 10


def test_batch_25_waits_after_only_the_actual_10_remaining_requests(settings, fake, monkeypatch):
    settings = replace(settings, run_max=50, fallback_limit=50)
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    with TestClient(app, base_url=settings.origin) as client:
        signed_in(client)
        services = app.state
        services.archive.import_bytes(archive_bytes(60))
        with services.db.connect(write=True) as db:
            db.execute(
                "INSERT INTO quota VALUES(?,?,?,?,?)", ("12345", 50, 10, clock[0] + 600, clock[0])
            )
        detail = plan(services, cap=50, batch=25)
        confirm(services, detail)
        for _ in range(11):
            services.runs.tick()
        result = services.runs.detail("12345", detail["id"])
        assert len(fake.calls) == 10
        assert result["schedule"]["waiting_until"] == clock[0] + 600
        services.runs.tick()
        assert len(fake.calls) == 10
        clock[0] += 601
        for _ in range(40):
            services.runs.tick()
        assert services.runs.detail("12345", detail["id"])["status"] == "completed"
        assert len(fake.calls) == 50
