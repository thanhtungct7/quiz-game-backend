"""Deciding which achievements a player has earned.

Pure functions with no I/O, in the same shape as `combat.py` and `loot.py`. The
data lives in `catalog.py` beside the classes, items and monsters, and the rows
recording who unlocked what live in `models/game/achievement.py`; this module is
only the comparison between the two.

The comparison is the whole design. Nothing here observes an event, so nothing
has to be running at the moment an achievement is earned -- a player who was
already past a threshold before it was written unlocks it the next time
anything syncs. See the module docstring on `Achievement` for what that rules
out as well as what it buys.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.models.game.achievement import AchievementCategory, AchievementMetric


class Threshold(Protocol):
    """What evaluating an achievement actually needs.

    A protocol rather than a concrete type because the same comparison runs
    against two things: the seed specs in `catalog.py` when reasoning about the
    catalog, and the rows loaded from the database when syncing a player. They
    carry the same three fields and there is no reason to convert between them
    to find that out.
    """

    @property
    def code(self) -> str: ...

    @property
    def metric(self) -> AchievementMetric: ...

    @property
    def threshold(self) -> int: ...


@dataclass(frozen=True)
class AchievementSpec:
    """One catalog entry. Balance data, exactly like `ClassSpec`."""

    code: str
    name: str
    description: str
    category: AchievementCategory
    metric: AchievementMetric
    threshold: int
    icon_code: str = ""
    sort_order: int = 0
    is_hidden: bool = False


@dataclass(frozen=True)
class AchievementMetrics:
    """Every counter an achievement can be measured against, read at one moment.

    Gathered in one place rather than passed around piecemeal so that adding a
    metric is a field here and a row in the catalog, and so a sync cannot end up
    comparing half of one player against half of another.

    The defaults are what a brand-new account reads as, which is also what a
    player with no game profile row reads as -- those rows are created lazily,
    and "has not started yet" must not look like an error.
    """

    level: int = 1
    day_streak: int = 0
    best_day_streak: int = 0
    challenges_mastered: int = 0
    total_attempts: int = 0
    lessons_completed: int = 0
    pvp_wins: int = 0
    pvp_rating: int = 0
    pvp_best_streak: int = 0
    battles_won: int = 0


# Exhaustive on purpose, and held that way by a test: a metric with no field
# behind it would be an achievement nobody can ever unlock, and it would fail
# silently -- the sync would simply never return it.
_FIELD_BY_METRIC: dict[AchievementMetric, str] = {
    AchievementMetric.LEVEL: "level",
    AchievementMetric.DAY_STREAK: "day_streak",
    AchievementMetric.BEST_DAY_STREAK: "best_day_streak",
    AchievementMetric.CHALLENGES_MASTERED: "challenges_mastered",
    AchievementMetric.TOTAL_ATTEMPTS: "total_attempts",
    AchievementMetric.LESSONS_COMPLETED: "lessons_completed",
    AchievementMetric.PVP_WINS: "pvp_wins",
    AchievementMetric.PVP_RATING: "pvp_rating",
    AchievementMetric.PVP_BEST_STREAK: "pvp_best_streak",
    AchievementMetric.BATTLES_WON: "battles_won",
}


def reading_of(metrics: AchievementMetrics, metric: AchievementMetric) -> int:
    """What this player currently scores on that counter."""
    return int(getattr(metrics, _FIELD_BY_METRIC[metric]))


def is_unlocked(spec: Threshold, metrics: AchievementMetrics) -> bool:
    """Whether the threshold has been reached. Never un-reached: `day_streak`
    can fall, so anything that should survive a lapse is measured against
    `best_day_streak` instead. Unlocking is one-way at the storage layer too --
    a row, once written, is not deleted when a counter drops."""
    return reading_of(metrics, spec.metric) >= spec.threshold


def unlocked_codes(specs: Iterable[Threshold], metrics: AchievementMetrics) -> set[str]:
    """Every achievement this player has earned, as of these readings."""
    return {spec.code for spec in specs if is_unlocked(spec, metrics)}
