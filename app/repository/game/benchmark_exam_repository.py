from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.benchmark_exam_attempt import BenchmarkAttemptStatus, BenchmarkExamAttempt


class BenchmarkExamRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, attempt: BenchmarkExamAttempt) -> BenchmarkExamAttempt:
        self.db.add(attempt)
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise
        await self.db.refresh(attempt)
        return attempt

    async def get_for_update(self, user_id: str, attempt_id: str) -> BenchmarkExamAttempt | None:
        """One of this player's sittings, row-locked until the next commit.

        Two answers to the same sitting arriving together would otherwise both
        read the same `answers` and the later write would drop the earlier one.
        """
        statement = (
            select(BenchmarkExamAttempt)
            .where(
                BenchmarkExamAttempt.id == attempt_id,
                BenchmarkExamAttempt.user_id == user_id,
            )
            .with_for_update()
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_in_progress(self, user_id: str) -> BenchmarkExamAttempt | None:
        statement = select(BenchmarkExamAttempt).where(
            BenchmarkExamAttempt.user_id == user_id,
            BenchmarkExamAttempt.status == BenchmarkAttemptStatus.IN_PROGRESS,
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def save(
        self, attempt: BenchmarkExamAttempt, data: dict[str, object]
    ) -> BenchmarkExamAttempt:
        for field, value in data.items():
            setattr(attempt, field, value)
        await self.db.commit()
        await self.db.refresh(attempt)
        return attempt

    async def list_for_user(self, user_id: str, limit: int) -> list[BenchmarkExamAttempt]:
        statement = (
            select(BenchmarkExamAttempt)
            .where(BenchmarkExamAttempt.user_id == user_id)
            .order_by(BenchmarkExamAttempt.started_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())
