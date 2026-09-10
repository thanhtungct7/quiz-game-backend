"""Rolling a reward chest after a match.

Pure functions with no I/O. The random source is injected rather than taken
from the module, so a seeded `Random` makes every distribution here testable --
the same reason `QuizService` takes its own RNG.

Items only ever move the same numbers a class moves: maximum health, the damage
multiplier, starting mana and defence. That is a hard design rule, not a current
limitation -- but the rule is about *branches*, not about how many numbers there
are. Defence was added as a fourth knob precisely because `resolve_blow` already
took a `defender_flat_reduction` and needed no new branch to read it. An item
that wanted something the engine cannot already resolve is still refused.
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

# Ceilings on what all three equipment slots can add together. Deliberately
# small: equipment is meant to flavour a build, not to decide a match before
# the first question.
MAX_BONUS_HP = 20
MAX_BONUS_DAMAGE_PERMILLE = 150
MAX_BONUS_STARTING_MANA = 15
# Tighter than it looks, and deliberately so: defence is subtracted flat, and
# the weakest monster in the catalog swings for 8. A warrior in full armour
# stands at 3 + 2, which still lets a slime through for 3.
MAX_BONUS_DEFENCE = 2


@dataclass(frozen=True)
class ItemDrop:
    """One catalog item, as far as the roll is concerned."""

    item_id: str
    code: str
    name: str
    rarity: ItemRarity


@dataclass(frozen=True)
class StatBonus:
    max_hp: int = 0
    damage_permille: int = 0
    starting_mana: int = 0
    defence: int = 0

    def capped(self) -> "StatBonus":
        """The same bonus, clamped to what equipment is allowed to contribute."""
        return StatBonus(
            max_hp=_clamp(self.max_hp, MAX_BONUS_HP),
            damage_permille=_clamp(self.damage_permille, MAX_BONUS_DAMAGE_PERMILLE),
            starting_mana=_clamp(self.starting_mana, MAX_BONUS_STARTING_MANA),
            defence=_clamp(self.defence, MAX_BONUS_DEFENCE),
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


def total_bonus(bonuses: list[StatBonus]) -> StatBonus:
    """Add up equipped items, then apply the ceilings."""
    return StatBonus(
        max_hp=sum(bonus.max_hp for bonus in bonuses),
        damage_permille=sum(bonus.damage_permille for bonus in bonuses),
        starting_mana=sum(bonus.starting_mana for bonus in bonuses),
        defence=sum(bonus.defence for bonus in bonuses),
    ).capped()

