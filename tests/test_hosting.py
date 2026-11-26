from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import Settings
from app.main import create_app
from app.provider import XClient
from scripts import serve


def hosted(settings, **changes):
    return replace(
        settings,
        origin="https://delete.example.com",
        owner_id="12345",
        trusted_proxy_ips=("127.0.0.1",),
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "http://delete.example.com"},
        {"origin": "https://delete.example.com", "owner_id": "12345"},
        {"origin": "https://delete.example.com", "trusted_proxy_ips": ("127.0.0.1",)},
        {"origin": "https://delete.example.com/path"},
        {"origin": "https://user:password@delete.example.com"},
        {"origin": "https://delete.example.com?foo=bar"},
        {"origin": "https://delete.example.com#fragment"},
        {"origin": "https://delete.example.com:0"},
        {"origin": "https://delete.example.com:65536"},
        {"origin": "https://bad..example.com"},
        {"bind_host": "0.0.0.0"},
        {"bind_port": 65536},
        {"max_connections": 0},
        {"owner_id": "@someone"},
        {"trusted_proxy_ips": ("*",)},
        {"trusted_proxy_ips": ("127.0.0.0/8",)},
    ],
)
def test_invalid_deployment_configuration_fails_closed(settings, changes):
    with pytest.raises(ValueError):
        replace(settings, **changes)


def test_https_requires_fixed_backend_port(settings):
    with pytest.raises(ValueError, match="fixed"):
        hosted(settings, bind_port=0)


def test_environment_loads_deployment_settings(monkeypatch, settings):
    monkeypatch.setattr("app.config.load_dotenv", lambda *args, **kwargs: None)
    for key, value in {
        "APP_ORIGIN": "https://delete.example.com",
        "APP_BIND_HOST": "127.0.0.2",
        "APP_BIND_PORT": "9123",
        "TRUSTED_PROXY_IPS": "127.0.0.1, ::1",
        "X_OWNER_ID": "12345",
        "MAX_CONNECTIONS": "12",
        "APP_ENCRYPTION_KEY": settings.encryption_key,
        "DATA_DIR": str(settings.data_dir),
    }.items():
        monkeypatch.setenv(key, value)
    result = Settings.load()
    assert result.public
    assert result.bind_host == "127.0.0.2"
    assert result.listen_port == 9123
    assert result.trusted_proxy_ips == ("127.0.0.1", "::1")
    assert result.max_connections == 12
    assert result.callback == "https://delete.example.com/v1/auth/callback"


def test_public_oauth_cookies_and_request_boundaries(settings, fake):
    configuration = hosted(settings)
    app = create_app(
        configuration, XClient(configuration, httpx.MockTransport(fake.respond)), worker=False
    )
    with TestClient(app, base_url=configuration.origin) as client:
        response = client.get("/v1/session")
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "Domain=" not in cookie
        headers = {"Origin": configuration.origin, "X-CSRF-Token": response.json()["csrf"]}
        query = parse_qs(
            urlsplit(client.post("/v1/auth/start", headers=headers).json()["url"]).query
        )
        assert query["redirect_uri"] == [configuration.callback]
        callback = client.get(
            "/v1/auth/callback",
            params={"state": query["state"][0], "code": "synthetic"},
            follow_redirects=False,
        )
        assert callback.headers["location"] == "/?auth=connected"
        assert "Secure" in callback.headers["set-cookie"]
        assert client.get("/v1/workspace").status_code == 200
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("/", headers={"Host": "127.0.0.1"}).status_code == 400
        assert (
            client.get("/v1/posts", headers={"Origin": "https://evil.example"}).status_code == 403
        )
        assert client.post("/v1/auth/logout", headers=headers).status_code == 403
        session = client.get("/v1/session").json()
        headers["X-CSRF-Token"] = session["csrf"]
        response = client.post("/v1/auth/logout", headers=headers)
        assert response.status_code == 200 and "Secure" in response.headers["set-cookie"]
        assert client.get("/v1/workspace").status_code == 401
    assert all(request.method != "DELETE" for request in fake.calls)


