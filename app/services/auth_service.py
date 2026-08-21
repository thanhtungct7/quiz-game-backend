from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.exceptions import (
    AccountLinkRequiredError,
    EmailAlreadyExistsError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.repository.refresh_token_repository import RefreshTokenRepository
from app.repository.user_repository import UserRepository
from app.schemas.auth import TokenResponse
from app.services.google_auth_service import verify_google_id_token


class AuthService:
    def __init__(
        self,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
    ) -> None:
        self.users = users
        self.refresh_tokens = refresh_tokens

    async def register(self, email: str, password: str, username: str | None = None) -> User:
        existing_user = await self.users.get_user_by_email(email)
        if existing_user is not None:
            raise EmailAlreadyExistsError("Email is already registered")

        user = User(email=email, hashed_password=hash_password(password), username=username)
        return await self.users.create_user(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.users.get_user_by_email(email)
        if (
            user is None
            or user.hashed_password is None
            or not verify_password(password, user.hashed_password)
        ):
            raise InvalidCredentialsError("Invalid email or password")

        if not user.is_active:
            raise InactiveUserError("Invalid email or password")

        return await self._issue_token_pair(user)

    async def refresh(self, raw_refresh_token: str) -> TokenResponse:
        now = datetime.now(UTC)
        stored_token = await self.refresh_tokens.get_refresh_token_by_hash(
            hash_refresh_token(raw_refresh_token),
            for_update=True,
        )
        if (
            stored_token is None
            or stored_token.revoked_at is not None
            or stored_token.expires_at <= now
        ):
            raise InvalidRefreshTokenError("Invalid or expired refresh token")

        user = await self.users.get_by_id(stored_token.user_id)
        if user is None or not user.is_active:
            raise InvalidRefreshTokenError("Invalid or expired refresh token")

        return await self._issue_token_pair(user, rotated_from=stored_token)

    async def logout(self, raw_refresh_token: str) -> None:
        stored_token = await self.refresh_tokens.get_refresh_token_by_hash(
            hash_refresh_token(raw_refresh_token)
        )
        if stored_token is not None and stored_token.revoked_at is None:
            await self.refresh_tokens.revoke(stored_token, datetime.now(UTC))

    async def _issue_token_pair(
        self,
        user: User,
        rotated_from: RefreshToken | None = None,
    ) -> TokenResponse:
        now = datetime.now(UTC)
        access_token = create_access_token(user.id)
        raw_refresh_token = create_refresh_token()
        stored_refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh_token),
            expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        )

        if rotated_from is None:
            await self.refresh_tokens.create(stored_refresh_token)
        else:
            await self.refresh_tokens.rotate(rotated_from, stored_refresh_token, now)

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )

    async def login_with_google(self, raw_id_token: str) -> TokenResponse:
        claims = await verify_google_id_token(raw_id_token)

        google_subject = claims["sub"]
        email = claims["email"].strip().lower()

        user = await self.users.get_user_by_google_subject(google_subject)

        if user is None:
            email_owner = await self.users.get_user_by_email(email)

            if email_owner is not None:
                raise AccountLinkRequiredError(
                    "Email is already registered with a different login method."
                )

            raw_name = claims.get("name")
            username = raw_name.strip()[:50] if isinstance(raw_name, str) else None
            if not username:
                username = None

            raw_picture = claims.get("picture")
            avatar_url = (
                raw_picture
                if isinstance(raw_picture, str)
                and raw_picture.startswith("https://")
                and len(raw_picture) <= 255
                else None
            )

            user = User(
                email=email,
                google_subject=google_subject,
                username=username,
                avatar_url=avatar_url,
                is_active=True,
            )
            try:
                user = await self.users.create_user(user)
            except EmailAlreadyExistsError as exc:
                # A concurrent request may have created the same Google account
                # after the lookups above. Re-read by Google's stable subject.
                user = await self.users.get_user_by_google_subject(google_subject)
                if user is None:
                    raise AccountLinkRequiredError(
                        "Email is already registered with a different login method."
                    ) from exc

        if not user.is_active:
            raise InactiveUserError("Account is inactive")

        return await self._issue_token_pair(user)
