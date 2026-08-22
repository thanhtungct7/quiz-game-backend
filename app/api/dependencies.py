from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.repository.challenge_option_repository import ChallengeOptionRepository
from app.repository.challenge_repository import ChallengeRepository
from app.repository.course_repository import CourseRepository
from app.repository.lesson_repository import LessonRepository
from app.repository.password_reset_token_repository import PasswordResetTokenRepository
from app.repository.refresh_token_repository import RefreshTokenRepository
from app.repository.unit_repository import UnitRepository
from app.repository.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.course_content_service import CourseContentService
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


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin_user(current_user: CurrentUser) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges are required",
        )
    return current_user


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

def get_course_content_service(db: DatabaseSession) -> CourseContentService:
    return CourseContentService(
        courses=CourseRepository(db),
        units=UnitRepository(db),
        lessons=LessonRepository(db),
        challenges=ChallengeRepository(db),
        challenge_options=ChallengeOptionRepository(db),
    )


AdminUser = Annotated[User, Depends(get_current_admin_user)]
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
PasswordResetServiceDependency = Annotated[
    PasswordResetService, Depends(get_password_reset_service)
]
CourseContentServiceDependency = Annotated[
    CourseContentService, Depends(get_course_content_service)
]
