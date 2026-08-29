"""Combat maths for battle-mode duo matches: damage, mana, combos, strikes.

Pure functions with no I/O, in the same shape as `scoring.py`. Elapsed time is
always measured server-side by the caller and never taken from the client.

Every balance constant lives at the top of this module. `resolve_blow` is the
single place damage is scaled or reduced — skills, items and topic elements all
feed it numbers rather than adding branches of their own, so a new item can
never mean a new code path through the engine.
"""

from dataclasses import dataclass
from enum import StrEnum

MAX_HP = 100

# A correct answer is worth BASE_SHARE of MAX_DAMAGE plus a speed bonus that
# decays to zero at the deadline, so answering right is worth 10 to 20.
MAX_DAMAGE = 20
BASE_SHARE = 0.5

MAX_MANA = 100
MANA_ON_CORRECT = 20
MANA_SPEED_BONUS = 15
# A wrong answer still pays a little mana: being locked out of skills for a
# whole match because of one bad round is a worse experience than it is a
# meaningful punishment.
MANA_ON_WRONG = 5

# Two ways to be good at this, deliberately scaled by different mechanisms.
# QUICK adds flat damage, so it is worth the same against every opponent.
# HEAVY only pays off against a defence, so it is worth nothing when unopposed.
# Scaling both the same way would collapse them into one dominant strategy.
QUICK_THRESHOLD_MS = 3000
QUICK_BONUS = 4
HEAVY_THRESHOLD_RATIO = 0.6
HEAVY_PIERCE_SHARE = 0.5

COMBO_TIER_1 = 3
COMBO_TIER_1_MULTIPLIER = 1.5
COMBO_TIER_2 = 5
COMBO_TIER_2_MULTIPLIER = 2.0
# Tier 2 also skips the opponent's next round entirely.
COMBO_STUN_ROUNDS = 1

# Multipliers coming from classes and equipment are integers in thousandths, so
# balance data stays exact and comparable instead of drifting through floats.
PERMILLE_ONE = 1000

# A daily streak buys a head start, capped low on purpose. This rewards the
# habit; it must not make a long-running account unbeatable by a new one.
STREAK_HP_PER_DAY = 1
STREAK_HP_CAP = 15
STREAK_DAYS_PER_MANA = 3
STREAK_MANA_CAP = 10


class StrikeKind(StrEnum):
    QUICK = "QUICK"
    NORMAL = "NORMAL"
    HEAVY = "HEAVY"


@dataclass(frozen=True)
class Blow:
    """One player's attack in one round, fully resolved."""

    raw_damage: int
    strike: StrikeKind
    combo_count: int
    combo_multiplier: float
    is_critical: bool
    stuns_opponent: bool
    element_multiplier: float
    final_damage: int

    @classmethod
    def none(cls) -> "Blow":
        """A round where this player landed nothing."""
        return cls(
            raw_damage=0,
            strike=StrikeKind.NORMAL,
            combo_count=0,
            combo_multiplier=1.0,
            is_critical=False,
            stuns_opponent=False,
            element_multiplier=1.0,
            final_damage=0,
        )


@dataclass(frozen=True)
class StreakBuff:
    """The head start a daily study streak is worth."""

    bonus_max_hp: int
    bonus_starting_mana: int


