import tweepy
from app.state import state

# Human-readable messages for common X API error codes
_API_ERRORS: dict[int, str] = {
    32:  "Authentication failed — check your API credentials.",
    64:  "Your account has been suspended.",
    88:  "Rate limit exceeded — wait 15 minutes and try again.",
    89:  "Access token expired or revoked — regenerate your tokens.",
    135: "Timestamp out of bounds — check your system clock.",
    144: "Tweet not found (may already be deleted).",
    179: "You are not authorized to see this tweet.",
    185: "Daily tweet limit reached.",
    226: "Request flagged as automated spam — try again later.",
    261: "App write permissions disabled — enable Read+Write in the X Developer Portal.",
    326: "Account locked — verify your account on twitter.com first.",
    403: "Permission denied — ensure your app has Read and Write permissions.",
    401: "Unauthorized — credentials may be invalid or expired.",
}


def _friendly_error(exc: tweepy.errors.TweepyException) -> str:
    """Extract a user-friendly message from a TweepyException."""
    code = None
    try:
        if hasattr(exc, "api_codes") and exc.api_codes:
            code = exc.api_codes[0]
        elif hasattr(exc, "response") and exc.response is not None:
            errors = exc.response.json().get("errors", [])
            if errors:
                code = errors[0].get("code")
    except Exception:
        pass
    return _API_ERRORS.get(code, str(exc))


def get_api() -> tweepy.API:
    if not state.authenticated:
        raise ValueError("Not authenticated — connect your credentials first.")
    auth = tweepy.OAuth1UserHandler(
        state.consumer_key,
        state.consumer_secret,
        state.access_token,
        state.access_token_secret,
    )
    return tweepy.API(auth)


def delete_tweet(tweet_id: str) -> bool:
    """Delete a tweet by ID. Returns True on success or if already deleted."""
    api = get_api()
    try:
        api.destroy_status(id=tweet_id)
        return True
    except tweepy.errors.NotFound:
        # Tweet already gone — count as success
        return True
    except tweepy.errors.TweepyException as exc:
        raise ValueError(_friendly_error(exc)) from exc