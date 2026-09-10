"""The monster's side of a lesson battle: its turn, its rage, and which
monster a lesson gets.

Pure functions with no I/O, in the same shape as `services/game/combat.py`,
which resolves the player's side. Every balance constant lives at the top of
this module.

The player attacks through `combat.resolve_blow` -- the same maths duo uses,
so a build that works in PvP works here. Only the monster's blow is new, and it
is deliberately the simplest thing that can be balanced: a flat number,
multiplied when the monster is enraged, reduced by whatever shield the player
is standing behind and then by the DEF their class and armour carry.

The monster now swings on a clock of its own rather than on the player's
mistakes, so `cast_interval_seconds` sits beside the damage as a balance knob
of equal weight: how *hard* it hits and how *often* are the two halves of the
same difficulty.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.services.game.combat import PERMILLE_ONE, apply_defence
from app.services.pve.clock import SECONDS_PER_ROUND

# Six tiers over the whole learn path. A unit is eight lessons, so a tier
# covers eight units: long enough that meeting a new monster still reads as
# progress, short enough that a 48-unit path uses the whole catalog.
UNITS_PER_TIER = 8
MIN_TIER = 1
MAX_TIER = 6

# A question on the monster's weak topic hits harder. Fed to `resolve_blow` as
# its `element_multiplier`, so it needs no branch of its own in the engine.
WEAKNESS_MULTIPLIER = 1.5

# How often the monster swings, derived from tier rather than stored, so the
# whole difficulty curve is one line to read and one line to retune. A tier-1
# monster gives the player 15 seconds between blows and a tier-6 one 12.5, so a
# gate that takes ten correct answers -- about a minute -- eats three or four
# blows rather than a dozen.
#
# A boss winds up *slower* than the ordinary monster of its tier, not faster.
# It is on screen for half again as long, so swinging more often would simply
# kill everyone; instead it takes its time and each blow is worth watching the
# bar for, which is what the wind-up bar was drawn for in the first place.
BASE_CAST_MS = 15000
CAST_STEP_PER_TIER_MS = 500
BOSS_CAST_RATIO = 1.4
MIN_CAST_MS = 1200


class MonsterIntentKind(StrEnum):
    """What the blow now winding up will be when it lands.

    Sent alongside the cast deadline so the bar filling on screen carries a
    number as well as a countdown.
    """

    ATTACK = "ATTACK"
    ENRAGED_ATTACK = "ENRAGED_ATTACK"


@dataclass(frozen=True)
class MonsterProfile:
    """A catalog row resolved for one battle, read-only from then on.

    The engine runs on this rather than on the ORM object for the same reason
    it runs on `PlayerLoadout`: a live round must not touch the database, and a
    catalog edit must not change a fight that is already under way.
    """

    code: str
    name: str
    tier: int
    max_hp: int
    attack_damage: int
    damage_reduction_permille: int
    enrage_after_rounds: int
    enrage_multiplier_permille: int
    is_boss: bool
    art_code: str
    weak_topic_id: str | None = None


@dataclass(frozen=True)
class MonsterAttack:
    """The monster's counter-attack for one round, fully resolved."""

    raw_damage: int
    enraged: bool
    final_damage: int

    @classmethod
    def none(cls) -> "MonsterAttack":
        """A round the monster did not get to swing in."""
        return cls(raw_damage=0, enraged=False, final_damage=0)


def tier_for_unit(unit_order_index: int) -> int:
    """Which tier of monster guards a unit at this point on the path."""
    steps = max(0, unit_order_index) // UNITS_PER_TIER
    return MIN_TIER + min(MAX_TIER - MIN_TIER, steps)


def is_boss_lesson(lesson_order_index: int, last_order_index: int) -> bool:
    """Whether this lesson is the one that closes its unit.

    The caller passes the highest `order_index` among the unit's path lessons;
    bank lessons are not on the path and never hold a boss.
    """
    return lesson_order_index >= last_order_index


