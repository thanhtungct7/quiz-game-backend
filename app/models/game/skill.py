from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SkillEffect(StrEnum):
    """What a skill does. The catalog holds the numbers; this picks the branch."""

    DOUBLE_DAMAGE = "DOUBLE_DAMAGE"
    DAMAGE_REDUCTION = "DAMAGE_REDUCTION"
    HEAL = "HEAL"
    TIME_PENALTY = "TIME_PENALTY"
    REMOVE_OPTIONS = "REMOVE_OPTIONS"
    MANA_BURN = "MANA_BURN"
    COMBO_KEEP = "COMBO_KEEP"
    EXECUTE = "EXECUTE"


class SkillUnlockKind(StrEnum):
    STARTER = "STARTER"
    LEVEL_GOLD = "LEVEL_GOLD"
    UNIT_COMPLETION = "UNIT_COMPLETION"


class Skill(Base):
    """One node of a class skill tree.

    Catalog and tree structure share a table: `parent_code` is the edge, and a
    node is reachable once its parent is owned. `magnitude` is read against
    `effect` -- percent for DOUBLE_DAMAGE, thousandths for DAMAGE_REDUCTION,
    health for HEAL, seconds for TIME_PENALTY, and so on -- which is why the
    catalog is data and the meaning is code.
    """

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    effect: Mapped[SkillEffect] = mapped_column(
        Enum(SkillEffect, name="skill_effect"), nullable=False
    )
    # NULL means neutral: usable by every class.
    class_code: Mapped[str | None] = mapped_column(
        ForeignKey("game_classes.code", ondelete="CASCADE"), nullable=True, index=True
    )
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # The node that must be owned first. NULL is a root of its tree.
    parent_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mana_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    magnitude: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    duration_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    unlock_kind: Mapped[SkillUnlockKind] = mapped_column(
        Enum(SkillUnlockKind, name="skill_unlock_kind"), nullable=False
    )
    unlock_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    gold_price: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Set only for UNIT_COMPLETION skills: the unit whose lessons unlock this.
    unlock_unit_id: Mapped[str | None] = mapped_column(
        ForeignKey("units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
