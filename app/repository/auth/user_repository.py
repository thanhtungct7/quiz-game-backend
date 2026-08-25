from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EmailAlreadyExistsError
from app.models.auth.user import User


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_user_by_email(self, email: str) -> User | None:
        statement = select(User).where(User.email == email)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: str) -> User | None:
        return await self.db.get(User, user_id)

    async def exists_by_email(self, email: str) -> bool:
        statement = select(User).where(User.email == email)
        return await self.db.scalar(statement) is not None

    async def create_user(self, user: User) -> User:
        self.db.add(user)
        try:
            await self.db.commit()
        except IntegrityError as exc:
            await self.db.rollback()
            raise EmailAlreadyExistsError("Email is already registered") from exc
        await self.db.refresh(user)
        return user

    async def get_user_by_google_subject(self, subject: str) -> User | None:
        statement = select(User).where(User.google_subject == subject)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()
