from fastapi import APIRouter, Response, status

from app.api.dependencies import CurrentUser, NotificationServiceDependency
from app.api.rate_limit import limit_by_user
from app.core import rate_limit_policies as limits
from app.schemas.notification.notification import (
    DeviceRegistrationRequest,
    DeviceUnregistrationRequest,
)

router = APIRouter()


@router.post(
    "/register-device",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[limit_by_user("register-device", limits.DEVICE_REGISTRATION_PER_USER)],
)
async def register_device(
    payload: DeviceRegistrationRequest,
    current_user: CurrentUser,
    service: NotificationServiceDependency,
) -> Response:
    """Store this device's FCM token for the signed-in user. Safe to repeat."""
    await service.register_device(current_user.id, payload.fcm_token, payload.device_type)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/unregister-device",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[limit_by_user("unregister-device", limits.DEVICE_REGISTRATION_PER_USER)],
)
async def unregister_device(
    payload: DeviceUnregistrationRequest,
    current_user: CurrentUser,
    service: NotificationServiceDependency,
) -> Response:
    """Stop pushing to this device. Called on sign-out, before the session is revoked."""
    await service.unregister_device(current_user.id, payload.fcm_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
