"""Start the combined server with a local or public canonical origin."""

import argparse
import errno
import os
import socket
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402


def reserve(host: str, preferred: int, *, fallback: bool = True) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind((host, preferred))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE or not fallback:
            listener.close()
            raise
        listener.bind((host, 0))
    listener.listen(128)
    listener.set_inheritable(True)
    return listener


def launch(settings: Settings, *, port: int | None = None, reload: bool = False) -> None:
    preferred = settings.listen_port if port is None else port
    if not 0 <= preferred <= 65535:
        raise ValueError("Port must be between 0 and 65535")
    if settings.public and (reload or preferred == 0):
        raise ValueError("HTTPS hosting requires a fixed backend port and no reload")
    with reserve(settings.bind_host, preferred, fallback=not settings.public) as listener:
        selected = listener.getsockname()[1]
        local_port = "" if selected == 80 else f":{selected}"
        origin = settings.origin if settings.public else f"http://{settings.bind_host}{local_port}"
        os.environ["APP_ORIGIN"] = origin
        os.chdir(ROOT)
        print(f"\nTweet Deleter: {origin}\nX callback: {origin}/v1/auth/callback\n", flush=True)
        print(f"Backend: http://{settings.bind_host}:{selected}", flush=True)
        if preferred and selected != preferred:
            print(
                "Preferred port is busy; using an available port. Register the shown X callback if needed.",
                flush=True,
            )
        # Access logs are disabled because OAuth callback URLs contain temporary codes.
        uvicorn.run(
            "app.main:create_app",
            factory=True,
            fd=listener.fileno(),
            reload=reload,
            reload_dirs=[str(ROOT / "app"), str(ROOT / "static")] if reload else None,
            access_log=False,
            proxy_headers=settings.public,
            forwarded_allow_ips=",".join(settings.trusted_proxy_ips),
            workers=1,
            limit_concurrency=settings.max_connections,
            h11_max_incomplete_event_size=settings.header_bytes,
            server_header=False,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    try:
        launch(Settings.load(), port=args.port, reload=args.reload)
    except ValueError as exc:
        parser.error(str(exc))
    except OSError:
        parser.error(
            "Cannot bind the backend address/port. Check configuration and other processes."
        )


if __name__ == "__main__":
    main()
