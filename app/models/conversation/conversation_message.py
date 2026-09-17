from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class ConversationMessage(Base):
    """One line of a practice conversation, in the order `seq` gives.

    `translation_vi` caches the Vietnamese translation of an AI line the first
    time a learner asks for it.
    """

    __tablename__ = "conversation_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_conversation_messages_session_id_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    session_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="conversation_message_role"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    translation_vi: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
