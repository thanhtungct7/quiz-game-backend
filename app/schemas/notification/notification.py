from pydantic import BaseModel, Field

from app.models.notification.user_device_token import DeviceType

# FCM tokens run to a couple of hundred characters; this only stops abuse.
MAX_FCM_TOKEN_LENGTH = 4096


class DeviceRegistrationRequest(BaseModel):
    fcm_token: str = Field(min_length=1, max_length=MAX_FCM_TOKEN_LENGTH)
    device_type: DeviceType = DeviceType.ANDROID


class DeviceUnregistrationRequest(BaseModel):
    """A body rather than a path parameter, so the token stays out of access logs."""

    fcm_token: str = Field(min_length=1, max_length=MAX_FCM_TOKEN_LENGTH)
