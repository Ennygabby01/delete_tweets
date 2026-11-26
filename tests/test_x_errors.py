import logging

import httpx
import pytest

from app.x_errors import OLD_ACCESS_MESSAGE


@pytest.mark.parametrize(
    ("status", "body", "reason", "copy"),
    [
        (403, {"detail": "SYNTHETIC_PRIVATE_TOKEN"}, "access_denied", "write access"),
        (401, {"detail": "SYNTHETIC_PRIVATE_TOKEN"}, "authorization", "Sign in again"),
        (402, {"detail": "SYNTHETIC_PRIVATE_TOKEN"}, "billing", "API credits"),
        (
            403,
            {"type": "https://api.x.com/2/problems/client-forbidden"},
            "app_access",
            "does not have access",
        ),
        (403, {"type": "https://api.x.com/2/problems/usage-capped"}, "usage_cap", "usage cap"),
        (
            403,
            {
                "type": "https://api.x.com/2/problems/not-authorized-for-resource",
                "detail": "client IP not allowed: SYNTHETIC_PRIVATE_TOKEN",
            },
            "ip_allowlist",
            "IP allowlist",
        ),
        (
            403,
            {"type": "https://api.x.com/2/problems/not-authorized-for-resource"},
            "resource_access",
            "signed-in account",
        ),
        (
            403,
            {"errors": [{"type": "https://api.x.com/2/problems/client-forbidden"}]},
            "app_access",
            "does not have access",
        ),
    ],
)
def test_denial_is_specific_safe_and_never_automatically_retried(
    ready, fake, caplog, status, body, reason, copy
):
    _, _, services = ready
    fake.replies.append(httpx.Response(status, json=body))
    detail = services.runs.preview("12345", ["1000"], False, "", "denial-test-key-123456")
    services.runs.confirm("12345", detail["id"], "DELETE")
    with caplog.at_level(logging.WARNING):
        services.runs.tick()
    result = services.runs.detail("12345", detail["id"])
    assert result["status"] == "paused"
    assert result["counts"] == {"pending": 1}
    assert copy in result["note"]
    assert "SYNTHETIC_PRIVATE_TOKEN" not in str(result) + caplog.text
    assert f"reason={reason}" in caplog.text
    if reason != "billing":
        assert "credits" not in result["note"].lower()
    services.runs.tick()
    assert len(fake.calls) == 1
    assert fake.calls[0].method == "DELETE"


def test_historical_ambiguous_message_does_not_invent_a_cause(ready):
    _, _, services = ready
    detail = services.runs.preview("12345", ["1000"], False, "", "old-denial-key-123456")
    with services.db.connect(write=True) as db:
        db.execute("UPDATE runs SET note=? WHERE id=?", (OLD_ACCESS_MESSAGE, detail["id"]))
        db.execute(
            "UPDATE run_items SET message=? WHERE run_id=?", (OLD_ACCESS_MESSAGE, detail["id"])
        )
    result = services.runs.detail("12345", detail["id"])
    assert "exact reason was not saved" in result["note"]
    assert "exact reason was not saved" in result["items"][0]["message"]
    with services.db.connect() as db:
        assert (
            db.execute("SELECT note FROM runs WHERE id=?", (detail["id"],)).fetchone()[0]
            == OLD_ACCESS_MESSAGE
        )