def select_monster(
    candidates: Sequence[MonsterProfile], *, tier: int, is_boss: bool
) -> MonsterProfile | None:
    """The monster of this kind that guards a lesson of this tier.

    Falls back to the highest tier at or below the one asked for, so a learn
    path that grows past the catalog still has something to fight instead of
    losing its battle screen entirely.
    """
    matching = [
        candidate
        for candidate in candidates
        if candidate.is_boss is is_boss and candidate.tier <= tier
    ]
    if not matching:
        return None
    return max(matching, key=lambda candidate: candidate.tier)


def cast_interval_seconds(monster: MonsterProfile) -> float:
    """How long the monster takes to wind up one blow."""
    tier = max(MIN_TIER, min(MAX_TIER, monster.tier))
    interval = float(BASE_CAST_MS - (tier - MIN_TIER) * CAST_STEP_PER_TIER_MS)
    if monster.is_boss:
        interval *= BOSS_CAST_RATIO
    return max(MIN_CAST_MS, interval) / 1000.0


def enrage_after_seconds(monster: MonsterProfile) -> float:
    """When the monster starts hitting harder, counted from the opening bell.

    `enrage_after_rounds` is still authored in rounds because duo still runs in
    rounds; a realtime fight reads one authored round as `SECONDS_PER_ROUND`.
    """
    return max(0, monster.enrage_after_rounds) * SECONDS_PER_ROUND


def is_enraged(elapsed_seconds: float, monster: MonsterProfile) -> bool:
    """Whether the fight has run long enough for the monster to hit harder.

    Timed rather than counted: a player who stalls no longer holds the monster
    at its opening strength by simply not answering.
    """
    if monster.enrage_after_rounds <= 0:
        return True
    return elapsed_seconds >= enrage_after_seconds(monster)


def element_multiplier(weak_topic_id: str | None, question_topic_id: str | None) -> float:
    """The player's damage multiplier against this monster on this question."""
    if weak_topic_id is None or question_topic_id is None:
        return 1.0
    return WEAKNESS_MULTIPLIER if weak_topic_id == question_topic_id else 1.0


def monster_attack(
    *,
    monster: MonsterProfile,
    elapsed_seconds: float,
    defender_reduction_permille: int = 0,
    defender_flat_reduction: int = 0,
) -> MonsterAttack:
    """The blow the monster lands when its cast finishes.

    No longer earned by a missed question: the cast bar decides, so a perfect
    run is one where the player killed the monster before it ever completed a
    wind-up. Both defences are whatever the player has standing on them, read
    in the same order and with the same meaning `combat.resolve_blow` gives
    them: the percentage shield first, then flat DEF off what is left.

    Monsters hit for single digits, so a point of DEF is worth much more here
    than it is in duo. That is the intended shape -- gear and a class are meant
    to make the learn path easier -- and it is why `loot.MAX_BONUS_DEFENCE` is
    tight enough that the weakest monster in the catalog still gets through.
    """
    enraged = is_enraged(elapsed_seconds, monster)
    raw = max(0, monster.attack_damage)
    if enraged:
        raw = raw * max(0, monster.enrage_multiplier_permille) // PERMILLE_ONE

    reduction = min(max(0, defender_reduction_permille), PERMILLE_ONE)
    after_shield = raw * (PERMILLE_ONE - reduction) // PERMILLE_ONE
    final = apply_defence(after_shield, defender_flat_reduction)
    return MonsterAttack(raw_damage=raw, enraged=enraged, final_damage=max(0, final))


def next_swing(
    *,
    monster: MonsterProfile,
    elapsed_seconds: float,
    defender_reduction_permille: int = 0,
    defender_flat_reduction: int = 0,
) -> tuple[MonsterIntentKind, int]:
    """What the cast currently filling will do when it lands.

    Read without spending anything, purely so the health bar the player is
    watching can be read against a number rather than a surprise.
    """
    attack = monster_attack(
        monster=monster,
        elapsed_seconds=elapsed_seconds,
        defender_reduction_permille=defender_reduction_permille,
        defender_flat_reduction=defender_flat_reduction,
    )

    kind = MonsterIntentKind.ENRAGED_ATTACK if attack.enraged else MonsterIntentKind.ATTACK
    return kind, attack.final_damage
