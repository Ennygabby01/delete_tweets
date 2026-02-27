from typing import Optional


class AppState:
    def __init__(self):
        self.consumer_key: Optional[str] = None
        self.consumer_secret: Optional[str] = None
        self.access_token: Optional[str] = None
        self.access_token_secret: Optional[str] = None
        self.authenticated: bool = False
        self.site_patterns: list = []  # Match tweets by domain in URLs
        self.keywords: list = []       # Match tweets by keyword in text
        # Track tweet IDs deleted this session so they vanish from the list
        # without needing to re-read tweets.js (which doesn't change on deletion)
        self.deleted_ids: set = set()


state = AppState()