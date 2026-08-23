from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.challenge import Challenge


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    challenges: Mapped[list["Challenge"]] = relationship("Challenge", back_populates="topic")
