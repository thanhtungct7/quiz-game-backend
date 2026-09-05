from app.models.duo.duo_match import STARTING_HP
from app.services.game.combat import (
    COMBO_TIER_1,
    COMBO_TIER_2,
    MAX_DAMAGE,
    MAX_HP,
    MAX_MANA,
    QUICK_BONUS,
    Blow,
    StrikeKind,
    apply_damage,
    award_damage,
    award_mana,
    classify_strike,
    combo_after,
    combo_multiplier,
    gain_mana,
    resolve_blow,
)

LIMIT = 20  # seconds


def test_a_wrong_answer_deals_nothing() -> None:
    assert award_damage(False, 0, LIMIT) == 0
    assert award_damage(False, 5_000, LIMIT) == 0


def test_damage_runs_from_half_to_full_with_speed() -> None:
    assert award_damage(True, 0, LIMIT) == MAX_DAMAGE
    assert award_damage(True, LIMIT * 1000, LIMIT) == MAX_DAMAGE // 2
    mid = award_damage(True, LIMIT * 500, LIMIT)
    assert MAX_DAMAGE // 2 < mid < MAX_DAMAGE


def test_damage_never_exceeds_the_cap_when_answering_past_the_deadline() -> None:
    assert award_damage(True, LIMIT * 5000, LIMIT) == MAX_DAMAGE // 2


def test_a_zero_length_round_still_resolves() -> None:
    assert award_damage(True, 0, 0) == MAX_DAMAGE
    assert classify_strike(0, 0) is StrikeKind.QUICK


def test_the_two_playstyles_are_classified_by_the_clock() -> None:
    assert classify_strike(500, LIMIT) is StrikeKind.QUICK
    assert classify_strike(3_000, LIMIT) is StrikeKind.QUICK
    assert classify_strike(6_000, LIMIT) is StrikeKind.NORMAL
    assert classify_strike(12_000, LIMIT) is StrikeKind.HEAVY
    assert classify_strike(19_000, LIMIT) is StrikeKind.HEAVY


def test_a_quick_strike_adds_flat_damage() -> None:
    quick = resolve_blow(
        is_correct=True, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=1
    )
    assert quick.strike is StrikeKind.QUICK
    assert quick.final_damage == MAX_DAMAGE + QUICK_BONUS


def test_a_heavy_strike_pierces_half_the_defence() -> None:
    # 50% reduction: a normal blow loses half, a heavy one loses a quarter.
    normal = resolve_blow(
        is_correct=True,
        elapsed_ms=6_000,
        time_limit_seconds=LIMIT,
        combo_count=1,
        defender_reduction_permille=500,
    )
    heavy = resolve_blow(
        is_correct=True,
        elapsed_ms=14_000,
        time_limit_seconds=LIMIT,
        combo_count=1,
        defender_reduction_permille=500,
    )
    assert normal.strike is StrikeKind.NORMAL
    assert heavy.strike is StrikeKind.HEAVY
    # The heavy blow starts from less raw damage (it was slower) yet gets
    # through for more, which is the entire point of the archetype.
    assert heavy.raw_damage < normal.raw_damage
    assert heavy.final_damage > normal.final_damage


def test_piercing_is_worth_nothing_against_an_undefended_opponent() -> None:
    heavy = resolve_blow(
        is_correct=True, elapsed_ms=14_000, time_limit_seconds=LIMIT, combo_count=1
    )
    normal = resolve_blow(
        is_correct=True, elapsed_ms=6_000, time_limit_seconds=LIMIT, combo_count=1
    )
    assert heavy.final_damage < normal.final_damage


def test_a_combo_builds_and_any_miss_resets_it() -> None:
    combo = 0
    for expected in (1, 2, 3, 4):
        combo = combo_after(combo, True)
        assert combo == expected
    assert combo_after(combo, False) == 0
    assert combo_after(0, False) == 0


def test_the_combo_tiers_are_where_they_are_documented() -> None:
    assert combo_multiplier(1) == (1.0, False, False)
    assert combo_multiplier(COMBO_TIER_1 - 1) == (1.0, False, False)
    assert combo_multiplier(COMBO_TIER_1) == (1.5, False, False)
    assert combo_multiplier(COMBO_TIER_2 - 1) == (1.5, False, False)
    assert combo_multiplier(COMBO_TIER_2) == (2.0, True, True)
    assert combo_multiplier(COMBO_TIER_2 + 3) == (2.0, True, True)


