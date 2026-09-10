"""Rolling a reward chest after a match, and what equipping its contents buys.

Pure functions with no I/O. The random source is injected rather than taken
from the module, so a seeded `Random` makes every distribution here testable --
the same reason `QuizService` takes its own RNG.

Items no longer move a combat number -- that loop (`resolve_blow`'s
`attacker_damage_permille`/`defender_flat_reduction`) reads only class and
streak now, see `combat_stats.resolve`. What equipping an item buys is a
percentage on top of what a match or lesson pays out in EXP and Gold, applied
in `settlement.GameSettlementService`. An item that wanted something the
settlement layer cannot already resolve is still refused.
"""

from dataclasses import dataclass
from random import Random

from app.models.game.game_item import ItemRarity

# A chest is mostly common. The long tail is what makes opening one interesting.
RARITY_WEIGHTS: dict[ItemRarity, int] = {
    ItemRarity.COMMON: 70,
    ItemRarity.RARE: 22,
    ItemRarity.EPIC: 7,
    ItemRarity.LEGENDARY: 1,
}
# Winning shifts weight up the table without ever guaranteeing anything.
WIN_RARITY_WEIGHTS: dict[ItemRarity, int] = {
    ItemRarity.COMMON: 55,
    ItemRarity.RARE: 30,
    ItemRarity.EPIC: 12,
    ItemRarity.LEGENDARY: 3,
}

# Ceilings on what all three equipment slots can add together, in thousandths
# (150 = +15%). Deliberately small: a full loadout should feel like a
# meaningful head start on grinding XP and Gold, never like the difference
# between passing and failing a lesson or a match.
MAX_BONUS_EXP_PERMILLE = 150
MAX_BONUS_GOLD_PERMILLE = 150


@dataclass(frozen=True)
class ItemDrop:
    """One catalog item, as far as the roll is concerned."""

    item_id: str
    code: str
    name: str
    rarity: ItemRarity


@dataclass(frozen=True)
class RewardBonus:
    """The EdTech buff a piece of equipment (or a whole loadout) is worth."""

    exp_permille: int = 0
    gold_permille: int = 0

    def capped(self) -> "RewardBonus":
        """The same bonus, clamped to what equipment is allowed to contribute."""
        return RewardBonus(
            exp_permille=_clamp(self.exp_permille, MAX_BONUS_EXP_PERMILLE),
            gold_permille=_clamp(self.gold_permille, MAX_BONUS_GOLD_PERMILLE),
        )


def _clamp(value: int, ceiling: int) -> int:
    return max(0, min(value, ceiling))


def roll_rarity(rng: Random, *, won: bool) -> ItemRarity:
    weights = WIN_RARITY_WEIGHTS if won else RARITY_WEIGHTS
    tiers = list(weights)
    return rng.choices(tiers, weights=[weights[tier] for tier in tiers], k=1)[0]


def roll_item(rng: Random, pool: list[ItemDrop], *, won: bool) -> ItemDrop | None:
    """Pick one item, or None when the catalog has nothing to give.

    The rolled rarity is a preference, not a promise: an empty tier falls back
    to whatever the pool does hold rather than dropping the reward entirely.
    """
    if not pool:
        return None
    rarity = roll_rarity(rng, won=won)
    candidates = [item for item in pool if item.rarity is rarity] or pool
    return rng.choice(candidates)


def total_bonus(bonuses: list[RewardBonus]) -> RewardBonus:
    """Add up equipped items, then apply the ceilings."""
    return RewardBonus(
        exp_permille=sum(bonus.exp_permille for bonus in bonuses),
        gold_permille=sum(bonus.gold_permille for bonus in bonuses),
    ).capped()


def apply_bonus(amount: int, permille: int) -> int:
    """`amount` plus its share of `permille`, never touching a non-positive
    amount: a bonus is a reward for owning gear, not a way to shrink a
    forfeit penalty or amplify a loss."""
    if amount <= 0:
        return amount
    return amount + amount * max(0, permille) // 1000

