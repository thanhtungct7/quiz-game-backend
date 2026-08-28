from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

MAX_BIO_LENGTH = 300


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    username: str | None
    bio: str | None = None
    # Ready-to-fetch URL, not the raw column: an uploaded avatar lives in Google Drive and is
    # served back through this API, while a Google login brings an absolute googleusercontent
    # link. Built by `build_user_read`, which is why routes must not return the ORM user.
    avatar_url: str | None = None
    # Whether that URL is an avatar this user uploaded, and so has something to delete.
    # A Google picture arrives with the account and cannot be removed here.
    has_uploaded_avatar: bool = False
    is_active: bool
    created_at: datetime


class UserUpdate(BaseModel):
    """PATCH body. Every field is optional and only the ones actually present in the
    request are written -- `bio: null` clears the bio, an absent `bio` leaves it alone."""

    username: str | None = Field(default=None, min_length=1, max_length=50)
    bio: str | None = Field(default=None, max_length=MAX_BIO_LENGTH)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Username must not be blank")
        return normalized

    @field_validator("bio")
    @classmethod
    def normalize_bio(cls, value: str | None) -> str | None:
        # Unlike the username, a blank bio is a legitimate "clear it" rather than an error.
        if value is None:
            return None
        return value.strip() or None
