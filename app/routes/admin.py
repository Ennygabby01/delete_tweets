import json
import re

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel, field_validator
from app.state import state
from app import credentials as creds_store
from app import filters as filters_store
from app import parser

router = APIRouter()


class Credentials(BaseModel):
    consumer_key: str
    consumer_secret: str
    access_token: str
    access_token_secret: str

    @field_validator("consumer_key", "consumer_secret", "access_token", "access_token_secret")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()


class FilterItem(BaseModel):
    pattern: str

    @field_validator("pattern")
    @classmethod
    def not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Pattern must not be empty")
        return v


# ── Credentials ───────────────────────────────────────────────────────────────

@router.post("/login")
def login(creds: Credentials):
    state.consumer_key = creds.consumer_key
    state.consumer_secret = creds.consumer_secret
    state.access_token = creds.access_token
    state.access_token_secret = creds.access_token_secret
    state.authenticated = True
    creds_store.save(
        creds.consumer_key, creds.consumer_secret,
        creds.access_token, creds.access_token_secret,
    )
    return {"status": "ok", "message": "Credentials saved"}


@router.post("/logout")
def logout():
    state.consumer_key = None
    state.consumer_secret = None
    state.access_token = None
    state.access_token_secret = None
    state.authenticated = False
    creds_store.clear()
    return {"status": "ok"}


@router.get("/status")
def status():
    return {"authenticated": state.authenticated}


# ── Site patterns ─────────────────────────────────────────────────────────────

@router.get("/sites")
def get_sites():
    return {"sites": state.site_patterns}


@router.post("/sites")
def add_site(body: FilterItem):
    if body.pattern not in state.site_patterns:
        state.site_patterns.append(body.pattern)
        filters_store.save(state.site_patterns, state.keywords)
    return {"sites": state.site_patterns}


@router.delete("/sites/{pattern:path}")
def remove_site(pattern: str):
    pattern = pattern.strip()
    if pattern in state.site_patterns:
        state.site_patterns.remove(pattern)
        filters_store.save(state.site_patterns, state.keywords)
    return {"sites": state.site_patterns}


# ── Keywords ──────────────────────────────────────────────────────────────────

@router.get("/keywords")
def get_keywords():
    return {"keywords": state.keywords}


@router.post("/keywords")
def add_keyword(body: FilterItem):
    if body.pattern not in state.keywords:
        state.keywords.append(body.pattern)
        filters_store.save(state.site_patterns, state.keywords)
    return {"keywords": state.keywords}


@router.delete("/keywords/{keyword:path}")
def remove_keyword(keyword: str):
    keyword = keyword.strip()
    if keyword in state.keywords:
        state.keywords.remove(keyword)
        filters_store.save(state.site_patterns, state.keywords)
    return {"keywords": state.keywords}


# ── Archive upload ─────────────────────────────────────────────────────────────

@router.post("/upload-tweets")
async def upload_tweets(file: UploadFile = File(...)):
    if not file.filename or not file.filename.endswith(".js"):
        raise HTTPException(status_code=400, detail="Please upload a .js file (tweets.js from your archive).")

    raw_bytes = await file.read()
    try:
        content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded.")

    # Strip the JS assignment wrapper before validating
    stripped = re.sub(r"^window\.YTD\.tweets\.\w+\s*=\s*", "", content.strip())
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid tweets.js — JSON parse error: {e}")

    if not isinstance(data, list):
        raise HTTPException(status_code=400, detail="tweets.js has unexpected format — expected a JSON array.")

    parser.TWEETS_FILE.write_bytes(raw_bytes)
    parser.invalidate_cache()

    tweet_count = sum(1 for item in data if "tweet" in item)
    return {"status": "ok", "tweet_count": tweet_count}