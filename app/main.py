from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from app.routes import admin, tweets
from app import credentials as creds_store
from app import filters as filters_store
from app.state import state


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-load saved credentials on startup so users don't have to re-enter them
    saved = creds_store.load()
    if saved:
        state.consumer_key = saved["consumer_key"]
        state.consumer_secret = saved["consumer_secret"]
        state.access_token = saved["access_token"]
        state.access_token_secret = saved["access_token_secret"]
        state.authenticated = True

    # Auto-load saved filters (sites + keywords) on startup
    saved_filters = filters_store.load()
    if saved_filters:
        state.site_patterns = saved_filters["sites"]
        state.keywords = saved_filters["keywords"]

    yield


app = FastAPI(title="Tweet Deleter", lifespan=lifespan)

app.include_router(admin.router, prefix="/v1/admin", tags=["admin"])
app.include_router(tweets.router, prefix="/v1/tweets", tags=["tweets"])

static_dir = Path(__file__).parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(str(static_dir / "index.html"))