from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from app.state import state
from app import parser
from app import twitter as tw

router = APIRouter()


class DeleteRequest(BaseModel):
    tweet_ids: list[str]


def _load_or_raise():
    """Load tweets from archive, raising a clean 503 if the file is missing."""
    try:
        return parser.load_tweets()
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse tweets.js: {e}")


@router.get("")
def get_tweets(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
):
    all_tweets = _load_or_raise()
    matched = parser.filter_tweets(
        all_tweets, state.site_patterns, state.keywords, state.deleted_ids, search or ""
    )

    total = len(matched)
    start = (page - 1) * limit
    page_slice = matched[start : start + limit]

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "tweets": [parser.format_tweet(t, sites, kws) for t, sites, kws in page_slice],
    }


@router.get("/stats")
def get_stats():
    all_tweets = _load_or_raise()
    matched = parser.filter_tweets(
        all_tweets, state.site_patterns, state.keywords, state.deleted_ids
    )

    site_counts: dict = {}
    keyword_counts: dict = {}
    for _, sites, kws in matched:
        for site in sites:
            site_counts[site] = site_counts.get(site, 0) + 1
        for kw in kws:
            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

    return {
        "total_in_archive": len(all_tweets),
        "matched": len(matched),
        "deleted_this_session": len(state.deleted_ids),
        "by_site": site_counts,
        "by_keyword": keyword_counts,
    }


@router.post("/delete")
def delete_tweets(body: DeleteRequest):
    if not state.authenticated:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated — connect your credentials first.",
        )
    if not body.tweet_ids:
        raise HTTPException(status_code=400, detail="No tweet IDs provided.")

    results: dict = {"deleted": [], "failed": []}

    for tweet_id in body.tweet_ids:
        try:
            tw.delete_tweet(tweet_id)
            state.deleted_ids.add(tweet_id)
            results["deleted"].append(tweet_id)
        except ValueError as e:
            results["failed"].append({"id": tweet_id, "error": str(e)})
        except Exception as e:
            results["failed"].append({"id": tweet_id, "error": f"Unexpected error: {e}"})

    return results