def test_a_third_correct_answer_hits_harder_than_a_second() -> None:
    second = resolve_blow(
        is_correct=True, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=2
    )
    third = resolve_blow(
        is_correct=True, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=3
    )
    assert third.final_damage == round(second.final_damage * 1.5)
    assert third.is_critical is False
    assert third.stuns_opponent is False


def test_a_fifth_correct_answer_crits_and_stuns() -> None:
    blow = resolve_blow(
        is_correct=True, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=COMBO_TIER_2
    )
    assert blow.is_critical is True
    assert blow.stuns_opponent is True
    assert blow.combo_multiplier == 2.0


def test_a_critical_still_loses_damage_to_a_shield() -> None:
    bare = resolve_blow(
        is_correct=True,
        elapsed_ms=0,
        time_limit_seconds=LIMIT,
        combo_count=COMBO_TIER_2,
    )
    shielded = resolve_blow(
        is_correct=True,
        elapsed_ms=0,
        time_limit_seconds=LIMIT,
        combo_count=COMBO_TIER_2,
        defender_reduction_permille=500,
    )
    assert shielded.final_damage == round(bare.final_damage * 0.5)
    assert shielded.is_critical is True


def test_a_wrong_answer_resolves_to_an_empty_blow() -> None:
    blow = resolve_blow(
        is_correct=False, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=0
    )
    assert blow == Blow.none()
    assert blow.final_damage == 0
    assert blow.stuns_opponent is False


def test_total_immunity_lets_nothing_through() -> None:
    blow = resolve_blow(
        is_correct=True,
        elapsed_ms=0,
        time_limit_seconds=LIMIT,
        combo_count=COMBO_TIER_2,
        defender_reduction_permille=1000,
    )
    assert blow.final_damage == 0


def test_reduction_beyond_total_does_not_heal_the_defender() -> None:
    blow = resolve_blow(
        is_correct=True,
        elapsed_ms=0,
        time_limit_seconds=LIMIT,
        combo_count=1,
        defender_reduction_permille=5000,
    )
    assert blow.final_damage == 0


def test_class_and_element_multipliers_stack_onto_the_blow() -> None:
    plain = resolve_blow(
        is_correct=True, elapsed_ms=0, time_limit_seconds=LIMIT, combo_count=1
    )
    boosted = resolve_blow(
        is_correct=True,
        elapsed_ms=0,
        time_limit_seconds=LIMIT,
        combo_count=1,
        attacker_damage_permille=1250,
        element_multiplier=1.5,
    )
    assert boosted.final_damage == round(plain.final_damage * 1.25 * 1.5)


def test_health_is_floored_at_zero() -> None:
    assert apply_damage(100, 30) == 70
    assert apply_damage(10, 30) == 0
    assert apply_damage(0, 30) == 0
    # Negative damage must not be a heal.
    assert apply_damage(50, -30) == 50


def test_mana_rewards_speed_and_still_pays_for_a_miss() -> None:
    assert award_mana(True, 0, LIMIT) == 35
    assert award_mana(True, LIMIT * 1000, LIMIT) == 20
    assert award_mana(False, 0, LIMIT) == 5


def test_mana_is_capped_and_never_negative() -> None:
    assert gain_mana(MAX_MANA - 5, 35) == MAX_MANA
    assert gain_mana(0, 35) == 35
    assert gain_mana(3, -10) == 0


def test_the_database_starting_health_matches_the_combat_rule() -> None:
    # duo_matches.player_*_hp_left defaults to STARTING_HP; a match that starts
    # at one number and is stored against another would misreport every result.
    assert STARTING_HP == MAX_HP


def test_a_streak_buys_a_small_head_start() -> None:
    from app.services.game.combat import STREAK_HP_CAP, STREAK_MANA_CAP, streak_buff

    assert streak_buff(0) == streak_buff(0)
    assert streak_buff(0).bonus_max_hp == 0
    assert streak_buff(0).bonus_starting_mana == 0
    assert streak_buff(5).bonus_max_hp == 5
    assert streak_buff(5).bonus_starting_mana == 1
    # Capped low: the buff rewards the habit, it does not decide the match.
    assert streak_buff(500).bonus_max_hp == STREAK_HP_CAP
    assert streak_buff(500).bonus_starting_mana == STREAK_MANA_CAP
    assert STREAK_HP_CAP <= MAX_HP // 5


def test_a_negative_streak_is_treated_as_none() -> None:
    from app.services.game.combat import streak_buff

    assert streak_buff(-5).bonus_max_hp == 0
