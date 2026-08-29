from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.gold_transaction import GoldReason, GoldTransaction

UNIQUE_GRANT_CONSTRAINT = "uq_gold_transactions_user_id_reason_ref_id"


class GoldTransactionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def grant(
        self,
        *,
        user_id: str,
        amount: int,
        reason: GoldReason,
        ref_id: str,
        balance_after: int,
    ) -> bool:
        """Write one ledger row, and say whether it was actually new.

        Returns False when a row for this (user, reason, ref) already exists,
        which is the signal that this payout has already happened — settling
        the same match twice must not move the balance twice. Callers must only
        apply the balance change when this returns True.

        Deliberately does not commit: the caller pairs this with the balance
        write so the two land together.
        """
        statement = (
            pg_insert(GoldTransaction)
            .values(
                id=str(uuid4()),
                user_id=user_id,
                amount=amount,
                reason=reason,
                ref_id=ref_id,
                balance_after=balance_after,
            )
            .on_conflict_do_nothing(constraint=UNIQUE_GRANT_CONSTRAINT)
            .returning(GoldTransaction.id)
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none() is not None

    async def list_for_user(self, user_id: str, limit: int = 50) -> list[GoldTransaction]:
        statement = (
            select(GoldTransaction)
            .where(GoldTransaction.user_id == user_id)
            .order_by(GoldTransaction.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def find(
        self, *, user_id: str, reason: GoldReason, ref_id: str
    ) -> GoldTransaction | None:
        statement = select(GoldTransaction).where(
            GoldTransaction.user_id == user_id,
            GoldTransaction.reason == reason,
            GoldTransaction.ref_id == ref_id,
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()
