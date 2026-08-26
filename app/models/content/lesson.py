from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.content.challenge import Challenge
    from app.models.content.unit import Unit

class Lesson(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        UniqueConstraint("unit_id", "order_index", name="uq_lessons_unit_id_order_index"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    title: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    unit_id: Mapped[str] = mapped_column(
        ForeignKey("units.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    is_bank: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false", index=True
    )
    """Holdover storage, not a step on the learning path.

    The learning path is made of small lessons (~10 challenges) so a learner can
    actually finish one. The imported question bank is far larger than the path,
    so every challenge that does not fit into a path lesson is parked in a
    ``is_bank=True`` lesson: still reachable by duo matches and practice draws
    (which sample the whole `challenges` table), but hidden from the course tree
    so it never shows up as a 69k-question "lesson".
    """
    unit: Mapped["Unit"] = relationship("Unit", back_populates="lessons")
    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge", 
        back_populates="lesson", 
        cascade="all, delete-orphan",
        passive_deletes=True, 
        order_by="Challenge.order_index",
    )

