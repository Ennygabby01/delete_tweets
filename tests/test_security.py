import asyncio
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import archive_bytes, signed_in
from fastapi.testclient import TestClient

from app.main import create_app
from app.provider import XClient
from app.sessions import COOKIE


def test_private_routes_are_denied_before_body(client):
    for path in ["/v1/workspace", "/v1/posts", "/v1/runs/not-a-run"]:
        response = client.get(path)
        assert response.status_code == 401
        assert "no-store" in response.headers["cache-control"]
    response = client.post(
        "/v1/archive",
        content=b"not an archive",
        headers={"Origin": str(client.base_url).rstrip("/")},
    )
    assert response.status_code == 401
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_csrf_origins_hosts_extra_headers_and_framing(ready):
    client, headers, _ = ready
    body = {"kind": "keyword", "value": "Post"}
    assert client.post("/v1/filters", json=body).status_code == 403
    assert (
        client.post("/v1/filters", json=body, headers={"Origin": headers["Origin"]}).status_code
        == 403
    )
    assert (
        client.post(
            "/v1/filters", json=body, headers={**headers, "Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.get("/v1/posts", headers={"Host": "evil.example"}).status_code == 400
    assert (
        client.get(
            "/v1/posts", headers={"X-Unknown-Benign": "fine", "X-Forwarded-For": "8.8.8.8"}
        ).status_code
        == 200
    )
    assert client.get("/v1/posts", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert (
        client.get(
            "/v1/posts", headers=[("Host", "127.0.0.1:8000"), ("Host", "evil.example")]
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/filters", json=body, headers=[*headers.items(), ("X-CSRF-Token", "duplicate")]
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/v1/filters", json=body, headers={**headers, "Content-Encoding": "gzip"}
        ).status_code
        == 415
    )
    assert client.post("/v1/filters", json=body, headers=headers).status_code == 200


def test_bounded_content_length_and_chunked_body(ready):
    client, headers, services = ready
    body = b"x" * (services.settings.json_bytes + 1)
    response = client.post(
        "/v1/filters", content=body, headers={**headers, "Content-Type": "application/json"}
    )
    assert response.status_code == 413
    response = client.post("/v1/filters", content=iter([body[:1000], body[1000:]]), headers=headers)
    assert response.status_code == 413
    response = client.get("/v1/posts", headers={"X-Long": "x" * 20000})
    assert response.status_code == 431


def test_session_idle_absolute_and_logout(ready, fake):
    client, headers, services = ready
    with services.db.connect(write=True) as db:
        db.execute(
            "UPDATE sessions SET touched=?", (time.time() - services.settings.session_idle - 1,)
        )
    assert client.get("/v1/posts").status_code == 401
    token, session = services.sessions.create("12345")
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    headers["X-CSRF-Token"] = session.csrf
    with services.db.connect(write=True) as db:
        db.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert client.post("/v1/session/activity", headers=headers).status_code == 401
    token, session = services.sessions.create("12345")
    other, _ = services.sessions.create("12345")
    client.cookies.clear()
    client.cookies.set(COOKIE, token)
    headers["X-CSRF-Token"] = session.csrf
    assert client.post("/v1/auth/logout", headers=headers).status_code == 200
    assert services.sessions.get(other) is None
    assert client.get("/v1/posts").status_code == 401
    assert any(call.url.path.endswith("/revoke") for call in fake.calls)


def begin(client):
    session = client.get("/v1/session").json()
    headers = {"Origin": str(client.base_url).rstrip("/"), "X-CSRF-Token": session["csrf"]}
    url = client.post("/v1/auth/start", headers=headers).json()["url"]
    return parse_qs(urlsplit(url).query), headers


def test_oauth_pkce_cookie_binding_rotation_and_replay(client, settings, fake):
    query, _ = begin(client)
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    assert query["redirect_uri"] == [settings.callback]
    old_cookie = client.cookies.get(COOKIE)
    url = "/v1/auth/callback?state=" + query["state"][0] + "&code=synthetic-code"
    client.cookies.clear()
    response = client.get(url, follow_redirects=False)
    assert response.headers["location"] == "/?auth=failed"
    client.cookies.set(COOKIE, old_cookie, domain="127.0.0.1", path="/")
    response = client.get(url, follow_redirects=False)
    assert response.headers["location"] == "/?auth=connected"
    assert client.cookies.get(COOKIE) != old_cookie
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/v1/session").json()["authenticated"] is True
    assert client.get(url, follow_redirects=False).headers["location"] == "/?auth=failed"
    with client.app.state.db.connect() as db:
        ciphertext = db.execute("SELECT tokens FROM accounts").fetchone()[0]
        assert "synthetic-access" not in ciphertext
        assert db.execute("SELECT count(*) FROM oauth").fetchone()[0] == 0
    assert len([r for r in fake.calls if r.url.path.endswith("/token")]) == 1


def test_oauth_state_expiry_denial_and_account_binding(client, fake):
    query, _ = begin(client)
    with client.app.state.db.connect(write=True) as db:
        db.execute("UPDATE oauth SET expires=0")
    assert (
        client.get(
            "/v1/auth/callback",
            params={"state": query["state"][0], "code": "synthetic"},
            follow_redirects=False,
        ).headers["location"]
        == "/?auth=failed"
    )
    query, _ = begin(client)
    assert (
        client.get(
            "/v1/auth/callback",
            params={"state": query["state"][0], "error": "access_denied"},
            follow_redirects=False,
        ).headers["location"]
        == "/?auth=failed"
    )
    assert not fake.calls
    signed_in(client)
    query, _ = begin(client)
    fake.identity = "99999"
    assert (
        client.get(
            "/v1/auth/callback",
            params={"state": query["state"][0], "code": "synthetic"},
            follow_redirects=False,
        ).headers["location"]
        == "/?auth=failed"
    )
    with client.app.state.db.connect() as db:
        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_invalid_archive_and_safe_errors(ready):
    client, headers, services = ready
    before = services.archive.stats()
    for payload in [b"not JSON", b"[1]", b'[{"tweet":{"id_str":"../escape"}}]', b"[{}]"]:
        response = client.post(
            "/v1/archive",
            content=payload,
            headers={**headers, "Content-Type": "application/octet-stream"},
        )
        assert response.status_code == 400
        assert "/Users/" not in response.text
    assert services.archive.stats() == before
    response = client.post(
        "/v1/archive", content=archive_bytes(2), headers={**headers, "Content-Type": "text/plain"}
    )
    assert response.status_code == 415
    assert (
        client.post(
            "/v1/filters", json={"kind": "keyword", "value": "x", "admin": True}, headers=headers
        ).status_code
        == 422
    )


def test_security_headers_and_no_cdn(client):
    response = client.get("/")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "cdn." not in response.text and "<!-- ─" not in response.text


def test_invalid_auth_attempts_consume_pre_auth_budget(client):
    origin = str(client.base_url).rstrip("/")
    for _ in range(60):
        assert client.post("/v1/auth/start", headers={"Origin": origin}).status_code == 403
    assert client.post("/v1/auth/start", headers={"Origin": origin}).status_code == 429


def test_request_body_timeout_and_no_unauthorized_consumption(settings, fake):
    limited = replace(settings, body_timeout=1)
    app = create_app(limited, XClient(limited, httpx.MockTransport(fake.respond)), worker=False)
    with TestClient(app, base_url=settings.origin) as client:
        headers = signed_in(client)
        cookie = client.cookies.get(COOKIE)

        async def exercise(authenticated):
            consumed, messages = 0, []

            async def receive():
                nonlocal consumed
                consumed += 1
                await asyncio.sleep(2)
                return {"type": "http.request", "body": b"x", "more_body": True}

            async def send(message):
                messages.append(message)

            pairs = [(b"host", b"127.0.0.1:8000"), (b"origin", settings.origin.encode())]
            if authenticated:
                pairs += [
                    (b"cookie", f"{COOKIE}={cookie}".encode()),
                    (b"x-csrf-token", headers["X-CSRF-Token"].encode()),
                ]
            scope = {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "path": "/v1/archive",
                "raw_path": b"/v1/archive",
                "query_string": b"",
                "scheme": "http",
                "server": ("127.0.0.1", 8000),
                "client": ("127.0.0.1", 1234),
                "headers": pairs,
            }
            await app(scope, receive, send)
            return consumed, messages[0]["status"]

        assert asyncio.run(exercise(False)) == (0, 401)
        assert asyncio.run(exercise(True)) == (1, 408)


def test_callback_duplicate_fields_fail_before_token_exchange(client, fake):
    query, _ = begin(client)
    response = client.get(
        "/v1/auth/callback",
        params=[("state", query["state"][0]), ("state", "different"), ("code", "synthetic")],
    )
    assert response.status_code == 400
    assert not fake.calls


def test_polling_does_not_extend_idle_session(ready):
    client, _, services = ready
    with services.db.connect(write=True) as db:
        db.execute("UPDATE sessions SET touched=?", (time.time() - 300,))
        before = db.execute("SELECT touched FROM sessions").fetchone()[0]
    assert client.get("/v1/session").status_code == 200
    assert client.get("/v1/workspace").status_code == 200
    with services.db.connect() as db:
        assert db.execute("SELECT touched FROM sessions").fetchone()[0] == before


def test_ambiguous_duplicate_session_cookie_is_rejected(ready):
    client, _, _ = ready
    response = client.get("/v1/posts", headers={"Cookie": "td_session=one; td_session=two"})
    assert response.status_code == 400
    assert "Conflicting session" in response.json()["detail"]


def test_internal_failures_return_safe_public_response(ready, monkeypatch):
    client, _, services = ready

    def fail(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_SENTINEL /private/path SQL SELECT token=value")

    monkeypatch.setattr(services.archive, "list", fail)
    response = client.get("/v1/posts")
    assert response.status_code == 500
    assert response.json() == {"detail": "The request could not be completed."}
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "avatar",
    [
        "https://pbs.twimg.com/profile_images/12345/synthetic_normal.jpg",
        "https://pbs.twimg.com/sticky/default_profile_images/default_profile_normal.png",
        "https://pbs.twimg.com.evil.example/profile_images/12345/image.jpg",
        "https://evil.example/profile.jpg",
        "https://pbs.twimg.com/profile_images/../../private.jpg",
        "https://user:secret@pbs.twimg.com/profile_images/12345/image.jpg",
        "javascript:alert(1)",
        "http://127.0.0.1/private.jpg",
    ],
)
def test_profile_photo_cached_at_sign_in_with_narrow_image_policy(client, fake, avatar):
    fake.avatar = avatar
    query, _ = begin(client)
    response = client.get(
        "/v1/auth/callback",
        params={"state": query["state"][0], "code": "synthetic"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/?auth=connected"
    calls = len(fake.calls)
    expected = (
        avatar
        if avatar
        in {
            "https://pbs.twimg.com/profile_images/12345/synthetic_normal.jpg",
            "https://pbs.twimg.com/sticky/default_profile_images/default_profile_normal.png",
        }
        else ""
    )
    for _ in range(3):
        session = client.get("/v1/session")
        assert session.json()["profile"]["profile_image_url"] == expected
    assert len(fake.calls) == calls
    profile_call = next(r for r in fake.calls if r.url.path == "/2/users/me")
    assert profile_call.url.params["user.fields"] == "profile_image_url"
    policy = session.headers["content-security-policy"]
    assert "https://pbs.twimg.com/profile_images/" in policy
    assert "https://pbs.twimg.com/sticky/default_profile_images/" in policy
    assert "*" not in policy


def test_workspace_polling_does_not_extend_idle_deadline_but_activity_does(ready):
    client, headers, services = ready
    original = time.time() - 120
    with services.db.connect(write=True) as db:
        db.execute("UPDATE sessions SET touched=?", (original,))
    for _ in range(3):
        assert client.get("/v1/workspace").status_code == 200
    with services.db.connect() as db:
        before = db.execute("SELECT touched,expires FROM sessions").fetchone()
        assert before["touched"] == original
    assert client.post("/v1/session/activity", headers=headers).status_code == 200
    with services.db.connect() as db:
        after = db.execute("SELECT touched,expires FROM sessions").fetchone()
        assert after["touched"] > original
        assert after["expires"] == before["expires"]