def streak_buff(day_streak: int) -> StreakBuff:
    """Extra health and opening mana for showing up day after day."""
    days = max(0, day_streak)
    return StreakBuff(
        bonus_max_hp=min(STREAK_HP_CAP, days * STREAK_HP_PER_DAY),
        bonus_starting_mana=min(STREAK_MANA_CAP, days // STREAK_DAYS_PER_MANA),
    )


def _speed_share(elapsed_ms: int, time_limit_seconds: int) -> float:
    """How much of the deadline was left, as 0.0..1.0."""
    limit_ms = time_limit_seconds * 1000
    if limit_ms <= 0:
        return 1.0
    remaining = max(0, limit_ms - max(0, elapsed_ms))
    return remaining / limit_ms


def classify_strike(elapsed_ms: int, time_limit_seconds: int) -> StrikeKind:
    """Which of the two playstyles this answer used.

    Answering inside the first few seconds is a QUICK strike; taking most of
    the clock and still getting it right is a HEAVY one.
    """
    if elapsed_ms <= QUICK_THRESHOLD_MS:
        return StrikeKind.QUICK
    limit_ms = time_limit_seconds * 1000
    if limit_ms > 0 and elapsed_ms >= limit_ms * HEAVY_THRESHOLD_RATIO:
        return StrikeKind.HEAVY
    return StrikeKind.NORMAL


def award_damage(is_correct: bool, elapsed_ms: int, time_limit_seconds: int) -> int:
    """Base damage for one answer: 0 when wrong, 10..20 when right."""
    if not is_correct:
        return 0
    share = BASE_SHARE + (1.0 - BASE_SHARE) * _speed_share(elapsed_ms, time_limit_seconds)
    return round(MAX_DAMAGE * share)


def award_mana(is_correct: bool, elapsed_ms: int, time_limit_seconds: int) -> int:
    """Mana earned by one answer. Answering faster earns more."""
    if not is_correct:
        return MANA_ON_WRONG
    bonus = round(MANA_SPEED_BONUS * _speed_share(elapsed_ms, time_limit_seconds))
    return MANA_ON_CORRECT + bonus


def gain_mana(current: int, amount: int) -> int:
    """Add mana, capped at MAX_MANA."""
    return min(MAX_MANA, max(0, current + amount))


def combo_after(current_combo: int, is_correct: bool) -> int:
    """The combo counter after one answer. Any miss drops it to zero."""
    return current_combo + 1 if is_correct else 0


def combo_multiplier(combo_count: int) -> tuple[float, bool, bool]:
    """(multiplier, is_critical, stuns_opponent) for a combo of this length."""
    if combo_count >= COMBO_TIER_2:
        return COMBO_TIER_2_MULTIPLIER, True, COMBO_STUN_ROUNDS > 0
    if combo_count >= COMBO_TIER_1:
        return COMBO_TIER_1_MULTIPLIER, False, False
    return 1.0, False, False


def apply_damage(hp: int, damage: int) -> int:
    """Subtract damage from a health pool, floored at zero."""
    return max(0, hp - max(0, damage))


def resolve_blow(
    *,
    is_correct: bool,
    elapsed_ms: int,
    time_limit_seconds: int,
    combo_count: int,
    attacker_damage_permille: int = PERMILLE_ONE,
    defender_reduction_permille: int = 0,
    element_multiplier: float = 1.0,
) -> Blow:
    """Resolve one player's attack for one round.

    `combo_count` is the combo *including* this answer, so the third correct
    answer in a row is the one that lands at 1.5x.

    A HEAVY strike ignores part of the defender's damage reduction, which is
    the only thing that makes taking the slow, careful route worth anything.
    """
    if not is_correct:
        return Blow.none()

    raw = award_damage(is_correct, elapsed_ms, time_limit_seconds)
    strike = classify_strike(elapsed_ms, time_limit_seconds)
    if strike is StrikeKind.QUICK:
        raw += QUICK_BONUS

    multiplier, is_critical, stuns = combo_multiplier(combo_count)
    scaled = raw * multiplier * element_multiplier
    scaled = scaled * attacker_damage_permille / PERMILLE_ONE

    reduction = max(0, defender_reduction_permille)
    if strike is StrikeKind.HEAVY:
        reduction = round(reduction * (1.0 - HEAVY_PIERCE_SHARE))
    reduction = min(reduction, PERMILLE_ONE)
    final = round(scaled * (PERMILLE_ONE - reduction) / PERMILLE_ONE)

    return Blow(
        raw_damage=raw,
        strike=strike,
        combo_count=combo_count,
        combo_multiplier=multiplier,
        is_critical=is_critical,
        stuns_opponent=stuns,
        element_multiplier=element_multiplier,
        final_damage=max(0, final),
    )
