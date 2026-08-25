from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken


class PasswordResetTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def replace_active_token(
            self,
            token: PasswordResetToken,
            revoked_at: datetime,
    ) -> PasswordResetToken:
        statement = (
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == token.user_id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.revoked_at.is_(None),
            )
            .values(revoked_at=revoked_at)
        )

        await self.db.execute(statement)
        self.db.add(token)
        await self.db.commit()
        await self.db.refresh(token)
        return token

    async def get_by_hash_for_update(
            self,
            token_hash: str,
    ) -> PasswordResetToken | None:
        statement = (
            select(PasswordResetToken)
            .where(PasswordResetToken.token_hash == token_hash)
            .with_for_update()
        )

        result = await self.db.execute(statement)
        return result.scalar_one_or_none()
    