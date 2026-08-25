from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.content.unit import Unit

class Course(Base):
    __tablename__ = "courses"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    title: Mapped[str] = mapped_column(
        String(100),
        nullable=False
    )
    image_src: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )
    units: Mapped[list["Unit"]] = relationship(
        "Unit", back_populates="course",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Unit.order_index"
    )