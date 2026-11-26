import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.provider import XClient
from app.sessions import COOKIE


class FakeX:
    def __init__(self) -> None:
        self.calls: list[httpx.Request] = []
        self.replies: list[httpx.Response | Exception] = []
        self.identity = "12345"
        self.avatar = "https://pbs.twimg.com/profile_images/12345/synthetic_normal.jpg"

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path == "/2/oauth2/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-access",
                    "refresh_token": "synthetic-refresh",
                    "expires_in": 7200,
                    "token_type": "bearer",
                    "scope": "tweet.read tweet.write users.read offline.access",
                },
            )
        if request.url.path == "/2/users/me":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": self.identity,
                        "name": "Test Owner",
                        "username": "test_owner",
                        "profile_image_url": self.avatar,
                    }
                },
            )
        if request.url.path == "/2/oauth2/revoke":
            return httpx.Response(200, json={"revoked": True})
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return httpx.Response(200, json={"data": {"deleted": True}})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        origin="http://127.0.0.1:8000",
        encryption_key=Fernet.generate_key().decode(),
        client_id="synthetic-client",
        root=tmp_path,
        fallback_limit=3,
        run_max=3,
        upload_budget=10,
    )


@pytest.fixture
def fake() -> FakeX:
    return FakeX()


@pytest.fixture
def client(settings: Settings, fake: FakeX) -> Iterator[TestClient]:
    app = create_app(settings, XClient(settings, httpx.MockTransport(fake.respond)), worker=False)
    with TestClient(app, base_url=settings.origin, raise_server_exceptions=False) as client:
        yield client


def signed_in(client: TestClient) -> dict[str, str]:
    services = client.app.state
    with services.db.connect(write=True) as db:
        db.execute("INSERT INTO meta VALUES('owner','12345')")
        db.execute(
            "INSERT INTO accounts(id,username,name,tokens,token_expires) VALUES(?,?,?,?,?)",
            (
                "12345",
                "test_owner",
                "Test Owner",
                services.auth.seal({"access": "synthetic-access", "refresh": "synthetic-refresh"}),
                time.time() + 7200,
            ),
        )
    token, session = services.sessions.create("12345")
    client.cookies.clear()
    client.cookies.set(COOKIE, token, domain="127.0.0.1", path="/")
    return {"Origin": services.settings.origin, "X-CSRF-Token": session.csrf}


def archive_bytes(count: int = 5) -> bytes:
    return (
        "window.YTD.tweets.part0 = "
        + json.dumps(
            [
                {
                    "tweet": {
                        "id_str": str(1000 + index),
                        "full_text": f"Post {index} example.com",
                        "created_at": "Mon Jan 01 12:00:00 +0000 2024",
                        "entities": {"urls": [{"expanded_url": "https://example.com/read"}]},
                    }
                }
                for index in range(count)
            ]
        )
    ).encode()


@pytest.fixture
def ready(client: TestClient) -> tuple[TestClient, dict[str, str], Any]:
    headers = signed_in(client)
    client.app.state.archive.import_bytes(archive_bytes())
    return client, headers, client.app.state
