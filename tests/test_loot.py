from collections import Counter
from random import Random

from app.models.game.game_item import ItemRarity
from app.services.game.loot import (
    MAX_BONUS_EXP_PERMILLE,
    MAX_BONUS_GOLD_PERMILLE,
    RARITY_WEIGHTS,
    WIN_RARITY_WEIGHTS,
    ItemDrop,
    RewardBonus,
    apply_bonus,
    roll_item,
    roll_rarity,
    total_bonus,
)

ROLLS = 4000


def _pool() -> list[ItemDrop]:
    return [
        ItemDrop(
            item_id=f"item-{rarity.value}",
            code=rarity.value,
            name=rarity.value,
            rarity=rarity,
        )
        for rarity in ItemRarity
    ]


def _distribution(*, won: bool) -> Counter[ItemRarity]:
    rng = Random(20260829)  # noqa: S311 - deterministic test fixture
    return Counter(roll_rarity(rng, won=won) for _ in range(ROLLS))


def test_common_drops_dominate() -> None:
    counts = _distribution(won=False)
    assert counts[ItemRarity.COMMON] > counts[ItemRarity.RARE]
    assert counts[ItemRarity.RARE] > counts[ItemRarity.EPIC]
    assert counts[ItemRarity.EPIC] > counts[ItemRarity.LEGENDARY]


def test_the_distribution_is_close_to_the_declared_weights() -> None:
    counts = _distribution(won=False)
    total = sum(RARITY_WEIGHTS.values())
    for rarity, weight in RARITY_WEIGHTS.items():
        expected = ROLLS * weight / total
        assert abs(counts[rarity] - expected) < max(30, expected * 0.35), rarity


def test_winning_shifts_the_odds_upward() -> None:
    losing = _distribution(won=False)
    winning = _distribution(won=True)
    assert winning[ItemRarity.COMMON] < losing[ItemRarity.COMMON]
    assert winning[ItemRarity.EPIC] > losing[ItemRarity.EPIC]
    assert WIN_RARITY_WEIGHTS[ItemRarity.LEGENDARY] > RARITY_WEIGHTS[ItemRarity.LEGENDARY]


def test_the_same_seed_rolls_the_same_chest() -> None:
    first = roll_item(Random(7), _pool(), won=True)  # noqa: S311
    second = roll_item(Random(7), _pool(), won=True)  # noqa: S311
    assert first == second


def test_an_empty_catalog_drops_nothing() -> None:
    assert roll_item(Random(1), [], won=True) is None  # noqa: S311


def test_a_missing_rarity_falls_back_rather_than_dropping_nothing() -> None:
    # Only commons exist, but every roll must still produce an item.
    commons = [
        ItemDrop(item_id="i1", code="C1", name="C1", rarity=ItemRarity.COMMON),
        ItemDrop(item_id="i2", code="C2", name="C2", rarity=ItemRarity.COMMON),
    ]
    rng = Random(3)  # noqa: S311
    assert all(roll_item(rng, commons, won=True) is not None for _ in range(50))


def test_bonuses_add_up_across_slots() -> None:
    total = total_bonus(
        [
            RewardBonus(exp_permille=20, gold_permille=10),
            RewardBonus(exp_permille=30, gold_permille=15),
        ]
    )
    assert total == RewardBonus(exp_permille=50, gold_permille=25)


def test_the_combined_bonus_is_capped() -> None:
    huge = RewardBonus(exp_permille=5000, gold_permille=5000)
    total = total_bonus([huge, huge, huge])
    assert total.exp_permille == MAX_BONUS_EXP_PERMILLE
    assert total.gold_permille == MAX_BONUS_GOLD_PERMILLE


def test_a_negative_bonus_cannot_weaken_a_reward() -> None:
    total = total_bonus([RewardBonus(exp_permille=-900, gold_permille=-900)])
    assert total == RewardBonus(exp_permille=0, gold_permille=0)


def test_equipment_stays_a_flavour_not_a_decision() -> None:
    # The whole equipment set may not add more than 15% to either payout.
    assert MAX_BONUS_EXP_PERMILLE <= 150
    assert MAX_BONUS_GOLD_PERMILLE <= 150


def test_apply_bonus_adds_the_percentage_on_top() -> None:
    assert apply_bonus(100, 150) == 115


def test_apply_bonus_never_touches_a_non_positive_amount() -> None:
    assert apply_bonus(0, 150) == 0
    assert apply_bonus(-30, 150) == -30


def test_apply_bonus_ignores_a_negative_percentage() -> None:
    assert apply_bonus(100, -500) == 100
