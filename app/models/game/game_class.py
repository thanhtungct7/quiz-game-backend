from uuid import uuid4

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Multipliers are integers in thousandths so balance data stays exact and
# comparable; 1000 means "unchanged". Mirrors combat.PERMILLE_ONE.
PERMILLE_ONE = 1000


class GameClass(Base):
    """A character class: the stat block a player brings into a match.

    Four knobs, deliberately. A class changes the numbers fed to
    `combat.resolve_blow`; it never adds a branch to how a blow is resolved,
    so adding a class is a data change and not an engine change.

    `defence` is flat damage taken off every incoming blow rather than a
    percentage, so it reads as a number a player can compare against the damage
    numbers they see. It feeds `resolve_blow`'s existing `defender_flat_
    reduction`, which is why a fourth knob still costs the engine nothing.
    """

    __tablename__ = "game_classes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    max_hp: Mapped[int] = mapped_column(Integer, nullable=False)
    damage_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=PERMILLE_ONE, server_default=str(PERMILLE_ONE)
    )
    starting_mana: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    defence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
