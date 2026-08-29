from collections import Counter
from random import Random

from app.models.game.game_item import ItemRarity
from app.services.game.loot import (
    MAX_BONUS_DAMAGE_PERMILLE,
    MAX_BONUS_HP,
    MAX_BONUS_STARTING_MANA,
    RARITY_WEIGHTS,
    WIN_RARITY_WEIGHTS,
    ItemDrop,
    StatBonus,
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
            StatBonus(max_hp=5, damage_permille=20, starting_mana=3),
            StatBonus(max_hp=6, damage_permille=30, starting_mana=4),
        ]
    )
    assert total == StatBonus(max_hp=11, damage_permille=50, starting_mana=7)


def test_the_combined_bonus_is_capped() -> None:
    huge = StatBonus(max_hp=500, damage_permille=5000, starting_mana=500)
    total = total_bonus([huge, huge, huge])
    assert total.max_hp == MAX_BONUS_HP
    assert total.damage_permille == MAX_BONUS_DAMAGE_PERMILLE
    assert total.starting_mana == MAX_BONUS_STARTING_MANA


def test_a_negative_bonus_cannot_weaken_a_player() -> None:
    total = total_bonus([StatBonus(max_hp=-50, damage_permille=-900, starting_mana=-9)])
    assert total == StatBonus(max_hp=0, damage_permille=0, starting_mana=0)


def test_equipment_stays_a_flavour_not_a_decision() -> None:
    # The whole equipment set may not add more than a fifth of a health bar.
    assert MAX_BONUS_HP <= 20
    assert MAX_BONUS_DAMAGE_PERMILLE <= 150
