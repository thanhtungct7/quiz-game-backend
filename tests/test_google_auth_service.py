from collections.abc import Callable

import pytest
from google.auth import exceptions as google_auth_exceptions
from google.oauth2 import id_token

from app.core.exceptions import GoogleAuthUnavailableError, InvalidGoogleTokenError
from app.services.auth import google_auth_service as google_auth_service_module
from app.services.auth.google_auth_service import verify_google_id_token


async def run_direct(function: Callable[..., object], *args: object) -> object:
    return function(*args)


@pytest.mark.asyncio
async def test_verify_google_id_token_accepts_required_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_verify(*_: object) -> dict[str, object]:
        return {
            "iss": "https://accounts.google.com",
            "sub": "google-subject-123",
            "email": "player@example.com",
            "email_verified": True,
        }

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(google_auth_service_module, "run_in_threadpool", run_direct)

    claims = await verify_google_id_token("valid-google-id-token")

    assert claims["sub"] == "google-subject-123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_claim",
    [
        {"iss": "https://attacker.example"},
        {"email_verified": False},
        {"sub": ""},
        {"email": "not-an-email"},
    ],
)
async def test_verify_google_id_token_rejects_invalid_claims(
    monkeypatch: pytest.MonkeyPatch,
    changed_claim: dict[str, object],
) -> None:
    def fake_verify(*_: object) -> dict[str, object]:
        claims: dict[str, object] = {
            "iss": "https://accounts.google.com",
            "sub": "google-subject-123",
            "email": "player@example.com",
            "email_verified": True,
        }
        claims.update(changed_claim)
        return claims

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(google_auth_service_module, "run_in_threadpool", run_direct)

    with pytest.raises(InvalidGoogleTokenError):
        await verify_google_id_token("invalid-google-id-token")


@pytest.mark.asyncio
async def test_verify_google_id_token_maps_library_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_verify(*_: object) -> dict[str, object]:
        raise ValueError("bad signature or audience")

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(google_auth_service_module, "run_in_threadpool", run_direct)

    with pytest.raises(InvalidGoogleTokenError):
        await verify_google_id_token("invalid-google-id-token")


@pytest.mark.asyncio
async def test_verify_google_id_token_maps_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_verify(*_: object) -> dict[str, object]:
        raise google_auth_exceptions.TransportError("network unavailable")

    monkeypatch.setattr(id_token, "verify_oauth2_token", fake_verify)
    monkeypatch.setattr(google_auth_service_module, "run_in_threadpool", run_direct)

    with pytest.raises(GoogleAuthUnavailableError):
        await verify_google_id_token("valid-google-id-token")
