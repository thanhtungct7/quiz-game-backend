import pytest

from app.services.pve.clock import SECONDS_PER_ROUND
from app.services.pve.monster import (
    BASE_CAST_MS,
    BOSS_CAST_RATIO,
    MAX_TIER,
    MIN_CAST_MS,
    UNITS_PER_TIER,
    WEAKNESS_MULTIPLIER,
    MonsterIntentKind,
    MonsterProfile,
    cast_interval_seconds,
    element_multiplier,
    is_boss_lesson,
    is_enraged,
    monster_attack,
    next_swing,
    tier_for_unit,
)

ENRAGE_AFTER = 5
# The monster's clock, not the player's: rage arrives after this many seconds
# of fighting however many questions were answered in them.
ENRAGE_SECONDS = ENRAGE_AFTER * SECONDS_PER_ROUND


def _monster(
    *,
    attack_damage: int = 10,
    enrage_after_rounds: int = ENRAGE_AFTER,
    enrage_multiplier_permille: int = 1500,
    weak_topic_id: str | None = None,
    is_boss: bool = False,
    tier: int = 1,
) -> MonsterProfile:
    return MonsterProfile(
        code="SLIME",
        name="Slime",
        tier=tier,
        max_hp=60,
        attack_damage=attack_damage,
        damage_reduction_permille=0,
        enrage_after_rounds=enrage_after_rounds,
        enrage_multiplier_permille=enrage_multiplier_permille,
        is_boss=is_boss,
        art_code="SLIME",
        weak_topic_id=weak_topic_id,
    )


# --- the monster's blow ----------------------------------------------------


def test_a_calm_monster_hits_for_its_flat_damage() -> None:
    attack = monster_attack(monster=_monster(attack_damage=12), elapsed_seconds=0)

    assert attack.final_damage == 12
    assert attack.enraged is False


def test_rage_arrives_on_the_clock_not_on_the_answer_count() -> None:
    monster = _monster(attack_damage=12)

    assert is_enraged(ENRAGE_SECONDS - 1, monster) is False
    assert is_enraged(ENRAGE_SECONDS, monster) is True
    assert monster_attack(monster=monster, elapsed_seconds=ENRAGE_SECONDS).final_damage == 18


def test_stalling_no_longer_holds_the_monster_at_its_opening_strength() -> None:
    # The whole point of the rewrite: under the round-counted rule a player who
    # answered nothing kept `rounds_played` at zero and never met the enraged
    # monster at all.
    monster = _monster(attack_damage=12)

    assert is_enraged(ENRAGE_SECONDS + 30, monster) is True


def test_a_shield_soaks_part_of_the_blow() -> None:
    attack = monster_attack(
        monster=_monster(attack_damage=20),
        elapsed_seconds=0,
        defender_reduction_permille=500,
    )

    assert attack.raw_damage == 20
    assert attack.final_damage == 10


def test_a_total_shield_stops_the_blow_entirely() -> None:
    attack = monster_attack(
        monster=_monster(attack_damage=20),
        elapsed_seconds=0,
        # More reduction than exists is capped rather than turned into healing.
        defender_reduction_permille=4000,
    )

    assert attack.final_damage == 0


def test_the_snapshot_says_what_the_filling_cast_will_do() -> None:
    monster = _monster(attack_damage=12)

    calm = next_swing(monster=monster, elapsed_seconds=0)
    angry = next_swing(monster=monster, elapsed_seconds=ENRAGE_SECONDS)
    shielded = next_swing(monster=monster, elapsed_seconds=0, defender_reduction_permille=500)

    assert calm == (MonsterIntentKind.ATTACK, 12)
    assert angry == (MonsterIntentKind.ENRAGED_ATTACK, 18)
    # The warning is what will actually land, shield included.
    assert shielded == (MonsterIntentKind.ATTACK, 6)


# --- how often it swings ---------------------------------------------------


def test_the_cast_bar_gets_faster_as_tiers_climb() -> None:
    early = cast_interval_seconds(_monster(tier=1))
    late = cast_interval_seconds(_monster(tier=MAX_TIER))

    assert early == BASE_CAST_MS / 1000.0
    assert late < early


def test_a_boss_winds_up_faster_than_its_tier_alone_would() -> None:
    plain = cast_interval_seconds(_monster(tier=3))
    boss = cast_interval_seconds(_monster(tier=3, is_boss=True))

    assert boss == pytest.approx(plain * BOSS_CAST_RATIO)


def test_no_monster_swings_faster_than_the_floor() -> None:
    # A retune that drops the curve below the floor must not produce a monster
    # nobody can read a question against.
    hurried = cast_interval_seconds(_monster(tier=MAX_TIER, is_boss=True))

    assert hurried >= MIN_CAST_MS / 1000.0


# --- which monster guards which lesson -------------------------------------


def test_tiers_climb_with_the_learn_path() -> None:
    assert tier_for_unit(0) == 1
    assert tier_for_unit(UNITS_PER_TIER - 1) == 1
    assert tier_for_unit(UNITS_PER_TIER) == 2
    assert tier_for_unit(UNITS_PER_TIER * 3) == 4


def test_the_last_tier_holds_however_long_the_path_runs() -> None:
    # A path that grows past 48 units must not ask for a tier nothing seeds.
    assert tier_for_unit(UNITS_PER_TIER * 100) == MAX_TIER


def test_the_closing_lesson_of_a_unit_holds_the_boss() -> None:
    assert is_boss_lesson(7, last_order_index=7) is True
    assert is_boss_lesson(6, last_order_index=7) is False


# --- weakness --------------------------------------------------------------


def test_a_question_on_the_weak_topic_hits_harder() -> None:
    assert element_multiplier("grammar", "grammar") == WEAKNESS_MULTIPLIER
    assert element_multiplier("grammar", "reading") == 1.0


def test_a_monster_with_no_weakness_is_hit_normally() -> None:
    # v1 seeds every weakness as NULL, so this is the path every battle takes.
    assert element_multiplier(None, "grammar") == 1.0
    assert element_multiplier("grammar", None) == 1.0
