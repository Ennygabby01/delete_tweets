"""
Saves/loads API credentials to credentials.json so you don't have to
re-enter them every time the server restarts.

credentials.json is gitignored and never committed.
"""
import json
from pathlib import Path

CREDENTIALS_FILE = Path(__file__).parent.parent / "credentials.json"


def save(consumer_key: str, consumer_secret: str, access_token: str, access_token_secret: str) -> None:
    with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "consumer_key": consumer_key,
                "consumer_secret": consumer_secret,
                "access_token": access_token,
                "access_token_secret": access_token_secret,
            },
            f,
            indent=2,
        )


def load() -> dict | None:
    if not CREDENTIALS_FILE.exists():
        return None
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Validate all required keys are present and non-empty
        keys = ("consumer_key", "consumer_secret", "access_token", "access_token_secret")
        if all(data.get(k) for k in keys):
            return data
    except Exception:
        pass
    return None


def clear() -> None:
    if CREDENTIALS_FILE.exists():
        CREDENTIALS_FILE.unlink()