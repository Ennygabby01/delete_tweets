"""Allowlisted provider failures; upstream details never enter product messages."""

from dataclasses import dataclass
from typing import Any

from app.provider import Reply

OLD_ACCESS_MESSAGE = "X declined access. Check permissions or API credits, then reconnect."


def public_run_note(value: str) -> str:
    if value == OLD_ACCESS_MESSAGE:
        return "X denied this deletion. Its exact reason was not saved. Check this app's write access before continuing."
    return value


@dataclass(frozen=True)
class DeletionDenial:
    reason: str
    message: str


def deletion_denial(reply: Reply) -> DeletionDenial:
    if reply.status == 401:
        return DeletionDenial(
            "authorization", "X authorization is no longer valid. Sign in again before continuing."
        )
    if reply.status == 402:
        return DeletionDenial(
            "billing",
            "X requires billing access for this request. Check this Developer app's API credits before continuing.",
        )
    records: list[dict[str, Any]] = [reply.data]
    errors = reply.data.get("errors")
    if isinstance(errors, list):
        records += [item for item in errors[:100] if isinstance(item, dict)]
    for record in records:
        kind = record.get("type")
        if kind == "https://api.x.com/2/problems/client-forbidden":
            return DeletionDenial(
                "app_access",
                "This X Developer app does not have access to deletion. Check its API access in X Developer Console.",
            )
        if kind == "https://api.x.com/2/problems/usage-capped":
            return DeletionDenial(
                "usage_cap",
                "X reports that this Developer app has reached its usage cap. Check its usage limits before continuing.",
            )
        if kind == "https://api.x.com/2/problems/not-authorized-for-resource":
            detail = record.get("detail")
            if isinstance(detail, str) and detail.lower().startswith("client ip not allowed"):
                return DeletionDenial(
                    "ip_allowlist",
                    "X blocked this connection through its IP allowlist. Check the allowed addresses in X Developer Console.",
                )
            return DeletionDenial(
                "resource_access",
                "X did not authorize deletion of this post. Check the signed-in account and the app's write permissions.",
            )
    return DeletionDenial(
        "access_denied",
        "X denied deletion for this app or account. Check write access in X Developer Console.",
    )
