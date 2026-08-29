from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.duo.duo_match import DuoMatch, DuoMatchEndReason, DuoMatchStatus
from app.models.duo.duo_match_round import DuoMatchRound
from app.models.game.duo_match_skill_use import DuoMatchSkillUse


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

    async def claim_for_settlement(self, match_id: str) -> bool:
        """Flip IN_PROGRESS -> FINISHED, and say whether this caller did it.

        Settling a match writes to several tables and commits more than once,
        so a crash or a retry can re-enter it partway through. This is the
        serialization point: exactly one caller sees True, and only that caller
        is allowed to hand out rewards.
        """
        statement = (
            update(DuoMatch)
            .where(DuoMatch.id == match_id, DuoMatch.status == DuoMatchStatus.IN_PROGRESS)
            .values(status=DuoMatchStatus.FINISHED)
            .returning(DuoMatch.id)
        )
        result = await self.db.execute(statement)
        claimed = result.scalar_one_or_none() is not None
        await self.db.commit()
        return claimed

    async def add_rounds(self, rounds: list[DuoMatchRound]) -> None:
        if not rounds:
            return
        self.db.add_all(rounds)
        await self.db.commit()

    async def abandon_orphaned(self) -> list[str]:
        """Close matches left IN_PROGRESS by a previous process.

        Live match state lives in memory, so a restart makes every running
        match unreachable; without this they would sit in history forever.

        Returns the ids of the players who were in them, so their energy can be
        handed back: the match charged them and then never ran.
        """
        statement = (
            update(DuoMatch)
            .where(DuoMatch.status == DuoMatchStatus.IN_PROGRESS)
            .values(
                status=DuoMatchStatus.ABANDONED,
                end_reason=DuoMatchEndReason.CANCELLED,
                finished_at=func.now(),
            )
            .returning(DuoMatch.player_one_id, DuoMatch.player_two_id)
        )
        result = await self.db.execute(statement)
        await self.db.commit()
        return [
            user_id for row in result.all() for user_id in row if user_id is not None
        ]

    async def add_skill_uses(self, uses: list[DuoMatchSkillUse]) -> None:
        if not uses:
            return
        self.db.add_all(uses)
        await self.db.commit()

    async def list_skill_uses(self, match_id: str) -> list[DuoMatchSkillUse]:
        statement = (
            select(DuoMatchSkillUse)
            .where(DuoMatchSkillUse.match_id == match_id)
            .order_by(DuoMatchSkillUse.round_index, DuoMatchSkillUse.created_at)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())
