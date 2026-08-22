from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.challenge import Challenge

class ChallengeOption(Base):
    __tablename__ = "challenge_options"
    __table_args__ = (
        UniqueConstraint(
            "challenge_id", "order_index", name="uq_challenge_options_challenge_id_order_index"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )

    challenge_id: Mapped[str] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    image_src: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )
    audio_src: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )
    challenge: Mapped["Challenge"] = relationship("Challenge", back_populates="options")
    