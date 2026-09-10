"""Turning a build into four numbers, and the two invariants that keeps.

The arithmetic here is shared by a match and a profile card, so a bug in it
would not show up as a wrong screen -- it would show up as a screen that
disagrees with the fight it describes. These tests pin the two properties that
make the breakdown trustworthy: the lines add up to the totals, and the totals
are the same ones `loot.total_bonus` hands the engine.
"""

from app.services.game.catalog import CLASSES, WARRIOR
from app.services.game.combat import MAX_DAMAGE, MAX_HP, PERMILLE_ONE, streak_buff
from app.services.game.combat_stats import (
    BASELINE_LABEL,
    ClassPart,
    ItemPart,
    StatSourceKind,
    attack_for,
    resolve,
)
from app.services.game.loot import MAX_BONUS_DEFENCE, MAX_BONUS_HP, StatBonus, total_bonus

WARRIOR_PART = ClassPart(
    code=WARRIOR,
    name="Chiến binh",
    max_hp=115,
    damage_permille=900,
    starting_mana=10,
    defence=3,
)


def _item(code: str, **bonuses: int) -> ItemPart:
    return ItemPart(code=code, name=code.title(), **bonuses)


# --- reading a multiplier as damage -----------------------------------------


def test_the_multiplier_is_published_as_damage() -> None:
    assert attack_for(PERMILLE_ONE) == MAX_DAMAGE
    assert attack_for(900) == 18
    assert attack_for(1050) == 21
    assert attack_for(1250) == 25


def test_every_class_in_the_catalog_reads_as_a_number() -> None:
    """A card compares ATK against HP. A class whose damage cannot be read as a
    number would leave one of the three bars blank."""
    for spec in CLASSES:
        assert attack_for(spec.damage_permille) > 0


# --- the baseline -----------------------------------------------------------


def test_a_player_with_no_class_still_has_a_stat_block() -> None:
    build = resolve(base=None)

    assert build.stats.hp == MAX_HP
    assert build.stats.atk == MAX_DAMAGE
    assert build.stats.defence == 0


def test_the_baseline_is_a_line_of_the_breakdown_like_any_other() -> None:
    """Otherwise the lines would not add up for a player who has not chosen a
    class -- the numbers would come from nowhere the modal could name."""
    build = resolve(base=None)

    assert [source.label for source in build.sources] == [BASELINE_LABEL]
    assert build.sources[0].hp == MAX_HP


# --- the lines add up -------------------------------------------------------


def _sums_match(build) -> None:
    assert sum(source.hp for source in build.sources) == build.stats.hp
    assert sum(source.atk for source in build.sources) == build.stats.atk
    assert sum(source.defence for source in build.sources) == build.stats.defence
    assert sum(source.mana for source in build.sources) == build.stats.mana


def test_the_lines_add_up_to_the_totals() -> None:
    build = resolve(
        base=WARRIOR_PART,
        equipment=[
            _item("STEEL_SWORD", bonus_damage_permille=45),
            _item("CHAIN_MAIL", bonus_max_hp=8, bonus_defence=1),
            _item("MANA_RING", bonus_starting_mana=4),
        ],
        day_streak=12,
    )

    _sums_match(build)


def test_attack_is_attributed_as_a_running_difference() -> None:
    """Converting each item's own multiplier and summing would lose points to
    rounding: three items worth 25 thousandths each round to nothing on their
    own but to two whole points together."""
    build = resolve(
        base=None,
        equipment=[
            _item("A", bonus_damage_permille=25),
            _item("B", bonus_damage_permille=25),
            _item("C", bonus_damage_permille=25),
        ],
    )

    assert build.damage_permille == PERMILLE_ONE + 75
    assert build.stats.atk == attack_for(PERMILLE_ONE + 75)
    _sums_match(build)


def test_the_lines_still_add_up_when_a_ceiling_bites() -> None:
    """The case that makes attribution hard: the player is wearing more than
    the cap allows, so the total is smaller than the sum of the labels."""
    greedy = [_item(f"ITEM_{n}", bonus_max_hp=MAX_BONUS_HP, bonus_defence=5) for n in range(3)]

    build = resolve(base=WARRIOR_PART, equipment=greedy)

    _sums_match(build)
    assert build.defence == WARRIOR_PART.defence + MAX_BONUS_DEFENCE


def test_the_capped_total_is_the_one_a_match_would_use() -> None:
    """`loot.total_bonus` is what the engine reads. If this drifted, a card and
    a fight would disagree about what the same armour is worth."""
    worn = [
        _item("A", bonus_max_hp=15, bonus_defence=5),
        _item("B", bonus_max_hp=15, bonus_defence=5),
    ]

    build = resolve(base=None, equipment=worn)
    gear = total_bonus(
        [StatBonus(max_hp=item.bonus_max_hp, defence=item.bonus_defence) for item in worn]
    )

    assert build.max_hp == MAX_HP + gear.max_hp
    assert build.defence == gear.defence


# --- the streak -------------------------------------------------------------


def test_a_streak_buys_health_and_mana_but_never_damage_or_armour() -> None:
    build = resolve(base=WARRIOR_PART, day_streak=30)
    streak = next(
        source for source in build.sources if source.kind is StatSourceKind.STREAK
    )
    buff = streak_buff(30)

    assert streak.hp == buff.bonus_max_hp
    assert streak.mana == buff.bonus_starting_mana
    assert streak.atk == 0
    assert streak.defence == 0


def test_a_player_with_no_streak_gets_no_streak_line() -> None:
    """An empty line reads as a reward that failed rather than one not earned."""
    build = resolve(base=WARRIOR_PART, day_streak=0)

    assert all(source.kind is not StatSourceKind.STREAK for source in build.sources)


# --- ordering ---------------------------------------------------------------


def test_the_breakdown_reads_class_then_gear_then_streak() -> None:
    build = resolve(
        base=WARRIOR_PART, equipment=[_item("CHAIN_MAIL", bonus_defence=1)], day_streak=5
    )

    assert [source.kind for source in build.sources] == [
        StatSourceKind.CLASS,
        StatSourceKind.EQUIPMENT,
        StatSourceKind.STREAK,
    ]
