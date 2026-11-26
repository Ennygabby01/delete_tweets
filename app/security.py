"""Canonical-origin admission, server sessions, CSRF and bounded requests."""

import asyncio
import json
import secrets
import sqlite3
import time
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlsplit

from app.config import Settings
from app.sessions import COOKIE, Sessions
from app.storage import Database

HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' https://pbs.twimg.com/profile_images/ "
    "https://pbs.twimg.com/sticky/default_profile_images/; connect-src 'self'; font-src 'self'; base-uri 'none'; "
    "form-action 'self'; frame-ancestors 'none'; object-src 'none'",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
    "cache-control": "no-store",
    "cross-origin-opener-policy": "same-origin-allow-popups",
}


class SecurityMiddleware:
    def __init__(self, app: Any, settings: Settings, db: Database, sessions: Sessions) -> None:
        self.app, self.settings, self.db, self.sessions = app, settings, db, sessions

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings, path, method = self.settings, scope["path"], scope["method"]

        async def safe_send(message: Any) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.decode().lower() not in HEADERS
                ]
                message["headers"] += [(k.encode(), v.encode()) for k, v in HEADERS.items()]
            await send(message)

        async def reject(code: int, detail: str) -> None:
            await safe_send(
                {
                    "type": "http.response.start",
                    "status": code,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await safe_send(
                {"type": "http.response.body", "body": json.dumps({"detail": detail}).encode()}
            )

        pairs = scope.get("headers", [])
        if (
            len(pairs) > settings.header_count
            or sum(len(k) + len(v) for k, v in pairs) > settings.header_bytes
        ):
            await reject(431, "Request headers are too large.")
            return
        headers: dict[str, str] = {}
        for key, value in pairs:
            name = key.decode("latin1").lower()
            if name in headers and name in {
                "host",
                "origin",
                "content-length",
                "authorization",
                "x-csrf-token",
                "cookie",
            }:
                await reject(400, "Conflicting request headers.")
                return
            headers[name] = value.decode("latin1")
        canonical = urlsplit(settings.origin).netloc
        if settings.public and scope.get("scheme") != "https":
            await reject(400, "Use the configured HTTPS application URL.")
            return
        if headers.get("host") != canonical:
            # Keep one canonical browser origin so cookies/CSRF/callbacks agree.
            alternate = "localhost" if canonical.startswith("127.0.0.1") else "127.0.0.1"
            port = urlsplit(settings.origin).port
            if port:
                alternate += f":{port}"
            if (
                not settings.public
                and path == "/"
                and method == "GET"
                and headers.get("host") == alternate
            ):
                await safe_send(
                    {
                        "type": "http.response.start",
                        "status": 307,
                        "headers": [(b"location", (settings.origin + "/").encode())],
                    }
                )
                await safe_send({"type": "http.response.body", "body": b""})
                return
            await reject(400, "Use the configured application URL.")
            return
        if headers.get("content-encoding", "identity") != "identity":
            await reject(415, "Compressed request bodies are not supported.")
            return
        callback = path == "/v1/auth/callback" and method == "GET"
        if callback:
            raw_query = scope.get("query_string", b"")
            if len(raw_query) > settings.header_bytes:
                await reject(400, "Invalid sign-in response.")
                return
            values = parse_qs(raw_query.decode("latin1"), keep_blank_values=True)
            if any(len(values.get(field, [])) > 1 for field in ("state", "code", "error")) or (
                "code" in values and "error" in values
            ):
                await reject(400, "Conflicting sign-in response fields.")
                return
        api = path.startswith("/v1/")
        unsafe = method not in {"GET", "HEAD", "OPTIONS"}
        if api and not callback:
            if headers.get("origin", settings.origin) != settings.origin:
                await reject(403, "Request origin is not allowed.")
                return
            if headers.get("sec-fetch-site", "none") not in {"same-origin", "none"}:
                await reject(403, "Open the application directly to continue.")
                return
        if unsafe and headers.get("origin") != settings.origin:
            await reject(403, "Request origin is required.")
            return
        if "authorization" in headers:
            await reject(401, "Use the browser sign-in flow.")
            return
        cookie: SimpleCookie = SimpleCookie()
        raw_cookie = headers.get("cookie", "")
        if sum(part.strip().split("=", 1)[0] == COOKIE for part in raw_cookie.split(";")) > 1:
            await reject(400, "Conflicting session cookies.")
            return
        try:
            cookie.load(raw_cookie)
        except CookieError:
            await reject(400, "Invalid session cookie.")
            return
        try:
            auth_route = path in {"/v1/session", "/v1/auth/start", "/v1/auth/callback"}
            if api and auth_route:
                allowed = await asyncio.to_thread(
                    self.db.budget, "auth-local", settings.auth_budget, 600, time.time()
                )
                if not allowed:
                    await reject(429, "Too many sign-in requests. Wait and try again.")
                    return
            session = await asyncio.to_thread(
                self.sessions.get,
                cookie[COOKIE].value if COOKIE in cookie else None,
                activity=False,
            )
            public = path in {"/v1/session", "/v1/auth/start", "/v1/auth/callback"}
            if api and not public and (session is None or session.user_id is None):
                await reject(401, "Sign in with X to continue.")
                return
            if unsafe and api:
                csrf = headers.get("x-csrf-token", "")
                if not session or not secrets.compare_digest(csrf, session.csrf):
                    await reject(403, "Session verification failed. Refresh and try again.")
                    return
            if api and not auth_route:
                # Principal budgets are separate from pre-authentication admission.
                maximum, window = ((settings.write_budget if unsafe else settings.read_budget), 60)
                key = f"{'write' if unsafe else 'read'}:{session.user_id if session else 'local'}"
                if path == "/v1/archive" and unsafe:
                    maximum, window = settings.upload_budget, 600
                    key = f"upload:{session.user_id if session else 'local'}"
                allowed = await asyncio.to_thread(self.db.budget, key, maximum, window, time.time())
                if not allowed:
                    await reject(429, "Too many requests. Wait a moment and try again.")
                    return
            if unsafe and api and session:
                await asyncio.to_thread(self.sessions.get, cookie[COOKIE].value, activity=True)
            scope.setdefault("state", {})["session"] = session
        except sqlite3.Error:
            await reject(503, "Local storage is temporarily unavailable.")
            return
        if unsafe:
            maximum = settings.upload_bytes if path == "/v1/archive" else settings.json_bytes
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                await reject(400, "Invalid body length.")
                return
            if length < 0 or length > maximum:
                await reject(413, "Request is too large.")
                return
            body = bytearray()
            try:
                async with asyncio.timeout(settings.body_timeout):
                    while True:
                        part = await receive()
                        if part["type"] == "http.disconnect":
                            return
                        block = part.get("body", b"")
                        if len(body) + len(block) > maximum:
                            await reject(413, "Request is too large.")
                            return
                        body.extend(block)
                        if not part.get("more_body"):
                            break
            except TimeoutError:
                await reject(408, "Request body timed out.")
                return
            delivered = False

            async def bounded_receive() -> Any:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            await self.app(scope, bounded_receive, safe_send)
        else:
            await self.app(scope, receive, safe_send)
