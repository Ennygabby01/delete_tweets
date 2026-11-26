"""Browser session and X authorization endpoints."""

import asyncio
from typing import Annotated, Any, cast

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Settings
from app.errors import AppError
from app.sessions import COOKIE, Session

router = APIRouter()


def current(request: Request) -> Session:
    session = request.state.session
    if not session:
        raise AppError("Refresh the page to start a new session.", 401)
    return cast(Session, session)


def set_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.session_lifetime,
        secure=settings.public,
        httponly=True,
        samesite="lax",
        path="/",
    )


@router.get("/session")
def session(request: Request, response: Response) -> dict[str, Any]:
    services = request.app.state
    value = request.state.session
    if not value:
        token, value = services.sessions.create()
        set_cookie(response, token, services.settings)
    profile = services.auth.profile(value.user_id) if value.user_id else None
    return {
        "authenticated": profile is not None,
        "profile": profile,
        "csrf": value.csrf,
        "configured": bool(services.settings.client_id),
        "callback_url": services.settings.callback,
        "max_archive_bytes": services.settings.upload_bytes,
        "session_idle_seconds": services.settings.session_idle,
    }


@router.post("/session/activity")
def activity() -> dict[str, bool]:
    return {"ok": True}


@router.post("/auth/start")
def start(request: Request) -> dict[str, str]:
    return {"url": request.app.state.auth.start(current(request))}


@router.get("/auth/callback")
def callback(
    request: Request,
    state: Annotated[str, Query(min_length=20, max_length=256)],
    code: Annotated[str | None, Query(max_length=8192)] = None,
    error: Annotated[str | None, Query(max_length=128)] = None,
) -> Response:
    services = request.app.state
    try:
        old = current(request)
        user_id = services.auth.callback(old, state, code, error is not None)
        token, _ = services.sessions.create(user_id, previous=old.digest)
    except AppError:
        # The callback query contains authorization material. Never echo or log it.
        return RedirectResponse("/?auth=failed", status_code=303)
    response = RedirectResponse("/?auth=connected", status_code=303)
    set_cookie(response, token, services.settings)
    return response


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    services, value = request.app.state, current(request)
    assert value.user_id is not None
    message = await asyncio.to_thread(services.auth.logout, value.user_id)
    response.delete_cookie(
        COOKIE, path="/", secure=services.settings.public, httponly=True, samesite="lax"
    )
    return {"message": message}
