"""CEFR bands and the TOEIC estimate that goes with a level.

Pure functions with no I/O, in the same shape as `season.py`. A band is always
derived from a level rather than stored, so the two can never disagree -- the
same rule `tier_for_rating` follows for the PvP ladder.
"""

from enum import StrEnum

# Where the band ladder stops. Deliberately not imported into `leveling.py`,
# which has no ceiling of its own: experience keeps accruing past 100 and only
# the *band* stops climbing, so a long-lived account never hits a wall.
MAX_CEFR_LEVEL = 100

# TOEIC L&R's own scale, not a game number.
TOEIC_FLOOR = 10
TOEIC_CEILING = 990


class CefrBand(StrEnum):
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


# Ordered high to low, so the first match wins -- `TIER_FLOORS` again.
#
# The bands are not equal widths. A1 is a handful of lessons and C2 is whatever
# is left at the top, while B1 and B2 are twenty-five levels each because that
# is where a learner actually spends the year. Splitting 100 evenly would make
# the middle of the path fly past and the top drag.
BAND_FLOORS: tuple[tuple[CefrBand, int], ...] = (
    (CefrBand.C2, 93),
    (CefrBand.C1, 76),
    (CefrBand.B2, 51),
    (CefrBand.B1, 26),
    (CefrBand.A2, 11),
    (CefrBand.A1, 1),
)

# The score a band opens on, anchored to the published TOEIC L&R <-> CEFR
# floors rather than invented: A2 starts at 225, B1 at 550, B2 at 785, C1 at
# 945. The levels here must be the ones in BAND_FLOORS; tests hold them to it.
#
# The estimate flattens at 990 through C2 on purpose. TOEIC L&R does not
# measure past C1, so the level keeps climbing and the score cannot follow.
TOEIC_ANCHORS: tuple[tuple[int, int], ...] = (
    (1, TOEIC_FLOOR),
    (11, 225),
    (26, 550),
    (51, 785),
    (76, 945),
    (93, TOEIC_CEILING),
)


def cefr_for_level(level: int) -> CefrBand:
    """The band a player of this level has reached."""
    for band, floor in BAND_FLOORS:
        if level >= floor:
            return band
    return CefrBand.A1


def cefr_floor(band: CefrBand) -> int:
    """The first level that counts as this band."""
    return next(floor for candidate, floor in BAND_FLOORS if candidate is band)


def next_cefr_at_level(level: int) -> tuple[CefrBand, int] | None:
    """The band above this one and the level it opens at, or None at the top."""
    current = cefr_floor(cefr_for_level(level))
    higher = [(band, floor) for band, floor in BAND_FLOORS if floor > current]
    if not higher:
        return None
    return min(higher, key=lambda entry: entry[1])


def toeic_estimate_for_level(level: int) -> int:
    """A TOEIC L&R score this level roughly corresponds to.

    An estimate for the player to read, never a claim about a real sitting.
    Interpolated straight between the anchors so it climbs with every level
    instead of jumping once per band.
    """
    capped = max(1, min(level, MAX_CEFR_LEVEL))
    for (start_level, start_score), (end_level, end_score) in zip(
        TOEIC_ANCHORS, TOEIC_ANCHORS[1:], strict=False
    ):
        if capped < end_level:
            climbed = capped - start_level
            span = end_level - start_level
            return start_score + (end_score - start_score) * climbed // span
    return TOEIC_CEILING
