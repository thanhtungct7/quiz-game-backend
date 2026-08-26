from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.duo.duo_match import DuoMatch


class DuoMatchRound(Base):
    """One question inside a duo match, with both players' answers."""

    __tablename__ = "duo_match_rounds"
    __table_args__ = (
        UniqueConstraint(
            "match_id", "round_index", name="uq_duo_match_rounds_match_id_round_index"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    match_id: Mapped[str] = mapped_column(
        ForeignKey("duo_matches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    round_index: Mapped[int] = mapped_column(Integer, nullable=False)
    challenge_id: Mapped[str | None] = mapped_column(
        ForeignKey("challenges.id", ondelete="SET NULL"), nullable=True, index=True
    )
    player_one_option_id: Mapped[str | None] = mapped_column(
        ForeignKey("challenge_options.id", ondelete="SET NULL"), nullable=True
    )
    player_two_option_id: Mapped[str | None] = mapped_column(
        ForeignKey("challenge_options.id", ondelete="SET NULL"), nullable=True
    )
    player_one_correct: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    player_two_correct: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    player_one_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_two_elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    player_one_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    player_two_points: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    match: Mapped["DuoMatch"] = relationship("DuoMatch", back_populates="rounds")
