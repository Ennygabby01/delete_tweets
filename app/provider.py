"""Fixed X API destinations, bounded HTTP, validated provider payloads."""

import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app import __version__
from app.config import Settings
from app.errors import ProviderError

API = "https://api.x.com/2"
AUTHORIZE = "https://x.com/i/oauth2/authorize"
SCOPES = {"tweet.read", "tweet.write", "users.read", "offline.access"}


def profile_image_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ""
    if not re.fullmatch(
        r"https://pbs\.twimg\.com/(?:profile_images|sticky/default_profile_images)/"
        r"[A-Za-z0-9_/-]+\.(?:jpg|jpeg|png|webp)",
        value,
    ):
        return ""
    return value


@dataclass(frozen=True)
class Reply:
    status: int
    data: dict[str, Any]
    headers: httpx.Headers


class XClient:
    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.request_timeout,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": f"TweetDeleter/{__version__}"},
        )

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        form: dict[str, str] | None = None,
        token_endpoint: bool = False,
    ) -> Reply:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        auth = None
        if token_endpoint and self.settings.client_secret:
            auth = httpx.BasicAuth(self.settings.client_id, self.settings.client_secret)
        deadline = time.monotonic() + self.settings.request_timeout
        try:
            with self.client.stream(
                method, API + path, headers=headers, data=form, auth=auth
            ) as response:
                content = bytearray()
                for block in response.iter_bytes():
                    if time.monotonic() > deadline:
                        raise ProviderError(
                            "X took too long to respond.", uncertain=method == "DELETE"
                        )
                    content.extend(block)
                    if len(content) > self.settings.response_bytes:
                        raise ProviderError(
                            "X returned an oversized response.", uncertain=method == "DELETE"
                        )
                try:
                    payload = json.loads(content) if content else {}
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ProviderError(
                        "X returned an unreadable response.", uncertain=method == "DELETE"
                    ) from exc
                if not isinstance(payload, dict):
                    raise ProviderError(
                        "X returned an unexpected response.", uncertain=method == "DELETE"
                    )
                return Reply(response.status_code, payload, response.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(
                "X could not be reached. Try again later.", uncertain=method == "DELETE"
            ) from exc

    def tokens(self, form: dict[str, str]) -> dict[str, Any]:
        form = {**form, "client_id": self.settings.client_id}
        reply = self.request("POST", "/oauth2/token", form=form, token_endpoint=True)
        if reply.status != 200:
            if reply.data.get("error") == "invalid_grant":
                raise ProviderError("X authorization expired or was revoked. Sign in again.", 401)
            raise ProviderError(
                "X could not renew authorization. Reconnect X before continuing.", 503
            )
        data = reply.data
        access, refresh = data.get("access_token"), data.get("refresh_token")
        expires, scope = data.get("expires_in"), data.get("scope")
        if (
            not isinstance(access, str)
            or not 1 <= len(access) <= 8192
            or not isinstance(refresh, str)
            or not 1 <= len(refresh) <= 8192
            or not isinstance(expires, int)
            or not 1 <= expires <= 31_536_000
            or not isinstance(scope, str)
            or not SCOPES.issubset(set(scope.split()))
            or str(data.get("token_type", "")).lower() != "bearer"
        ):
            raise ProviderError("X did not grant the required permissions. Connect again.", 401)
        return {"access": access, "refresh": refresh, "expires_in": expires}

    def identity(self, token: str) -> dict[str, str]:
        reply = self.request("GET", "/users/me?user.fields=profile_image_url", token=token)
        data = reply.data.get("data")
        if reply.status != 200 or not isinstance(data, dict):
            raise ProviderError(
                "X could not verify your account. Check your app's API access.", 401
            )
        uid, name, username = data.get("id"), data.get("name"), data.get("username")
        if (
            not isinstance(uid, str)
            or not uid.isascii()
            or not uid.isdigit()
            or len(uid) > 25
            or not isinstance(name, str)
            or len(name) > 256
            or not isinstance(username, str)
            or not 1 <= len(username) <= 64
        ):
            raise ProviderError("X returned an invalid account profile.")
        return {
            "id": uid,
            "name": name,
            "username": username,
            "profile_image_url": profile_image_url(data.get("profile_image_url")),
        }

    def delete(self, token: str, post_id: str) -> Reply:
        return self.request("DELETE", "/tweets/" + post_id, token=token)

    def lookup(self, token: str, post_id: str) -> Reply:
        return self.request("GET", "/tweets/" + post_id + "?tweet.fields=author_id", token=token)

    def timeline(self, token: str, user_id: str, cursor: str | None = None) -> Reply:
        params = {
            "max_results": str(self.settings.sync_page_size),
            "exclude": "retweets",
            "post.fields": "created_at,entities,note_post",
            "expansions": "author_id",
        }
        if cursor:
            params["pagination_token"] = cursor
        return self.request("GET", f"/users/{user_id}/tweets?" + urlencode(params), token=token)

    def lookup_many(self, token: str, ids: list[str]) -> Reply:
        params = {
            "ids": ",".join(ids),
            "post.fields": "created_at,entities,note_post",
            "expansions": "author_id",
        }
        return self.request("GET", "/tweets?" + urlencode(params), token=token)
