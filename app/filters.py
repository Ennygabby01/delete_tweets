"""
Saves/loads target sites and keywords to filters.json so they persist
across server restarts.

filters.json is gitignored and never committed.
"""
import json
from pathlib import Path

FILTERS_FILE = Path(__file__).parent.parent / "filters.json"


def save(sites: list, keywords: list) -> None:
    with open(FILTERS_FILE, "w", encoding="utf-8") as f:
        json.dump({"sites": sites, "keywords": keywords}, f, indent=2)


def load() -> dict | None:
    if not FILTERS_FILE.exists():
        return None
    try:
        with open(FILTERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data.get("sites"), list) and isinstance(data.get("keywords"), list):
            return data
    except Exception:
        pass
    return None


def clear() -> None:
    if FILTERS_FILE.exists():
        FILTERS_FILE.unlink()