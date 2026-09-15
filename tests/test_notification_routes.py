import pytest
from fastapi import status
from pydantic import ValidationError

from app.api.routes.notification.notifications import register_device, unregister_device
from app.models.auth.user import User
from app.models.notification.user_device_token import DeviceType
from app.schemas.notification.notification import (
    DeviceRegistrationRequest,
    DeviceUnregistrationRequest,
)


class FakeNotificationService:
    def __init__(self) -> None:
        self.registered: list[tuple[str, str, DeviceType]] = []
        self.unregistered: list[tuple[str, str]] = []

    async def register_device(
        self, user_id: str, fcm_token: str, device_type: DeviceType
    ) -> None:
        self.registered.append((user_id, fcm_token, device_type))

    async def unregister_device(self, user_id: str, fcm_token: str) -> None:
        self.unregistered.append((user_id, fcm_token))


def _make_user() -> User:
    return User(id="user-1", email="user@example.com")


async def test_registering_a_device_stores_it_for_the_caller() -> None:
    service = FakeNotificationService()

    response = await register_device(
        DeviceRegistrationRequest(fcm_token="token-1"),  # noqa: S106
        _make_user(),
        service,  # type: ignore[arg-type]
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert service.registered == [("user-1", "token-1", DeviceType.ANDROID)]


async def test_unregistering_a_device_removes_the_callers_token() -> None:
    service = FakeNotificationService()

    response = await unregister_device(
        DeviceUnregistrationRequest(fcm_token="token-1"),  # noqa: S106
        _make_user(),
        service,  # type: ignore[arg-type]
    )

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert service.unregistered == [("user-1", "token-1")]


def test_an_empty_token_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DeviceRegistrationRequest(fcm_token="")
