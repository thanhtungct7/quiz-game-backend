from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
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
    unit: Mapped["Unit"] = relationship("Unit", back_populates="lessons")
    challenges: Mapped[list["Challenge"]] = relationship(
        "Challenge", 
        back_populates="lesson", 
        cascade="all, delete-orphan",
        passive_deletes=True, 
        order_by="Challenge.order_index",
    )

