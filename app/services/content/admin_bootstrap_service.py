import logging

from app.core.config import settings
from app.core.exceptions import EmailAlreadyExistsError
from app.core.security import hash_password
from app.models.auth.user import User
from app.repository.auth.user_repository import UserRepository

logger = logging.getLogger(__name__)


class AdminSeedService:
    """Creates a brand-new, pre-made admin user.

    This never promotes or modifies an existing account: if the email is
    already registered, ``create_admin`` fails (EmailAlreadyExistsError) and
    nothing in the database changes. Granting admin rights is only possible
    by seeding a fresh account this way.
    """

    def __init__(self, users: UserRepository) -> None:
        self.users = users

    async def create_admin(self, email: str, *, username: str | None, password: str) -> User:
        user = User(
            email=email.strip().lower(),
            username=username,
            hashed_password=hash_password(password),
            is_active=True,
            is_admin=True,
        )
        return await self.users.create_user(user)


async def seed_first_admin(service: AdminSeedService) -> None:
    """Create the configured FIRST_ADMIN_EMAIL user on application startup.

    No-op if FIRST_ADMIN_EMAIL/FIRST_ADMIN_PASSWORD are not configured, and
    no-op if that email already exists (skipped, never promoted or modified).
    """
    if settings.first_admin_email is None or settings.first_admin_password is None:
        return

    email = str(settings.first_admin_email).strip().lower()
    try:
        await service.create_admin(
            email,
            username=settings.first_admin_username,
            password=settings.first_admin_password.get_secret_value(),
        )
    except EmailAlreadyExistsError:
        logger.info("Initial admin '%s' already exists, skipping seed", email)
        return

    logger.info("Seeded initial admin user '%s'", email)
