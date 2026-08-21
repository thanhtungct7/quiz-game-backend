from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.auth import google_login
from app.core.exceptions import (
    AccountLinkRequiredError,
    GoogleAuthUnavailableError,
    InactiveUserError,
    InvalidGoogleTokenError,
)
from app.schemas.auth import GoogleLoginRequest


class FailingGoogleAuthService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def login_with_google(self, _: str) -> NoReturn:
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (InvalidGoogleTokenError(), status.HTTP_401_UNAUTHORIZED),
        (InactiveUserError(), status.HTTP_401_UNAUTHORIZED),
        (AccountLinkRequiredError(), status.HTTP_409_CONFLICT),
        (GoogleAuthUnavailableError(), status.HTTP_503_SERVICE_UNAVAILABLE),
    ],
)
async def test_google_login_maps_domain_errors_to_http_statuses(
    error: Exception,
    expected_status: int,
) -> None:
    payload = GoogleLoginRequest(id_token="x" * 32)
    service = FailingGoogleAuthService(error)

    with pytest.raises(HTTPException) as raised:
        await google_login(payload, service)  # type: ignore[arg-type]

    assert raised.value.status_code == expected_status
