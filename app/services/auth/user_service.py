import logging

from app.core.config import Settings
from app.core.exceptions import AvatarNotFoundError
from app.core.security import hash_password
from app.models.auth.user import User
from app.repository.auth.user_repository import UserRepository
from app.schemas.auth.user import UserRead, UserUpdate
from app.services.auth.avatar_storage import AvatarStorage
from app.services.auth.avatar_url import resolve_avatar_url

logger = logging.getLogger(__name__)


def build_user_read(user: User, config: Settings) -> UserRead:
    """Assemble the wire shape. `avatar_url` is resolved rather than copied, which is why
    routes must return this instead of the ORM user."""
    return UserRead(
        id=user.id,
        email=user.email,
        username=user.username,
        bio=user.bio,
        avatar_url=resolve_avatar_url(user, config),
        has_uploaded_avatar=user.avatar_file_id is not None,
        is_active=user.is_active,
        created_at=user.created_at,
    )


class UserService:
    def __init__(
        self,
        user_repository: UserRepository,
        avatar_storage: AvatarStorage | None = None,
        config: Settings | None = None,
    ) -> None:
        self.user_repository = user_repository
        self.avatar_storage = avatar_storage
        self.config = config

    async def create_user(
        self,
        email: str,
        password: str,
        username: str | None = None,
    ) -> User:
        user = User(email=email, hashed_password=hash_password(password), username=username)
        return await self.user_repository.create_user(user)

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        # exclude_unset, not exclude_none: `{"bio": null}` clears the bio, while a body that
        # omits `bio` entirely must leave whatever is stored untouched.
        fields = data.model_dump(exclude_unset=True)
        if not fields:
            return user
        return await self.user_repository.update(user, **fields)

    async def replace_avatar(self, user: User, content: bytes, content_type: str) -> User:
        storage = self._storage()
        previous_file_id = user.avatar_file_id
        file_id = await storage.upload(content, content_type, user.id)
        updated = await self.user_repository.update(user, avatar_file_id=file_id)
        await self._delete_quietly(previous_file_id)
        return updated

    async def remove_avatar(self, user: User) -> User:
        file_id = user.avatar_file_id
        if file_id is None:
            raise AvatarNotFoundError("This account has no uploaded avatar")
        updated = await self.user_repository.update(user, avatar_file_id=None)
        await self._delete_quietly(file_id)
        return updated

    async def read_avatar(self, user_id: str) -> tuple[bytes, str]:
        user = await self.user_repository.get_by_id(user_id)
        if user is None or user.avatar_file_id is None:
            raise AvatarNotFoundError("This account has no uploaded avatar")
        return await self._storage().download(user.avatar_file_id)

    def _storage(self) -> AvatarStorage:
        if self.avatar_storage is None:
            raise AvatarNotFoundError("Avatar storage is not configured")
        return self.avatar_storage

    async def _delete_quietly(self, file_id: str | None) -> None:
        """The new avatar is already the user's own; failing to reap the old blob is a
        leak in someone's Drive, not a reason to fail the request."""
        if file_id is None:
            return
        try:
            await self._storage().delete(file_id)
        except Exception:
            logger.warning("Could not delete replaced avatar file_id=%s", file_id, exc_info=True)
