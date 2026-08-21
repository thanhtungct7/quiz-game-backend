from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repository.refresh_token_repository import RefreshTokenRepository
from app.repository.user_repository import UserRepository
from app.services.auth_service import AuthService

from app.core.config import settings
from app.repository.password_reset_token_repository import PasswordResetTokenRepository
from app.services.email_service import SmtpEmailService
from app.services.password_reset_service import PasswordResetService

bearer_scheme = HTTPBearer(auto_error=False)
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise unauthorized
    return user


def get_auth_service(db: DatabaseSession) -> AuthService:
    return AuthService(
        users=UserRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
    )


def get_password_reset_service(db: DatabaseSession) -> PasswordResetService:
    return PasswordResetService(
        db=db,
        users=UserRepository(db),
        reset_tokens=PasswordResetTokenRepository(db),
        refresh_tokens=RefreshTokenRepository(db),
        email_service=SmtpEmailService(settings),
    )

CurrentUser = Annotated[User, Depends(get_current_user)]
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
PasswordResetServiceDependency = Annotated[PasswordResetService, Depends(get_password_reset_service)]
