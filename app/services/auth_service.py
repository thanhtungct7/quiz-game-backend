from datetime import timedelta, datetime, UTC

from app.core.config import settings

from app.schemas.auth import TokenResponse
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.core.security import create_access_token, create_refresh_token, verify_password, hash_password
from app.core.exceptions import (InvalidCredentialsError, InactiveUserError)
from app.repository.user_repository import UserRepository
from app.repository.refresh_token_repository import RefreshTokenRepository

class AuthService:
    def __init__(
            self,
            users: UserRepository,
            refresh_tokens: RefreshTokenRepository,
    ) -> None:
        self.users = users
        self.refresh_tokens = refresh_tokens


    async def register(
            self, 
            email: str, 
            password: str,
            username: str

        ) -> User:
        existing_user = await self.users.get_user_by_email(email)

        if existing_user is not None:
            raise ValueError("Email is already registered")

        user = User(
            email=email,
            hashed_password=hash_password(password),
            username=username
        )

        return await self.users.create_user(user)

    async def login(self, email: str, password: str) -> TokenResponse:
        user = await self.users.get_user_by_email(email)
        if user is None or not verify_password(password,
                                               user.hashed_password,):
            raise InvalidCredentialsError()

        if not user.is_active:
            raise InactiveUserError()

        access_token = create_access_token(user.id)
        raw_refresh_token = create_refresh_token()

        refresh_token = RefreshToken(
            user_id=user.id,
            token_hash=hash_password(raw_refresh_token),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
        )

        return TokenResponse(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            expires_in=settings.access_token_expire_minutes * 60,
        )
