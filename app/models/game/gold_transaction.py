from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GoldReason(StrEnum):
    MATCH_WIN = "MATCH_WIN"
    MATCH_LOSS = "MATCH_LOSS"
    MATCH_DRAW = "MATCH_DRAW"
    SKILL_UNLOCK = "SKILL_UNLOCK"
    ITEM_PURCHASE = "ITEM_PURCHASE"
    CLASS_CHANGE = "CLASS_CHANGE"
    LOOT_CHEST = "LOOT_CHEST"
    # PvE. BATTLE_WIN is keyed on the lesson, so a lesson pays full price
    # exactly once; BATTLE_REPLAY is keyed on the battle and pays a share.
    BATTLE_WIN = "BATTLE_WIN"
    BATTLE_REPLAY = "BATTLE_REPLAY"


class GoldTransaction(Base):
    """Ledger of every gold movement, and the guard against paying twice.

    The unique constraint on (user_id, reason, ref_id) is what makes settling a
    match idempotent: `ref_id` is the match id, so a second settle of the same
    match cannot insert a second row and therefore cannot move the balance
    again. It doubles as the audit trail when a balance looks wrong.

    `ref_id` is a plain string rather than a foreign key because it points at
    different tables depending on `reason` (a match, a skill, an item).
    """

    __tablename__ = "gold_transactions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "reason", "ref_id", name="uq_gold_transactions_user_id_reason_ref_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[GoldReason] = mapped_column(
        Enum(GoldReason, name="gold_reason"), nullable=False
    )
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
