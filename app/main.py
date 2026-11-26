"""Application composition. The durable worker only sends explicitly confirmed runs."""

import asyncio
import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.archive import Archive
from app.auth import Auth
from app.config import ROOT, Settings
from app.errors import AppError
from app.post_order import PostOrder
from app.post_reads import PostReads
from app.post_sync import PostSync
from app.provider import XClient
from app.quota import Quota
from app.routes import auth, workspace
from app.runs import Runs
from app.security import HEADERS, SecurityMiddleware
from app.sessions import Sessions
from app.storage import Database

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, provider: XClient | None = None, *, worker: bool = True
) -> FastAPI:
    settings = settings or Settings.load()
    db = Database(settings.data_dir)
    sessions = Sessions(db, settings)
    client = provider or XClient(settings)
    authorization = Auth(db, sessions, settings, client)
    archive = Archive(db)
    quota = Quota(db, settings)
    post_reads = PostReads(db)
    post_order = PostOrder(db)
    runs = Runs(db, archive, authorization, quota, client, settings, post_reads)
    post_sync = PostSync(db, authorization, client, settings, post_reads)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> Any:
        with db.runtime():
            db.initialize()
            with db.connect() as connection:
                owner = connection.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
            if settings.owner_id and owner and owner[0] != settings.owner_id:
                raise ValueError("X_OWNER_ID does not match this data directory's owner")
            await asyncio.to_thread(archive.migrate_legacy, settings.root, settings.upload_bytes)
            runs.recover()
            post_sync.recover()
            stop = asyncio.Event()

            async def work() -> None:
                while not stop.is_set():
                    try:
                        await asyncio.to_thread(runs.tick)
                    except Exception:
                        # Avoid logging SQL arguments, upstream bodies or tokens.
                        logger.error("deletion_worker_stopped_for_review")
                        await asyncio.to_thread(runs.recover)
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=settings.worker_interval)
                    except TimeoutError:
                        continue

            task = asyncio.create_task(work()) if worker else None
            try:
                yield
            finally:
                stop.set()
                if task:
                    await task
                client.close()

    application = FastAPI(
        title="Tweet Deleter",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    for name, value in {
        "db": db,
        "sessions": sessions,
        "settings": settings,
        "provider": client,
        "auth": authorization,
        "archive": archive,
        "quota": quota,
        "runs": runs,
        "post_sync": post_sync,
        "post_reads": post_reads,
        "post_order": post_order,
    }.items():
        setattr(application.state, name, value)
    application.add_middleware(SecurityMiddleware, settings=settings, db=db, sessions=sessions)
    application.include_router(auth.router, prefix="/v1")
    application.include_router(workspace.router, prefix="/v1")
    application.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(ROOT / "static" / "index.html")

    @application.exception_handler(AppError)
    async def app_error(request: Request, error: AppError) -> JSONResponse:
        return JSONResponse({"detail": error.message}, status_code=error.status)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Check the submitted fields and try again."}, status_code=422
        )

    @application.exception_handler(sqlite3.Error)
    async def storage_error(request: Request, error: sqlite3.Error) -> JSONResponse:
        logger.error("local_storage_failure")
        return JSONResponse(
            {"detail": "Local storage is temporarily unavailable."}, status_code=503
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.error("request_failed")
        return JSONResponse(
            {"detail": "The request could not be completed."}, status_code=500, headers=HEADERS
        )

    return application
