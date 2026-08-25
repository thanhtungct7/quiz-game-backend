from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


class RefreshTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_refresh_token_by_hash(
        self,
        token_hash: str,
        *,
        for_update: bool = False,
    ) -> RefreshToken | None:
        statement = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            statement = statement.with_for_update()
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def create(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.commit()
        await self.db.refresh(token)
        return token

    async def rotate(
        self,
        current_token: RefreshToken,
        replacement_token: RefreshToken,
        revoked_at: datetime,
    ) -> RefreshToken:
        current_token.revoked_at = revoked_at
        self.db.add(replacement_token)
        await self.db.commit()
        await self.db.refresh(replacement_token)
        return replacement_token

    async def revoke(self, token: RefreshToken, revoked_at: datetime) -> None:
        token.revoked_at = revoked_at
        await self.db.commit()

    async def revoke_all_for_user(
            self,
            user_id: str,
            revoked_at: datetime,
    ) -> None:
        statement = (
            update(RefreshToken)
            .where(
                RefreshToken.user_id == user_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )
        await self.db.execute(statement)