"""What a lesson battle pays out in experience and gold.

Pure functions with no I/O, the same shape as `services/game/rewards.py` and
deliberately smaller than it: beating a monster on your own has to be worth
something, but duo must stay the fastest way up. PvE is the reliable road, not
the quick one.

The learn path pays nothing today -- a player who only studies stays level 1
and can never unlock a skill. This table is what closes that hole.
"""

from dataclasses import dataclass

WIN_BASE_EXP = 20
WIN_EXP_PER_CORRECT = 3
WIN_BASE_GOLD = 10
WIN_GOLD_PER_CORRECT = 1

# A unit's last lesson is guarded by a boss, and is worth the detour.
BOSS_BONUS_EXP = 30
BOSS_BONUS_GOLD = 15

# Answering everything right means the monster never swung. Paid in experience
# only: mastery should move the level, not the wallet.
FLAWLESS_BONUS_EXP = 15

# Replaying a lesson already cleared pays a share of the same battle's worth,
# so grinding one easy lesson can never beat moving forward.
REPLAY_PERMILLE = 300
PERMILLE_ONE = 1000


@dataclass(frozen=True)
class BattleRewardInput:
    """Everything a payout depends on, gathered by the caller."""

    won: bool
    correct_count: int
    is_boss: bool
    flawless: bool


@dataclass(frozen=True)
class BattleReward:
    exp: int
    gold: int

    @classmethod
    def none(cls) -> "BattleReward":
        return cls(exp=0, gold=0)


def compute_battle_reward(data: BattleRewardInput) -> BattleReward:
    """The full price of one battle, before the replay discount.

    Losing pays nothing, and costs nothing: the run is its own punishment, and
    every answer given along the way was still recorded as progress.
    """
    if not data.won:
        return BattleReward.none()

    exp = WIN_BASE_EXP + WIN_EXP_PER_CORRECT * max(0, data.correct_count)
    gold = WIN_BASE_GOLD + WIN_GOLD_PER_CORRECT * max(0, data.correct_count)
    if data.is_boss:
        exp += BOSS_BONUS_EXP
        gold += BOSS_BONUS_GOLD
    if data.flawless:
        exp += FLAWLESS_BONUS_EXP
    return BattleReward(exp=exp, gold=gold)


def replayed(reward: BattleReward) -> BattleReward:
    """The same battle's payout when this lesson has already been cleared."""
    return BattleReward(
        exp=reward.exp * REPLAY_PERMILLE // PERMILLE_ONE,
        gold=reward.gold * REPLAY_PERMILLE // PERMILLE_ONE,
    )