def test_public_installation_cannot_be_claimed_by_another_account(settings, fake):
    configuration = hosted(settings)
    fake.identity = "99999"
    app = create_app(
        configuration, XClient(configuration, httpx.MockTransport(fake.respond)), worker=False
    )
    with TestClient(app, base_url=configuration.origin) as client:
        session = client.get("/v1/session").json()
        query = parse_qs(
            urlsplit(
                client.post(
                    "/v1/auth/start",
                    headers={"Origin": configuration.origin, "X-CSRF-Token": session["csrf"]},
                ).json()["url"]
            ).query
        )
        response = client.get(
            "/v1/auth/callback",
            params={"state": query["state"][0], "code": "synthetic"},
            follow_redirects=False,
        )
        assert response.headers["location"] == "/?auth=failed"
        assert client.get("/v1/workspace").status_code == 401
        with app.state.db.connect() as connection:
            assert connection.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
            assert connection.execute("SELECT value FROM meta WHERE key='owner'").fetchone() is None


def test_configured_owner_cannot_replace_existing_owner(settings, fake):
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    with TestClient(app):
        with app.state.db.connect(write=True) as connection:
            connection.execute("INSERT INTO meta VALUES('owner','99999')")
    configuration = hosted(settings)
    app = create_app(
        configuration, XClient(configuration, httpx.MockTransport(fake.respond)), worker=False
    )
    with pytest.raises(ValueError, match="does not match"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "peer,forwarded,status",
    [
        ("127.0.0.1", "https", 200),
        ("127.0.0.1", "http", 400),
        ("198.51.100.1", "https", 400),
    ],
)
def test_proxy_scheme_trust_uses_connecting_peer(settings, fake, peer, forwarded, status):
    configuration = hosted(settings)
    app = create_app(
        configuration, XClient(configuration, httpx.MockTransport(fake.respond)), worker=False
    )
    proxy = ProxyHeadersMiddleware(app, trusted_hosts=list(configuration.trusted_proxy_ips))
    with TestClient(proxy, base_url="http://delete.example.com", client=(peer, 1234)) as client:
        response = client.get(
            "/v1/session",
            headers={
                "X-Forwarded-Proto": forwarded,
                "X-Forwarded-Host": "evil.example",
                "X-Forwarded-For": "203.0.113.2",
            },
        )
        assert response.status_code == status
        if status == 200:
            assert response.json()["callback_url"] == configuration.callback
            assert "Secure" in response.headers["set-cookie"]
        else:
            assert "set-cookie" not in response.headers
        assert not fake.calls


def test_public_launcher_preserves_domain_and_proxy_options(settings, monkeypatch):
    configuration = hosted(settings)
    monkeypatch.setenv("APP_ORIGIN", settings.origin)
    monkeypatch.setattr(serve.os, "chdir", lambda directory: None)
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    with serve.reserve(configuration.bind_host, 0) as listener:
        port = listener.getsockname()[1]
    serve.launch(configuration, port=port)
    import os

    assert os.environ["APP_ORIGIN"] == configuration.origin
    assert calls[0]["proxy_headers"] is True
    assert calls[0]["forwarded_allow_ips"] == "127.0.0.1"
    assert calls[0]["workers"] == 1
    with serve.reserve(configuration.bind_host, 0) as occupied:
        with pytest.raises(OSError):
            serve.launch(configuration, port=occupied.getsockname()[1])
    assert len(calls) == 1
    with pytest.raises(ValueError, match="fixed"):
        serve.launch(configuration, port=0)
    with pytest.raises(ValueError, match="no reload"):
        serve.launch(configuration, reload=True)


def test_local_launcher_keeps_auto_port_and_non_secure_cookie(settings, fake, monkeypatch):
    monkeypatch.setenv("APP_ORIGIN", settings.origin)
    monkeypatch.setattr(serve.os, "chdir", lambda directory: None)
    calls = []
    monkeypatch.setattr(serve.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs))
    with serve.reserve(settings.bind_host, 0) as occupied:
        serve.launch(settings, port=occupied.getsockname()[1])
    assert calls[0]["proxy_headers"] is False
    assert calls[0]["forwarded_allow_ips"] == ""
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    with TestClient(app, base_url=settings.origin) as client:
        assert "Secure" not in client.get("/v1/session").headers["set-cookie"]


def test_canonical_origin_removes_default_and_padded_ports(settings):
    assert replace(hosted(settings), origin="https://delete.example.com:443").origin == (
        "https://delete.example.com"
    )
    assert replace(hosted(settings), origin="https://delete.example.com:08443").origin == (
        "https://delete.example.com:8443"
    )
    for origin in ("https://delete.example.com?", "https://delete.example.com#"):
        with pytest.raises(ValueError):
            replace(hosted(settings), origin=origin)
