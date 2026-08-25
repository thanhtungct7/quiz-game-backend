from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.content.challenge import Challenge


class Passage(Base):
    __tablename__ = "passages"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    source_ref: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    level_grade: Mapped[str | None] = mapped_column(String(20), nullable=True)
    challenges: Mapped[list["Challenge"]] = relationship("Challenge", back_populates="passage")
