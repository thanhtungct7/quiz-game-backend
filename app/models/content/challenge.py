from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ARRAY, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.content.challenge_option import ChallengeOption
    from app.models.content.lesson import Lesson
    from app.models.content.passage import Passage
    from app.models.content.topic import Topic


class ChallengeType(StrEnum):
    SELECT = "SELECT"
    ASSIST = "ASSIST"


class ChallengeDifficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"

class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (
        UniqueConstraint("lesson_id", "order_index", name="uq_challenges_lesson_id_order_index"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key = True,
        default=lambda: str(uuid4())
    )
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lessons.id",ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    type: Mapped[ChallengeType] = mapped_column(
        Enum(ChallengeType, name="challenge_type"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[ChallengeDifficulty] = mapped_column(
        Enum(ChallengeDifficulty, name="challenge_difficulty"),
        nullable=False,
        default=ChallengeDifficulty.EASY,
        server_default=ChallengeDifficulty.EASY.value,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    passage_id: Mapped[str | None] = mapped_column(
        ForeignKey("passages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    correct_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)), nullable=True)
    cefr_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    toeic_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    toeic_min_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lesson: Mapped["Lesson"] = relationship("Lesson", back_populates="challenges")
    topic: Mapped["Topic | None"] = relationship("Topic", back_populates="challenges")
    passage: Mapped["Passage | None"] = relationship("Passage", back_populates="challenges")
    options: Mapped[list["ChallengeOption"]] = relationship(
        "ChallengeOption",
        back_populates="challenge",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChallengeOption.order_index",
    )
