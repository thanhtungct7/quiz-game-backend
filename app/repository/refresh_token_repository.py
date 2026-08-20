from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken

class RefreshTokenRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_refresh_token_by_hash(self, token_hash: str) -> RefreshToken | None:
        result = await self.db.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == token_hash
            )
        )
        return result.scalar_one_or_none()
    async def create(self, token: RefreshToken) -> RefreshToken:
        self.db.add(token)
        await self.db.commit()
        await self.db.refresh(token)
        return token
    
