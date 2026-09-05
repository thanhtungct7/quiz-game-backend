from uuid import uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Multipliers are integers in thousandths for the same reason the rest of the
# balance data is: exact, comparable, and free of float drift.
PERMILLE_ONE = 1000


class Monster(Base):
    """One archetype a lesson can be guarded by.

    A catalog row, not an instance: which lesson a monster stands on is derived
    from the unit's position on the learn path (`services/pve/monster.py`), so
    importing a new batch of questions never means assigning monsters by hand.

    Every number here is balance data the engine feeds to `combat.resolve_blow`
    or to the monster's own turn; none of it can introduce a code path.
    """

    __tablename__ = "monsters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tier: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    max_hp: Mapped[int] = mapped_column(Integer, nullable=False)
    attack_damage: Mapped[int] = mapped_column(Integer, nullable=False)
    damage_reduction_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # After this many rounds the monster's damage is multiplied, so a long
    # lesson cannot be turned into a safe place to grind.
    enrage_after_rounds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    enrage_multiplier_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=PERMILLE_ONE, server_default=str(PERMILLE_ONE)
    )
    is_boss: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Questions on this topic hit the monster harder. Left NULL by the seed:
    # the wiring is here, the balance does not lean on it yet.
    weak_topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    art_code: Mapped[str] = mapped_column(String(32), nullable=False, server_default="")
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Monsters are never deleted -- a battle in history names one by code.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
