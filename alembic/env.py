import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings
from app.db.base import Base

# Every model must be imported here, not just the ones autogenerate happens to
# need: `target_metadata` is built from whatever has been imported, so a missing
# model reads as a dropped table and autogenerate emits a DROP for it.
from app.models import (  # noqa: F401
    Challenge,
    ChallengeOption,
    Course,
    DuoMatch,
    DuoMatchRound,
    DuoMatchSkillUse,
    DuoRating,
    GameClass,
    GameItem,
    GameSeason,
    GoldTransaction,
    Lesson,
    LootGrant,
    Passage,
    PasswordResetToken,
    RefreshToken,
    SeasonRating,
    Skill,
    Topic,
    Unit,
    User,
    UserChallengeProgress,
    UserDailyActivity,
    UserEquipment,
    UserGameProfile,
    UserItem,
    UserLessonProgress,
    UserSkill,
    UserSkillLoadout,
)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
