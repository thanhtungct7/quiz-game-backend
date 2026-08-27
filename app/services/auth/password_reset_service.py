from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InvalidPasswordResetTokenError
from app.core.security import (
    create_password_reset_token,
    hash_password,
    hash_password_reset_token,
)
from app.models.auth.password_reset_token import PasswordResetToken
from app.repository.auth.password_reset_token_repository import PasswordResetTokenRepository
from app.repository.auth.refresh_token_repository import RefreshTokenRepository
from app.repository.auth.user_repository import UserRepository
from app.services.auth.email_service import EmailService


class PasswordResetService:
    def __init__(
            self,
            db: AsyncSession,
            users: UserRepository,
            reset_tokens: PasswordResetTokenRepository,
            refresh_tokens: RefreshTokenRepository,
            email_service: EmailService,
    ) -> None:
        self.db = db
        self.users = users
        self.reset_tokens = reset_tokens
        self.refresh_tokens = refresh_tokens
        self.email_service = email_service

    async def request_password_reset(self, email: str) -> None:
        """Email a reset link if the address matches a resettable account.

        Always returns silently for an unknown, inactive, or Google-only
        account — the caller must not be able to tell which case it was,
        or this endpoint becomes an email enumeration oracle.
        """
        user = await self.users.get_user_by_email(email)

        if (
            user is None
            or not user.is_active
            or user.hashed_password is None
        ):
            return
        now = datetime.now(UTC)
        raw_token = create_password_reset_token()
        token_hash = hash_password_reset_token(raw_token)
        reset_token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=now + timedelta(minutes=settings.password_reset_expire_minutes),
        )
        await self.reset_tokens.replace_active_token(reset_token, revoked_at=now)

        await self.email_service.send_password_reset(
            recipient=user.email,
            reset_token=raw_token,
        )

    async def reset_password(self, token: str, new_password: str) -> None:
        """Consume a one-time reset token to set a new password and sign
        the user out everywhere, so a leaked session can't survive a reset
        the account owner triggered because they suspected a compromise."""
        now = datetime.now(UTC)
        token_hash = hash_password_reset_token(token)
        reset_token = await self.reset_tokens.get_by_hash_for_update(token_hash)

        if (
            reset_token is None
            or reset_token.used_at is not None
            or reset_token.revoked_at is not None
            or reset_token.expires_at < now
        ):
            raise InvalidPasswordResetTokenError("Invalid or expired password reset token")

        user = await self.users.get_by_id(reset_token.user_id)
        if (
            user is None
            or not user.is_active
            or user.hashed_password is None
        ):
            raise InvalidPasswordResetTokenError("Invalid password reset token")

        user.hashed_password = hash_password(new_password)
        reset_token.used_at = now

        await self.refresh_tokens.revoke_all_for_user(user.id, revoked_at=now)
        await self.db.commit()

