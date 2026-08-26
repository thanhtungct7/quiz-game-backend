from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.duo.duo_match import DuoMatch, DuoMatchEndReason, DuoMatchStatus
from app.models.duo.duo_match_round import DuoMatchRound


class DuoMatchRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, match: DuoMatch) -> DuoMatch:
        self.db.add(match)
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def get_by_id(self, match_id: str) -> DuoMatch | None:
        statement = select(DuoMatch).where(DuoMatch.id == match_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_with_rounds(self, match_id: str) -> DuoMatch | None:
        statement = (
            select(DuoMatch)
            .where(DuoMatch.id == match_id)
            .options(selectinload(DuoMatch.rounds))
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[DuoMatch]:
        statement = (
            select(DuoMatch)
            .where(
                or_(DuoMatch.player_one_id == user_id, DuoMatch.player_two_id == user_id),
                DuoMatch.status.in_(
                    [DuoMatchStatus.FINISHED, DuoMatchStatus.ABANDONED]
                ),
            )
            .order_by(DuoMatch.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def finish(self, match: DuoMatch, data: dict[str, object]) -> DuoMatch:
        """Apply the final result and its rounds in one transaction."""
        for field, value in data.items():
            setattr(match, field, value)
        await self.db.commit()
        await self.db.refresh(match)
        return match

    async def add_rounds(self, rounds: list[DuoMatchRound]) -> None:
        if not rounds:
            return
        self.db.add_all(rounds)
        await self.db.commit()

    async def abandon_orphaned(self) -> int:
        """Close matches left IN_PROGRESS by a previous process.

        Live match state lives in memory, so a restart makes every running
        match unreachable; without this they would sit in history forever.
        """
        counted = await self.db.execute(
            select(func.count())
            .select_from(DuoMatch)
            .where(DuoMatch.status == DuoMatchStatus.IN_PROGRESS)
        )
        orphaned = int(counted.scalar_one())
        if orphaned == 0:
            return 0

        await self.db.execute(
            update(DuoMatch)
            .where(DuoMatch.status == DuoMatchStatus.IN_PROGRESS)
            .values(
                status=DuoMatchStatus.ABANDONED,
                end_reason=DuoMatchEndReason.CANCELLED,
            )
        )
        await self.db.commit()
        return orphaned
