from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Query, WebSocketException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import AsyncSessionFactory, get_db
from app.models.auth.user import User
from app.repository.auth.password_reset_token_repository import PasswordResetTokenRepository
from app.repository.auth.refresh_token_repository import RefreshTokenRepository
from app.repository.auth.user_repository import UserRepository
from app.repository.content.challenge_option_repository import ChallengeOptionRepository
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.course_repository import CourseRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.topic_repository import TopicRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.repository.game.activity_repository import ActivityRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.monster_repository import MonsterRepository
from app.repository.game.season_repository import SeasonRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.repository.progress.user_progress_repository import UserProgressRepository
from app.repository.pve.lesson_battle_repository import LessonBattleRepository
from app.services.auth.auth_service import AuthService
from app.services.auth.avatar_storage import (
    AvatarStorage,
    GoogleDriveAvatarStorage,
    InMemoryAvatarStorage,
)
from app.services.auth.email_service import SmtpEmailService
from app.services.auth.password_reset_service import PasswordResetService
from app.services.auth.user_service import UserService
from app.services.content.course_content_service import CourseContentService
from app.services.content.quiz_service import QuizService
from app.services.duo.duo_service import DuoService
from app.services.game.game_service import GameService
from app.services.game.lesson_rewards import LessonRewardService
from app.services.progress.progress_service import ProgressService
from app.services.pve.battle_service import BattleService

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


async def get_current_user_ws(
    token: Annotated[str | None, Query()] = None,
) -> User:
    """Authenticate a WebSocket handshake from a `?token=` query parameter.

    `HTTPBearer` is unusable here because a WebSocket handshake cannot carry an
    Authorization header from most clients. The session is opened and closed
    inside this function rather than injected: a duo socket stays open for the
    length of a match and must not hold a transaction for that long.
    """
    unauthorized = WebSocketException(
        code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired credentials"
    )
    if not token:
        raise unauthorized

    try:
        user_id = decode_access_token(token)
    except jwt.InvalidTokenError as exc:
        raise unauthorized from exc

    async with AsyncSessionFactory() as db:
        user = await db.scalar(select(User).where(User.id == user_id))

    if user is None or not user.is_active:
        raise unauthorized
    return user


CurrentWebSocketUser = Annotated[User, Depends(get_current_user_ws)]


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


_avatar_storage: AvatarStorage | None = None


def get_avatar_storage() -> AvatarStorage:
    """One process-wide instance: it caches the Drive access token and holds the HTTP
    connection pool, both of which would be thrown away by a per-request instance.

    Falls back to in-memory storage when no Drive credentials are configured, so a
    developer can exercise the profile screens without a Google project. `Settings`
    refuses that fallback outside development.
    """
    global _avatar_storage
    if _avatar_storage is None:
        _avatar_storage = (
            GoogleDriveAvatarStorage(settings)
            if settings.is_avatar_storage_configured
            else InMemoryAvatarStorage()
        )
    return _avatar_storage


async def close_avatar_storage() -> None:
    global _avatar_storage
    if isinstance(_avatar_storage, GoogleDriveAvatarStorage):
        await _avatar_storage.aclose()
    _avatar_storage = None


def get_user_service(db: DatabaseSession) -> UserService:
    return UserService(
        user_repository=UserRepository(db),
        avatar_storage=get_avatar_storage(),
        config=settings,
    )


def get_course_content_service(db: DatabaseSession) -> CourseContentService:
    return CourseContentService(
        courses=CourseRepository(db),
        units=UnitRepository(db),
        lessons=LessonRepository(db),
        challenges=ChallengeRepository(db),
        challenge_options=ChallengeOptionRepository(db),
        topics=TopicRepository(db),
    )


def get_quiz_service(db: DatabaseSession) -> QuizService:
    return QuizService(
        challenges=ChallengeRepository(db),
        lessons=LessonRepository(db),
        units=UnitRepository(db),
    )


def get_progress_service(db: DatabaseSession) -> ProgressService:
    return ProgressService(
        progress=UserProgressRepository(db),
        challenges=ChallengeRepository(db),
        lessons=LessonRepository(db),
        units=UnitRepository(db),
        courses=CourseRepository(db),
        rewards=LessonRewardService(
            profiles=GameProfileRepository(db), activity=ActivityRepository(db)
        ),
    )


def get_game_service(db: DatabaseSession) -> GameService:
    return GameService(
        profiles=GameProfileRepository(db),
        catalog=CatalogRepository(db),
        skills=UserSkillRepository(db),
        ledger=GoldTransactionRepository(db),
        progress=UserProgressRepository(db),
        items=ItemRepository(db),
        seasons=SeasonRepository(db),
        ratings=DuoRatingRepository(db),
    )


def get_duo_service(db: DatabaseSession) -> DuoService:
    return DuoService(
        matches=DuoMatchRepository(db),
        ratings=DuoRatingRepository(db),
        profiles=GameProfileRepository(db),
        seasons=SeasonRepository(db),
    )


def get_battle_service(db: DatabaseSession) -> BattleService:
    return BattleService(
        monsters=MonsterRepository(db),
        lessons=LessonRepository(db),
        units=UnitRepository(db),
        courses=CourseRepository(db),
        battles=LessonBattleRepository(db),
    )


AdminUser = Annotated[User, Depends(get_current_admin_user)]
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
PasswordResetServiceDependency = Annotated[
    PasswordResetService, Depends(get_password_reset_service)
]
CourseContentServiceDependency = Annotated[
    CourseContentService, Depends(get_course_content_service)
]
QuizServiceDependency = Annotated[QuizService, Depends(get_quiz_service)]
ProgressServiceDependency = Annotated[ProgressService, Depends(get_progress_service)]
DuoServiceDependency = Annotated[DuoService, Depends(get_duo_service)]
GameServiceDependency = Annotated[GameService, Depends(get_game_service)]
UserServiceDependency = Annotated[UserService, Depends(get_user_service)]
BattleServiceDependency = Annotated[BattleService, Depends(get_battle_service)]
