"""Environment configuration; private values never enter API responses."""

import os
import re
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    origin: str
    encryption_key: str
    client_id: str = ""
    client_secret: str = ""
    owner_id: str = ""
    bind_host: str = "127.0.0.1"
    bind_port: int | None = None
    trusted_proxy_ips: tuple[str, ...] = ()
    max_connections: int = 64
    session_idle: int = 1800
    session_lifetime: int = 43200
    upload_bytes: int = 67108864
    json_bytes: int = 262144
    body_timeout: int = 30
    header_bytes: int = 16384
    header_count: int = 100
    fallback_limit: int = 50
    quota_window: int = 900
    run_max: int = 50
    schedule_max: int = 5000
    request_timeout: int = 15
    response_bytes: int = 262144
    worker_interval: float = 1.0
    auth_budget: int = 60
    read_budget: int = 600
    write_budget: int = 60
    upload_budget: int = 3
    sync_page_size: int = 100
    sync_interval: int = 2
    auto_sync: bool = False
    sync_retry: int = 60
    root: Path = ROOT

    def __post_init__(self) -> None:
        url = urlsplit(self.origin)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.path
            or url.query
            or url.fragment
            or url.username
            or url.password
            or self.origin != f"{url.scheme}://{url.netloc}"
            or not re.fullmatch(r"[a-z0-9.\[\]:-]+", url.netloc)
            or url.port == 0
        ):
            raise ValueError("APP_ORIGIN must be a canonical HTTP(S) origin without a path")
        hostname = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
        port = url.port
        suffix = "" if port is None or port == (443 if self.public else 80) else f":{port}"
        object.__setattr__(self, "origin", f"{url.scheme}://{hostname}{suffix}")
        if not self.public and url.hostname != "127.0.0.1":
            raise ValueError("Non-local APP_ORIGIN requires HTTPS")
        try:
            ip_address(url.hostname)
        except ValueError:
            if len(url.hostname) > 253 or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in url.hostname.split(".")
            ):
                raise ValueError("APP_ORIGIN must have a valid canonical hostname") from None
        if self.owner_id and not re.fullmatch(r"[0-9]{1,32}", self.owner_id):
            raise ValueError("X_OWNER_ID must be a numeric X account ID")
        if self.public and (not self.owner_id or not self.trusted_proxy_ips):
            raise ValueError("HTTPS hosting requires X_OWNER_ID and TRUSTED_PROXY_IPS")
        address = ip_address(self.bind_host)
        if address.version != 4 or not address.is_loopback:
            raise ValueError("APP_BIND_HOST must be an IPv4 loopback address behind the proxy")
        if not self.public and self.bind_host != url.hostname:
            raise ValueError("Local APP_BIND_HOST must match APP_ORIGIN")
        if self.bind_port is not None and not 0 <= self.bind_port <= 65535:
            raise ValueError("APP_BIND_PORT must be between 0 and 65535")
        if self.public and self.bind_port == 0:
            raise ValueError("HTTPS hosting requires a fixed backend port")
        for proxy in self.trusted_proxy_ips:
            ip_address(proxy)
        for value in (
            self.session_idle,
            self.session_lifetime,
            self.upload_bytes,
            self.json_bytes,
            self.body_timeout,
            self.header_bytes,
            self.header_count,
            self.fallback_limit,
            self.quota_window,
            self.run_max,
            self.request_timeout,
            self.response_bytes,
            self.auth_budget,
            self.read_budget,
            self.write_budget,
            self.upload_budget,
            self.sync_interval,
            self.sync_retry,
            self.max_connections,
        ):
            if value <= 0:
                raise ValueError("Configured limits must be positive")
        if not 5 <= self.sync_page_size <= 100:
            raise ValueError("X_SYNC_PAGE_SIZE must be between 5 and 100")
        if self.worker_interval < 0.1 or self.run_max > 1000:
            raise ValueError("Worker interval or run maximum is out of range")
        if not 1 <= self.schedule_max <= 10000:
            raise ValueError("MAX_SCHEDULE_POSTS must be between 1 and 10000")

    @property
    def public(self) -> bool:
        return urlsplit(self.origin).scheme == "https"

    @property
    def listen_port(self) -> int:
        if self.bind_port is not None:
            return self.bind_port
        return 8000 if self.public else urlsplit(self.origin).port or 80

    @property
    def callback(self) -> str:
        return self.origin + "/v1/auth/callback"

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        return cls(
            data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))).expanduser().resolve(),
            origin=os.getenv("APP_ORIGIN", "http://127.0.0.1:8000").rstrip("/"),
            encryption_key=os.getenv("APP_ENCRYPTION_KEY", ""),
            client_id=os.getenv("X_CLIENT_ID", ""),
            client_secret=os.getenv("X_CLIENT_SECRET", ""),
            owner_id=os.getenv("X_OWNER_ID", ""),
            bind_host=os.getenv("APP_BIND_HOST", "127.0.0.1"),
            bind_port=int(os.environ["APP_BIND_PORT"]) if os.getenv("APP_BIND_PORT") else None,
            trusted_proxy_ips=tuple(
                value.strip()
                for value in os.getenv("TRUSTED_PROXY_IPS", "").split(",")
                if value.strip()
            ),
            max_connections=int(os.getenv("MAX_CONNECTIONS", "64")),
            session_idle=int(os.getenv("SESSION_IDLE_SECONDS", "1800")),
            session_lifetime=int(os.getenv("SESSION_LIFETIME_SECONDS", "43200")),
            upload_bytes=int(os.getenv("MAX_ARCHIVE_BYTES", "67108864")),
            json_bytes=int(os.getenv("MAX_JSON_BYTES", "262144")),
            body_timeout=int(os.getenv("BODY_TIMEOUT_SECONDS", "30")),
            header_bytes=int(os.getenv("MAX_HEADER_BYTES", "16384")),
            header_count=int(os.getenv("MAX_HEADER_COUNT", "100")),
            fallback_limit=int(os.getenv("X_DELETE_FALLBACK_LIMIT", "50")),
            quota_window=int(os.getenv("X_DELETE_WINDOW_SECONDS", "900")),
            run_max=int(os.getenv("MAX_POSTS_PER_RUN", "50")),
            schedule_max=int(os.getenv("MAX_SCHEDULE_POSTS", "5000")),
            request_timeout=int(os.getenv("X_REQUEST_TIMEOUT_SECONDS", "15")),
            response_bytes=int(os.getenv("MAX_X_RESPONSE_BYTES", "262144")),
            auth_budget=int(os.getenv("AUTH_REQUESTS_PER_10_MIN", "60")),
            read_budget=int(os.getenv("READ_REQUESTS_PER_MINUTE", "600")),
            write_budget=int(os.getenv("WRITE_REQUESTS_PER_MINUTE", "60")),
            upload_budget=int(os.getenv("ARCHIVE_IMPORTS_PER_10_MIN", "3")),
            sync_page_size=int(os.getenv("X_SYNC_PAGE_SIZE", "100")),
            sync_interval=int(os.getenv("X_SYNC_MIN_INTERVAL_SECONDS", "2")),
            sync_retry=int(os.getenv("X_SYNC_RETRY_SECONDS", "60")),
            auto_sync=os.getenv("X_AUTO_SYNC", "false").lower() == "true",
            worker_interval=float(os.getenv("WORKER_INTERVAL_SECONDS", "1")),
        )
