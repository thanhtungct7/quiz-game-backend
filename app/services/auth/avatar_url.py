from app.core.config import Settings
from app.models.auth.user import User


def resolve_avatar_url(user: User, config: Settings) -> str | None:
    """The URL a client should load for this user's avatar, or None if they have none.

    Two sources feed it. An uploaded avatar lives in Google Drive and is served back through
    this API, because Drive's own link formats are undocumented and throttled; the `?v=`
    cache buster is derived from the file id, so a new upload is always a new URL and can
    never be masked by a cached copy of the old one. Failing that, a Google sign-in leaves an
    absolute googleusercontent link on the user, which is already loadable as-is.
    """
    if user.avatar_file_id:
        return f"{config.api_v1_prefix}/users/{user.id}/avatar?v={user.avatar_file_id[:8]}"
    return user.avatar_url
