from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.content.course import Course
    from app.models.content.lesson import Lesson

class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("course_id", "order_index", name="uq_units_course_id_order_index"),
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
    description: Mapped[str] = mapped_column(Text, nullable=False)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("courses.id",ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    course: Mapped["Course"] = relationship("Course", back_populates="units")
    lessons: Mapped[list["Lesson"]] = relationship(
        "Lesson",
        back_populates="unit",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Lesson.order_index",
    )
    