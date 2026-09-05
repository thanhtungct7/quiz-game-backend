from app.services.game.rewards import WIN_BASE_EXP as DUO_WIN_BASE_EXP
from app.services.pve.rewards import (
    BattleRewardInput,
    compute_battle_reward,
    replayed,
)


def _input(
    *,
    won: bool = True,
    correct_count: int = 10,
    is_boss: bool = False,
    flawless: bool = False,
) -> BattleRewardInput:
    return BattleRewardInput(
        won=won, correct_count=correct_count, is_boss=is_boss, flawless=flawless
    )


def test_a_win_scales_with_correct_answers() -> None:
    reward = compute_battle_reward(_input(correct_count=8))

    assert reward.exp == 20 + 3 * 8
    assert reward.gold == 10 + 1 * 8


def test_a_boss_pays_a_bonus_in_both_currencies() -> None:
    plain = compute_battle_reward(_input(correct_count=8))
    boss = compute_battle_reward(_input(correct_count=8, is_boss=True))

    assert boss.exp == plain.exp + 30
    assert boss.gold == plain.gold + 15


def test_taking_no_damage_pays_experience_only() -> None:
    plain = compute_battle_reward(_input(correct_count=8))
    flawless = compute_battle_reward(_input(correct_count=8, flawless=True))

    assert flawless.exp == plain.exp + 15
    assert flawless.gold == plain.gold


def test_losing_pays_nothing() -> None:
    reward = compute_battle_reward(_input(won=False, correct_count=9))

    assert reward.exp == 0
    assert reward.gold == 0


def test_a_replay_pays_a_rounded_down_share() -> None:
    full = compute_battle_reward(_input(correct_count=10, is_boss=True))
    again = replayed(full)

    assert again.exp == full.exp * 30 // 100
    assert again.gold == full.gold * 30 // 100
    assert again.exp < full.exp


def test_a_replay_of_a_tiny_win_rounds_down_to_nothing_rather_than_up() -> None:
    tiny = compute_battle_reward(_input(correct_count=0))

    assert replayed(tiny).gold == 3  # 10 gold -> 3
    assert replayed(tiny).exp == 6  # 20 exp -> 6


def test_pve_never_pays_better_than_duo() -> None:
    """The point of the split: PvP stays the fastest climb, PvE the sure one."""
    best_pve = compute_battle_reward(
        _input(correct_count=10, is_boss=True, flawless=True)
    )
    assert best_pve.exp < DUO_WIN_BASE_EXP + 5 * 10 + 25
