import json
import re
from pathlib import Path
from typing import Optional

TWEETS_FILE = Path(__file__).parent.parent / "tweets.js"

_tweets_cache: Optional[list] = None


def invalidate_cache() -> None:
    global _tweets_cache
    _tweets_cache = None


def load_tweets() -> list:
    """Parse tweets.js and cache the result. Raises clear errors on failure."""
    global _tweets_cache
    if _tweets_cache is not None:
        return _tweets_cache

    if not TWEETS_FILE.exists():
        raise FileNotFoundError(
            f"tweets.js not found. Place your Twitter/X archive file at: {TWEETS_FILE}"
        )

    try:
        with open(TWEETS_FILE, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise OSError(f"Could not read tweets.js: {e}") from e

    # Strip the JS assignment wrapper: window.YTD.tweets.part0 = [...]
    content = re.sub(r"^window\.YTD\.tweets\.\w+\s*=\s*", "", content.strip())

    try:
        raw = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"tweets.js is not valid JSON: {e}") from e

    if not isinstance(raw, list):
        raise ValueError("tweets.js has an unexpected format — expected a JSON array.")

    _tweets_cache = [item["tweet"] for item in raw if "tweet" in item]
    return _tweets_cache


def _tweet_matches(
    tweet: dict,
    site_patterns: list[str],
    keywords: list[str],
) -> tuple[list[str], list[str]]:
    """Return (matched_sites, matched_keywords) for a tweet."""
    text = tweet.get("full_text", "").lower()
    expanded_urls = [
        u.get("expanded_url", "").lower()
        for u in tweet.get("entities", {}).get("urls", [])
    ]

    matched_sites = [
        p for p in site_patterns
        if p.lower() in text or any(p.lower() in u for u in expanded_urls)
    ]
    matched_keywords = [k for k in keywords if k.lower() in text]

    return matched_sites, matched_keywords


def filter_tweets(
    tweets: list,
    site_patterns: list[str],
    keywords: list[str],
    deleted_ids: set,
    search: str = "",
) -> list[tuple]:
    """Return list of (tweet, matched_sites, matched_keywords) tuples."""
    search_lower = search.strip().lower()
    results = []

    for tweet in tweets:
        if tweet.get("id_str", "") in deleted_ids:
            continue

        matched_sites, matched_keywords = _tweet_matches(tweet, site_patterns, keywords)

        # A tweet must match at least one filter to be included
        if not matched_sites and not matched_keywords:
            continue

        # Additional text search on top of the matched set
        if search_lower and search_lower not in tweet.get("full_text", "").lower():
            continue

        results.append((tweet, matched_sites, matched_keywords))

    return results


def format_tweet(tweet: dict, matched_sites: list[str], matched_keywords: list[str]) -> dict:
    source_raw = tweet.get("source", "")
    source_clean = re.sub(r"<[^>]+>", "", source_raw).strip()
    return {
        "id": tweet["id_str"],
        "text": tweet.get("full_text", ""),
        "created_at": tweet.get("created_at", ""),
        "source": source_clean,
        "matched_sites": matched_sites,
        "matched_keywords": matched_keywords,
        "urls": [
            {
                "url": u.get("url", ""),
                "expanded_url": u.get("expanded_url", ""),
                "display_url": u.get("display_url", ""),
            }
            for u in tweet.get("entities", {}).get("urls", [])
        ],
    }