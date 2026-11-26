"""Thin authenticated transport for local archive, filters and deletion runs."""

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from app.errors import AppError
from app.routes.auth import current

router = APIRouter()


class FilterInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kind: Literal["site", "keyword"]
    value: str = Field(min_length=1, max_length=253)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[Annotated[str, Field(pattern=r"^[0-9]{1,25}$")]] = Field(
        default_factory=list, max_length=5000
    )
    all_matching: bool = False
    search: str = Field(default="", max_length=253)
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{16,64}$")
    post_order: Literal["newest", "oldest"] | None = None

    @model_validator(mode="after")
    def one_selection(self) -> "Selection":
        if self.all_matching and self.ids:
            raise ValueError("Choose a list of IDs or all matching posts, not both")
        return self


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["DELETE"]


def owner(request: Request) -> str:
    value = current(request).user_id
    if value is None:
        raise AppError("Sign in with X to continue.", 401)
    return value


@router.get("/workspace")
def workspace(request: Request) -> dict[str, Any]:
    services = request.app.state
    user_id = owner(request)
    return {
        "stats": services.archive.stats(),
        "filters": services.archive.filters(),
        "quota": services.quota.get(user_id),
        "runs": services.runs.list(user_id),
        "sync": services.post_sync.state(user_id, current(request).digest),
        "preferences": {
            "allow_post_reads": services.post_reads.allowed(user_id),
            "post_order": services.post_order.get(user_id),
        },
        "runner_limits": {
            "max_posts": services.settings.schedule_max,
            "batch_size": services.settings.run_max,
            "fallback_window_seconds": services.settings.quota_window,
        },
    }


@router.get("/posts")
def posts(
    request: Request,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    search: Annotated[str, Query(max_length=253)] = "",
) -> dict[str, Any]:
    user_id = owner(request)
    services = request.app.state
    return services.archive.list(page, limit, search, services.post_order.get(user_id))


@router.post("/archive")
async def upload(request: Request) -> dict[str, int]:
    owner(request)
    if request.headers.get("content-type", "").split(";")[0] != "application/octet-stream":
        raise AppError("Upload tweets.js as a raw archive file.", 415)
    raw = await request.body()
    count = await asyncio.to_thread(request.app.state.archive.import_bytes, raw)
    return {"imported": count}


@router.post("/filters")
def add_filter(request: Request, body: FilterInput) -> dict[str, list[str]]:
    owner(request)
    request.app.state.archive.add_filter(body.kind, body.value)
    return request.app.state.archive.filters()


@router.delete("/filters")
def remove_filter(request: Request, body: FilterInput) -> dict[str, list[str]]:
    owner(request)
    request.app.state.archive.remove_filter(body.kind, body.value)
    return request.app.state.archive.filters()


@router.post("/runs/preview")
def preview(request: Request, body: Selection) -> dict[str, Any]:
    return request.app.state.runs.preview(
        owner(request),
        body.ids,
        body.all_matching,
        body.search,
        body.request_key,
        post_order=body.post_order,
    )


@router.get("/runs")
def history(
    request: Request,
    page: Annotated[int, Query(ge=1, le=100000)] = 1,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    focus: Annotated[str | None, Query(pattern=r"^[a-f0-9]{32}$")] = None,
) -> dict[str, Any]:
    return request.app.state.runs.history(owner(request), page, limit, focus)


class ScheduledSelection(Selection):
    cap: Annotated[int, Field(strict=True, ge=1, le=10000)]
    batch_size: Annotated[int, Field(strict=True, ge=1, le=1000)]


@router.post("/runs/scheduled/preview")
def scheduled_preview(request: Request, body: ScheduledSelection) -> dict[str, Any]:
    return request.app.state.runs.preview(
        owner(request),
        body.ids,
        body.all_matching,
        body.search,
        body.request_key,
        schedule=(body.cap, body.batch_size),
        post_order=body.post_order,
    )


@router.get("/runs/{run_id}")
def run_detail(request: Request, run_id: str) -> dict[str, Any]:
    return request.app.state.runs.detail(owner(request), run_id)


@router.post("/runs/{run_id}/confirm")
def confirm(request: Request, run_id: str, body: Confirmation) -> dict[str, Any]:
    user_id = owner(request)
    request.app.state.runs.confirm(user_id, run_id, body.confirmation)
    return request.app.state.runs.detail(user_id, run_id)


@router.post("/runs/{run_id}/pause")
def pause(request: Request, run_id: str) -> dict[str, Any]:
    user_id = owner(request)
    request.app.state.runs.stop(user_id, run_id)
    return request.app.state.runs.detail(user_id, run_id)


@router.post("/runs/{run_id}/cancel")
def cancel(request: Request, run_id: str) -> dict[str, Any]:
    user_id = owner(request)
    request.app.state.runs.stop(user_id, run_id, cancel=True)
    return request.app.state.runs.detail(user_id, run_id)


@router.post("/runs/{run_id}/reconcile")
def reconcile(request: Request, run_id: str) -> dict[str, Any]:
    return request.app.state.runs.reconcile(owner(request), run_id)


class SyncInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "recent", "older", "check"] = "recent"
    ids: list[Annotated[str, Field(pattern=r"^[0-9]{1,25}$")]] = Field(
        default_factory=list, max_length=100
    )


@router.post("/posts/sync")
def sync_posts(request: Request, body: SyncInput) -> dict[str, Any]:
    return request.app.state.post_sync.sync(
        owner(request), body.mode, body.ids, session_digest=current(request).digest
    )


class ReadPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_post_reads: StrictBool


class OrderPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_order: Literal["newest", "oldest"]


@router.put("/preferences/post-order")
def set_post_order(request: Request, body: OrderPreference) -> dict[str, str]:
    return request.app.state.post_order.save(owner(request), body.post_order)


@router.put("/preferences/post-reads")
def set_post_reads(request: Request, body: ReadPreference) -> dict[str, bool]:
    return request.app.state.post_reads.set_allowed(owner(request), body.allow_post_reads)
