from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ConversationStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"


class ConversationSession(Base):
    """One practice conversation: a learner, a scenario, and what the AI made of it.

    `cefr` is the learner's band when the conversation started, fixed for the
    whole of it, so a level-up mid-conversation does not change how the AI talks.

    `feedback` is filled once, on finishing, and read back on every later
    request -- asking twice never pays for a second call. `score` repeats the
    number inside it so the history list need not read the JSON.
    """

    __tablename__ = "conversation_sessions"
    __table_args__ = (
        Index("ix_conversation_sessions_user_id_started_at", "user_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    scenario_code: Mapped[str] = mapped_column(String(50), nullable=False)
    cefr: Mapped[str] = mapped_column(String(2), nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status"),
        nullable=False,
        default=ConversationStatus.ACTIVE,
        server_default=ConversationStatus.ACTIVE.value,
    )
    user_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The AI said the learner reached the scenario's goal.
    goal_reached: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    feedback: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
