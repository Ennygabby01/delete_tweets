from dataclasses import replace

import httpx
import pytest

from app.errors import ProviderError
from app.provider import XClient


def test_provider_bounded_body_and_safe_errors(settings):
    bounded = replace(settings, response_bytes=10)

    def respond(request):
        return httpx.Response(200, content=b"x" * 100)

    provider = XClient(bounded, httpx.MockTransport(respond))
    with pytest.raises(ProviderError, match="oversized") as failure:
        provider.delete("synthetic-token", "12345")
    assert failure.value.uncertain
    assert "synthetic-token" not in failure.value.message
    provider.close()


def test_provider_does_not_follow_redirects_or_trust_environment(settings):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"}, json={})

    provider = XClient(settings, httpx.MockTransport(respond))
    response = provider.delete("synthetic-token", "12345")
    assert response.status == 302 and len(calls) == 1
    assert calls[0].url.host == "api.x.com"
    provider.close()


@pytest.mark.parametrize(
    ("status", "body", "public_status"),
    [
        (400, {"error": "invalid_grant"}, 401),
        (401, {"error": "invalid_client"}, 503),
        (503, {"detail": "SYNTHETIC_PRIVATE"}, 503),
    ],
)
def test_token_rejection_distinguishes_revocation_from_temporary_or_app_failure(
    settings, status, body, public_status
):
    provider = XClient(
        settings, httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    with pytest.raises(ProviderError) as error:
        provider.tokens({"grant_type": "refresh_token", "refresh_token": "SYNTHETIC_PRIVATE"})
    assert error.value.status == public_status
    assert "SYNTHETIC_PRIVATE" not in error.value.message
    provider.close()
