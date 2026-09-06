"""The CEFR band ladder and the TOEIC estimate hung off it."""

from app.services.game.cefr import (
    BAND_FLOORS,
    MAX_CEFR_LEVEL,
    TOEIC_ANCHORS,
    TOEIC_CEILING,
    TOEIC_FLOOR,
    CefrBand,
    cefr_floor,
    cefr_for_level,
    next_cefr_at_level,
    toeic_estimate_for_level,
)

# --- the band table --------------------------------------------------------


def test_bands_are_ordered_high_to_low() -> None:
    floors = [floor for _band, floor in BAND_FLOORS]
    assert floors == sorted(floors, reverse=True)


def test_every_band_appears_exactly_once() -> None:
    assert {band for band, _floor in BAND_FLOORS} == set(CefrBand)


def test_the_ladder_starts_at_level_one() -> None:
    # Nothing may fall off the bottom: level 1 has to land somewhere.
    assert min(floor for _band, floor in BAND_FLOORS) == 1


def test_toeic_anchors_line_up_with_the_band_floors() -> None:
    assert {level for level, _score in TOEIC_ANCHORS} == {
        floor for _band, floor in BAND_FLOORS
    }


# --- bands -----------------------------------------------------------------


def test_a_new_account_is_a1() -> None:
    assert cefr_for_level(1) is CefrBand.A1


def test_a_band_starts_exactly_on_its_floor() -> None:
    for band, floor in BAND_FLOORS:
        assert cefr_for_level(floor) is band
        # And the level below belongs to somebody else.
        if floor > 1:
            assert cefr_for_level(floor - 1) is not band


def test_bands_never_go_backwards() -> None:
    seen = [cefr_for_level(level) for level in range(1, MAX_CEFR_LEVEL + 1)]
    ranks = [list(CefrBand).index(band) for band in seen]
    assert ranks == sorted(ranks)


def test_a_level_past_the_ceiling_stays_at_the_top_band() -> None:
    assert cefr_for_level(MAX_CEFR_LEVEL) is CefrBand.C2
    assert cefr_for_level(9999) is CefrBand.C2


def test_a_level_below_one_does_not_fall_through() -> None:
    assert cefr_for_level(0) is CefrBand.A1
    assert cefr_for_level(-5) is CefrBand.A1


def test_cefr_floor_round_trips() -> None:
    for band, floor in BAND_FLOORS:
        assert cefr_floor(band) == floor


# --- the next band ---------------------------------------------------------


def test_the_next_band_is_the_one_immediately_above() -> None:
    assert next_cefr_at_level(1) == (CefrBand.A2, cefr_floor(CefrBand.A2))
    assert next_cefr_at_level(cefr_floor(CefrBand.B1)) == (
        CefrBand.B2,
        cefr_floor(CefrBand.B2),
    )


def test_the_top_band_has_nothing_above_it() -> None:
    assert next_cefr_at_level(cefr_floor(CefrBand.C2)) is None
    assert next_cefr_at_level(MAX_CEFR_LEVEL) is None


# --- the TOEIC estimate ----------------------------------------------------


def test_the_estimate_starts_and_ends_on_the_real_scale() -> None:
    assert toeic_estimate_for_level(1) == TOEIC_FLOOR
    assert toeic_estimate_for_level(MAX_CEFR_LEVEL) == TOEIC_CEILING


def test_the_estimate_never_leaves_the_scale() -> None:
    for level in range(-5, MAX_CEFR_LEVEL + 20):
        assert TOEIC_FLOOR <= toeic_estimate_for_level(level) <= TOEIC_CEILING


def test_the_estimate_never_goes_backwards() -> None:
    scores = [toeic_estimate_for_level(level) for level in range(1, MAX_CEFR_LEVEL + 1)]
    assert scores == sorted(scores)


def test_a_band_floor_scores_its_published_toeic_floor() -> None:
    for level, score in TOEIC_ANCHORS:
        assert toeic_estimate_for_level(level) == score


def test_the_estimate_ceilings_before_the_level_does() -> None:
    """TOEIC L&R does not measure past C1, so the last band is flat."""
    assert toeic_estimate_for_level(cefr_floor(CefrBand.C2)) == TOEIC_CEILING
    assert toeic_estimate_for_level(MAX_CEFR_LEVEL) == TOEIC_CEILING
    assert toeic_estimate_for_level(9999) == TOEIC_CEILING
