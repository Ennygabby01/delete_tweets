"""PKCE authorization, account binding and encrypted refresh-token lifecycle."""

import base64
import hashlib
import json
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.errors import AppError, ProviderError
from app.provider import AUTHORIZE, SCOPES, XClient, profile_image_url
from app.sessions import Session, Sessions, digest
from app.storage import Database


class Auth:
    def __init__(
        self, db: Database, sessions: Sessions, settings: Settings, provider: XClient
    ) -> None:
        self.db, self.sessions, self.settings, self.provider = db, sessions, settings, provider
        try:
            self.cipher = Fernet(settings.encryption_key.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError(
                "Missing/invalid APP_ENCRYPTION_KEY. Run make setup; keep .env private."
            ) from exc
        self.lock = threading.RLock()

    def seal(self, value: Any) -> str:
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def open(self, value: str) -> Any:
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError) as exc:
            raise AppError(
                "Local credentials cannot be unlocked. Restore the matching .env key.", 503
            ) from exc

    def start(self, session: Session) -> str:
        if not self.settings.client_id:
            raise AppError("Add X_CLIENT_ID to your private .env, then restart the app.", 503)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        now = time.time()
        with self.db.connect(write=True) as db:
            db.execute("DELETE FROM oauth WHERE expires<? OR session=?", (now, session.digest))
            db.execute(
                "INSERT INTO oauth VALUES(?,?,?,?)",
                (digest(state), session.digest, self.seal(verifier), now + 600),
            )
        return (
            AUTHORIZE
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": self.settings.client_id,
                    "redirect_uri": self.settings.callback,
                    "scope": " ".join(sorted(SCOPES)),
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def callback(self, session: Session, state: str, code: str | None, denied: bool) -> str:
        with self.db.connect(write=True) as db:
            row = db.execute(
                "DELETE FROM oauth WHERE state=? AND session=? AND expires>? RETURNING verifier",
                (digest(state), session.digest, time.time()),
            ).fetchone()
        if not row:
            raise AppError("This sign-in link expired. Start sign-in again.", 400)
        if denied or not code:
            raise AppError("X sign-in was cancelled. You can try again.", 400)
        tokens = self.provider.tokens(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.callback,
                "code_verifier": self.open(row["verifier"]),
            }
        )
        identity = self.provider.identity(tokens["access"])
        if self.settings.owner_id and identity["id"] != self.settings.owner_id:
            raise AppError("This installation belongs to a different X account.", 403)
        with self.lock, self.db.connect(write=True) as db:
            owner = db.execute("SELECT value FROM meta WHERE key='owner' ").fetchone()
            if owner and owner[0] != identity["id"]:
                raise AppError("This installation belongs to a different X account.", 403)
            db.execute("INSERT OR IGNORE INTO meta VALUES('owner',?)", (identity["id"],))
            db.execute(
                "INSERT INTO accounts(id,username,name,tokens,token_expires) VALUES(?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username,name=excluded.name,"
                "tokens=excluded.tokens,token_expires=excluded.token_expires,refresh_state='ready'",
                (
                    identity["id"],
                    identity["username"],
                    identity["name"],
                    self.seal(tokens),
                    time.time() + tokens["expires_in"],
                ),
            )
            db.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("profile_image:" + identity["id"], identity["profile_image_url"]),
            )
        return identity["id"]

    def access_token(self, user_id: str) -> str:
        # One server process, plus a durable claim so crash/restart cannot reuse a
        # refresh token whose provider-side rotation outcome is unknown.
        with self.lock:
            with self.db.connect() as db:
                account = db.execute("SELECT * FROM accounts WHERE id=?", (user_id,)).fetchone()
            if not account or not account["tokens"]:
                raise AppError("Connect your X account again.", 401)
            if account["refresh_state"] != "ready":
                raise AppError("Token renewal was interrupted. Reconnect X before continuing.", 409)
            tokens = self.open(account["tokens"])
            if account["token_expires"] > time.time() + 60:
                return str(tokens["access"])
            with self.db.connect(write=True) as db:
                changed = db.execute(
                    "UPDATE accounts SET refresh_state='refreshing' "
                    "WHERE id=? AND refresh_state='ready' AND tokens IS NOT NULL",
                    (user_id,),
                ).rowcount
                if changed != 1:
                    raise AppError("Account renewal is already in progress. Connect again.", 401)
            try:
                renewed = self.provider.tokens(
                    {"grant_type": "refresh_token", "refresh_token": tokens["refresh"]}
                )
            except ProviderError as exc:
                if exc.status == 401:
                    self.disconnect(user_id)
                raise
            with self.db.connect(write=True) as db:
                db.execute(
                    "UPDATE accounts SET tokens=?,token_expires=?,refresh_state='ready' WHERE id=?",
                    (self.seal(renewed), time.time() + renewed["expires_in"], user_id),
                )
            return str(renewed["access"])

    def disconnect(self, user_id: str) -> None:
        with self.lock, self.db.connect(write=True) as db:
            db.execute(
                "UPDATE runs SET status='paused',note='X account disconnected.' "
                "WHERE user_id=? AND status='running'",
                (user_id,),
            )
            db.execute(
                "UPDATE accounts SET tokens=NULL,token_expires=0,refresh_state='ready' WHERE id=?",
                (user_id,),
            )
            db.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def logout(self, user_id: str) -> str:
        with self.db.connect() as db:
            account = db.execute("SELECT tokens FROM accounts WHERE id=?", (user_id,)).fetchone()
        sealed = account["tokens"] if account else None
        self.disconnect(user_id)
        revoked = False
        if sealed:
            try:
                tokens = self.open(sealed)
                reply = self.provider.request(
                    "POST",
                    "/oauth2/revoke",
                    form={"token": tokens["access"], "client_id": self.settings.client_id},
                    token_endpoint=True,
                )
                revoked = reply.status == 200
            except AppError:
                revoked = False
        return (
            "Disconnected locally and revoked on X."
            if revoked
            else "Disconnected locally. You can also revoke this app in X Settings."
        )

    def profile(self, user_id: str) -> dict[str, str]:
        with self.db.connect() as db:
            row = db.execute(
                "SELECT id,username,name FROM accounts WHERE id=?", (user_id,)
            ).fetchone()
            avatar = db.execute(
                "SELECT value FROM meta WHERE key=?", ("profile_image:" + user_id,)
            ).fetchone()
        if not row:
            raise AppError("Connect your X account again.", 401)
        return {**dict(row), "profile_image_url": profile_image_url(avatar[0]) if avatar else ""}
