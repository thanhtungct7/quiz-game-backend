from typing import NoReturn

import pytest
from fastapi import BackgroundTasks, HTTPException, status

from app.api.routes.auth.auth import forgot_password, google_login, reset_password
from app.core.exceptions import (
    AccountLinkRequiredError,
    GoogleAuthUnavailableError,
    InactiveUserError,
    InvalidGoogleTokenError,
    InvalidPasswordResetTokenError,
)
from app.schemas.auth.auth import (
    ForgotPasswordRequest,
    GoogleLoginRequest,
    ResetPasswordRequest,
)


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


class RecordingPasswordResetService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.requested: list[tuple[str, object]] = []

    async def request_password_reset(
        self, email: str, background_tasks: object | None = None
    ) -> None:
        self.requested.append((email, background_tasks))

    async def reset_password(self, token: str, new_password: str) -> None:
        del token, new_password
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_forgot_password_hands_the_send_to_background_tasks() -> None:
    """The SMTP roundtrip must not run inside the request: it is slow, and a
    failure would only ever surface for addresses that do have an account."""
    service = RecordingPasswordResetService()
    background_tasks = BackgroundTasks()

    response = await forgot_password(
        ForgotPasswordRequest(email="Player@Example.com"),
        background_tasks,
        service,  # type: ignore[arg-type]
    )

    assert service.requested == [("player@example.com", background_tasks)]
    assert "if the email exists" in response["message"].lower()


@pytest.mark.asyncio
async def test_reset_password_returns_no_content_on_success() -> None:
    response = await reset_password(
        ResetPasswordRequest(token="t" * 32, new_password="brand-new-password"),  # noqa: S106
        RecordingPasswordResetService(),  # type: ignore[arg-type]
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT


@pytest.mark.asyncio
async def test_reset_password_maps_an_invalid_token_to_400() -> None:
    service = RecordingPasswordResetService(
        InvalidPasswordResetTokenError("Invalid or expired password reset token")
    )

    with pytest.raises(HTTPException) as raised:
        await reset_password(
            ResetPasswordRequest(token="t" * 32, new_password="brand-new-password"),  # noqa: S106
            service,  # type: ignore[arg-type]
        )

    assert raised.value.status_code == status.HTTP_400_BAD_REQUEST
