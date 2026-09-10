"""The four numbers a profile card shows, and where each one came from.

This is the arithmetic that turns a stored build -- a class and a daily streak
-- into HP, ATK, DEF and MANA. It lives here rather than inside
`LoadoutBuilder` because two callers need it and only one of them may touch the
database: a match resolves a build in order to fight with it, and a profile
screen resolves the same build in order to draw it. Both read this, so the
numbers on the card are the numbers in the fight by construction rather than by
two implementations agreeing.

Equipment is deliberately not one of the inputs. It used to move these same
four numbers; the EdTech redesign moved that lever to `loot.RewardBonus`
instead, a percentage on a match or lesson's EXP/Gold payout rather than on
its odds of being won, so nothing here can be bought.

Pure functions with no I/O, in the same shape as `combat.py` and `loot.py`. The
ORM row is mapped to [ClassPart] by whoever loaded it.

ATK is the one number that is not stored as itself. The engine scales damage by
`damage_permille`, a multiplier in thousandths, which is exact and comparable
and completely unreadable on a card. So it is published as the damage a correct
answer actually deals -- `MAX_DAMAGE` put through the multiplier -- which is a
number a player can hold against the HP number next to it.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.models.game.game_class import GameClass
from app.services.game.combat import MAX_DAMAGE, MAX_HP, PERMILLE_ONE, streak_buff


def attack_for(damage_permille: int) -> int:
    """The damage multiplier, read as damage.

    Deliberately lossy: two multipliers a few thousandths apart land on the same
    displayed number, which is the point. A card is for comparing builds, not
    for auditing them -- `damage_permille` is still published beside this for
    anyone who wants the exact figure.
    """
    return round(MAX_DAMAGE * max(0, damage_permille) / PERMILLE_ONE)


@dataclass(frozen=True)
class CombatStats:
    """What a player brings into a fight, as four plain numbers."""

    hp: int
    atk: int
    defence: int
    mana: int
    # The exact multiplier behind `atk`, carried so nothing has to reverse the
    # rounding to get it back.
    damage_permille: int


class StatSourceKind(StrEnum):
    CLASS = "CLASS"
    STREAK = "STREAK"


@dataclass(frozen=True)
class StatSource:
    """One line of the breakdown: what this thing contributed, on its own.

    The four figures are *deltas*, and they always add up to [CombatStats]
    exactly -- see [resolve] for why that takes care.
    """

    kind: StatSourceKind
    code: str
    label: str
    hp: int
    atk: int
    defence: int
    mana: int


@dataclass(frozen=True)
class ClassPart:
    """A class row, reduced to the four numbers it moves."""

    code: str
    name: str
    max_hp: int
    damage_permille: int
    starting_mana: int
    defence: int


def class_part(row: GameClass) -> ClassPart:
    """A catalog row as this module reads it.

    Mapping lives here rather than at each call site so that a match and a
    profile card cannot pick different columns out of the same row.
    """
    return ClassPart(
        code=row.code,
        name=row.name,
        max_hp=row.max_hp,
        damage_permille=row.damage_permille,
        starting_mana=row.starting_mana,
        defence=row.defence,
    )


@dataclass(frozen=True)
class Build:

    """A resolved build: the totals a match uses, and the lines a card shows."""

    max_hp: int
    damage_permille: int
    starting_mana: int
    defence: int
    sources: tuple[StatSource, ...]

    @property
    def stats(self) -> CombatStats:
        return CombatStats(
            hp=self.max_hp,
            atk=attack_for(self.damage_permille),
            defence=self.defence,
            mana=self.starting_mana,
            damage_permille=self.damage_permille,
        )


# The baseline a player fights on before they have chosen anything. Same figures
# `loadout.default_loadout` uses, and for the same reason: nobody is turned away
# from a match for not having picked a class yet.
BASELINE_LABEL = "Cơ bản"
STREAK_LABEL = "Chuỗi ngày"


def resolve(
    *,
    base: ClassPart | None,
    day_streak: int = 0,
) -> Build:
    """Add a build up, and record what each part of it was worth."""
    origin = base or ClassPart(
        code="",
        name=BASELINE_LABEL,
        max_hp=MAX_HP,
        damage_permille=PERMILLE_ONE,
        starting_mana=0,
        defence=0,
    )

    hp = origin.max_hp
    permille = origin.damage_permille
    mana = origin.starting_mana
    defence = origin.defence

    sources = [
        StatSource(
            kind=StatSourceKind.CLASS,
            code=origin.code,
            label=origin.name,
            hp=hp,
            atk=attack_for(permille),
            defence=defence,
            mana=mana,
        )
    ]

    buff = streak_buff(day_streak)
    if buff.bonus_max_hp or buff.bonus_starting_mana:
        hp += buff.bonus_max_hp
        mana += buff.bonus_starting_mana
        sources.append(
            StatSource(
                kind=StatSourceKind.STREAK,
                code=str(day_streak),
                label=STREAK_LABEL,
                hp=buff.bonus_max_hp,
                # A habit buys health and opening mana, never damage and never
                # armour: it must not out-scale the class that chose either.
                atk=0,
                defence=0,
                mana=buff.bonus_starting_mana,
            )
        )

    return Build(
        max_hp=hp,
        damage_permille=permille,
        starting_mana=mana,
        defence=defence,
        sources=tuple(sources),
    )
