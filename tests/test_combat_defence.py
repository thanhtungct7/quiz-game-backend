"""DEF: flat damage taken off every blow, and the promises that keeps it fair.

Defence is the fourth number a class carries, and the only one subtracted
rather than scaled. That single difference is what these tests are about: a
percentage shield is worth most against a critical, while a flat number is
worth most against a stream of ordinary answers -- so the two must not be
allowed to collapse into each other, and the order they apply in decides which
is which.

The balance claims are stated out loud here for the reason `test_pve_balance`
gives: no test fails when a number in the catalog is doubled, it just stops
being a fight.
"""

from app.models.game.game_item import EquipmentSlot
from app.services.game.catalog import (
    ASSASSIN,
    CLASSES,
    ITEMS,
    MAGE,
    MONSTERS,
    WARRIOR,
    MonsterSpec,
)
from app.services.game.combat import (
    MIN_DAMAGE_THROUGH,
    StrikeKind,
    apply_defence,
    resolve_blow,
)
from app.services.game.loot import MAX_BONUS_DEFENCE, StatBonus, total_bonus
from app.services.pve.monster import MonsterProfile, monster_attack

LIMIT = 20  # seconds

# Answering at these marks puts the blow in the strike band named. Read off
# QUICK_THRESHOLD_MS and HEAVY_THRESHOLD_RATIO rather than guessed at.
NORMAL_MS = 6_000
HEAVY_MS = 15_000


def _blow(
    *,
    elapsed_ms: int = NORMAL_MS,
    combo_count: int = 1,
    reduction: int = 0,
    defence: int = 0,
):
    return resolve_blow(
        is_correct=True,
        elapsed_ms=elapsed_ms,
        time_limit_seconds=LIMIT,
        combo_count=combo_count,
        defender_reduction_permille=reduction,
        defender_flat_reduction=defence,
    )


def _profile(spec: MonsterSpec) -> MonsterProfile:
    """The catalog row as a battle reads it, built from the seed spec."""
    return MonsterProfile(
        code=spec.code,
        name=spec.name,
        tier=spec.tier,
        max_hp=spec.max_hp,
        attack_damage=spec.attack_damage,
        damage_reduction_permille=spec.damage_reduction_permille,
        enrage_after_rounds=spec.enrage_after_rounds,
        enrage_multiplier_permille=spec.enrage_multiplier_permille,
        is_boss=spec.is_boss,
        art_code=spec.art_code,
    )


# --- the flat rule ----------------------------------------------------------


def test_defence_comes_straight_off_the_blow() -> None:
    bare = _blow()
    guarded = _blow(defence=3)
    assert bare.final_damage - guarded.final_damage == 3
    assert guarded.defence == 3


def test_defence_never_reduces_a_landed_blow_to_nothing() -> None:
    """A correct answer always lands. DEF slows a fight; it cannot end one."""
    swamped = _blow(defence=1_000)
    assert swamped.final_damage == MIN_DAMAGE_THROUGH


def test_a_full_shield_still_stops_everything_even_behind_defence() -> None:
    """The floor is DEF's, not the shield's -- it must not resurrect a blow a
    100% shield already removed, or casting one would stop being worth it."""
    stopped = _blow(reduction=1_000, defence=5)
    assert stopped.final_damage == 0
    assert stopped.defence == 0


def test_the_shield_applies_before_defence() -> None:
    """Order decides everything. Scaling first and subtracting second makes DEF
    worth the same against every blow; the other way round it would be scaled
    by the shield and the two would stop being different mechanics."""
    shielded = _blow(reduction=500)
    both = _blow(reduction=500, defence=3)
    assert both.final_damage == apply_defence(shielded.final_damage, 3)


def test_a_blow_reports_what_defence_absorbed() -> None:
    assert _blow().defence == 0
    assert _blow(defence=3).defence == 3
    # Capped by what was actually there to take, never more.
    assert _blow(defence=1_000).defence == _blow().final_damage - MIN_DAMAGE_THROUGH


# --- the HEAVY pierce -------------------------------------------------------


def test_a_heavy_strike_pierces_half_the_defence() -> None:
    bare = _blow(elapsed_ms=HEAVY_MS)
    guarded = _blow(elapsed_ms=HEAVY_MS, defence=4)
    assert bare.strike is StrikeKind.HEAVY
    assert bare.final_damage - guarded.final_damage == 2
    assert guarded.defence == 2


def test_the_slow_careful_route_loses_less_to_defence() -> None:
    """The whole reason HEAVY exists. It already pierced the percentage shield;
    piercing DEF too is what keeps that true now there are two defences."""
    assert _blow(elapsed_ms=NORMAL_MS, defence=4).defence == 4
    assert _blow(elapsed_ms=HEAVY_MS, defence=4).defence == 2


# --- the pure helper --------------------------------------------------------


def test_apply_defence_is_a_floor_not_a_heal() -> None:
    assert apply_defence(10, 0) == 10
    assert apply_defence(10, 3) == 7
    assert apply_defence(10, 999) == MIN_DAMAGE_THROUGH
    # Nothing there to take: a blow already stopped stays stopped.
    assert apply_defence(0, 5) == 0
    assert apply_defence(-4, 5) == 0


# --- the monster's side -----------------------------------------------------


def test_a_monster_swing_is_blunted_by_defence() -> None:
    monster = _profile(MONSTERS[0])
    bare = monster_attack(monster=monster, elapsed_seconds=0)
    guarded = monster_attack(monster=monster, elapsed_seconds=0, defender_flat_reduction=3)
    assert bare.final_damage - guarded.final_damage == 3
    assert guarded.raw_damage == bare.raw_damage


def test_the_weakest_monster_still_gets_through_full_armour() -> None:
    """The claim `MAX_BONUS_DEFENCE` is set by. A warrior in the best armour in
    the catalog still takes damage from the smallest thing in it -- otherwise
    the learn path would have a build that simply cannot lose."""
    warrior = next(spec for spec in CLASSES if spec.code == WARRIOR)
    armoured = warrior.defence + MAX_BONUS_DEFENCE
    weakest = min(MONSTERS, key=lambda spec: spec.attack_damage)
    landed = monster_attack(
        monster=_profile(weakest), elapsed_seconds=0, defender_flat_reduction=armoured
    )
    assert landed.final_damage > 0


# --- the balance data -------------------------------------------------------


def test_the_warrior_is_the_class_that_carries_defence() -> None:
    by_code = {spec.code: spec for spec in CLASSES}
    assert by_code[WARRIOR].defence > by_code[ASSASSIN].defence > by_code[MAGE].defence
    assert by_code[MAGE].defence == 0


def test_the_warrior_pays_for_defence_in_health() -> None:
    """Defence and health are the same resource. The warrior was the toughest
    class before DEF existed and must not simply have gained a fourth stat."""
    by_code = {spec.code: spec for spec in CLASSES}
    assert by_code[WARRIOR].max_hp < 130


def test_only_armour_carries_defence() -> None:
    """Three slots, three different decisions. A weapon that also defended
    would make the armour slot the only one with a wrong answer."""
    defending = [spec for spec in ITEMS if spec.bonus_defence > 0]

    assert defending, "the drop table should offer defence somewhere"
    assert all(spec.slot is EquipmentSlot.ARMOR for spec in defending)


def test_piling_on_armour_cannot_beat_the_ceiling() -> None:
    piled = total_bonus([StatBonus(defence=5), StatBonus(defence=5), StatBonus(defence=5)])
    assert piled.defence == MAX_BONUS_DEFENCE
