"""Achievements: the comparison, the catalog, and what syncing twice does.

The design claim being tested is that an achievement is a reading rather than
an event -- so the important properties are that every metric can actually be
read, that a threshold is crossed exactly once, and that reconciling a player
who is already up to date writes nothing and returns nothing.
"""

from datetime import UTC, datetime

from app.models.game.achievement import (
    Achievement,
    AchievementCategory,
    AchievementMetric,
    UserAchievement,
)
from app.services.game.achievement_service import AchievementService
from app.services.game.achievements import (
    _FIELD_BY_METRIC,
    AchievementMetrics,
    AchievementSpec,
    is_unlocked,
    reading_of,
    unlocked_codes,
)
from app.services.game.catalog import ACHIEVEMENTS


def _spec(
    code: str = "X",
    metric: AchievementMetric = AchievementMetric.LEVEL,
    threshold: int = 5,
) -> AchievementSpec:
    return AchievementSpec(
        code=code,
        name=code,
        description="",
        category=AchievementCategory.PROGRESSION,
        metric=metric,
        threshold=threshold,
    )


def _row(code: str, metric: AchievementMetric, threshold: int) -> Achievement:
    return Achievement(
        id=f"ach-{code}",
        code=code,
        name=code,
        description="",
        category=AchievementCategory.PROGRESSION,
        metric=metric,
        threshold=threshold,
        icon_code="",
        sort_order=0,
        is_hidden=False,
        is_active=True,
    )


# --- every metric can be read -----------------------------------------------


def test_every_metric_has_a_counter_behind_it() -> None:
    """A metric with no field would be an achievement nobody can ever unlock,
    and it would fail silently: the sync would simply never return it."""
    assert set(_FIELD_BY_METRIC) == set(AchievementMetric)

    metrics = AchievementMetrics()
    for metric in AchievementMetric:
        assert isinstance(reading_of(metrics, metric), int)


def test_a_brand_new_account_reads_as_the_bottom_of_every_scale() -> None:
    """The defaults stand in for rows created lazily. "Has not started" must
    not look like an error, and must not accidentally unlock anything."""
    metrics = AchievementMetrics()

    assert unlocked_codes(ACHIEVEMENTS, metrics) == set()


# --- crossing the line ------------------------------------------------------


def test_the_threshold_is_inclusive() -> None:
    spec = _spec(threshold=5)

    assert is_unlocked(spec, AchievementMetrics(level=4)) is False
    assert is_unlocked(spec, AchievementMetrics(level=5)) is True
    assert is_unlocked(spec, AchievementMetrics(level=6)) is True


def test_only_the_metric_named_is_read() -> None:
    """A level achievement must not be unlocked by a long streak."""
    spec = _spec(metric=AchievementMetric.LEVEL, threshold=50)

    assert is_unlocked(spec, AchievementMetrics(day_streak=999)) is False


def test_a_lapsed_streak_keeps_the_badge_that_measured_the_best_one() -> None:
    """`day_streak` falls when a day is missed and `best_day_streak` does not,
    which is the whole reason the catalog measures the month-long badge against
    the second one."""
    lapsed = AchievementMetrics(day_streak=0, best_day_streak=30)
    earned = unlocked_codes(ACHIEVEMENTS, lapsed)

    assert "STREAK_30" in earned
    assert "STREAK_7" not in earned


# --- the catalog itself -----------------------------------------------------


def test_the_catalog_has_no_duplicate_codes() -> None:
    codes = [spec.code for spec in ACHIEVEMENTS]

    assert len(codes) == len(set(codes))


def test_every_threshold_is_reachable() -> None:
    """A threshold of zero would unlock for a brand-new account, and a negative
    one is nonsense. Both are data mistakes a test is cheaper than a bug report."""
    assert all(spec.threshold > 0 for spec in ACHIEVEMENTS)


def test_the_tiers_of_one_metric_climb() -> None:
    """Three tiers per axis only works if they are actually ordered: a middle
    tier easier than the first would unlock out of sequence."""
    by_metric: dict[AchievementMetric, list[int]] = {}
    for spec in sorted(ACHIEVEMENTS, key=lambda item: item.sort_order):
        by_metric.setdefault(spec.metric, []).append(spec.threshold)

    for thresholds in by_metric.values():
        assert thresholds == sorted(thresholds)


# --- syncing ----------------------------------------------------------------


class FakeAchievementRepository:
    def __init__(self, catalog: list[Achievement], metrics: AchievementMetrics) -> None:
        self.catalog = catalog
        self._metrics = metrics
        self.held: list[tuple[UserAchievement, Achievement]] = []
        self.grants: list[list[str]] = []

    async def list_active(self) -> list[Achievement]:
        return self.catalog

    async def metrics(self, user_id: str) -> AchievementMetrics:
        return self._metrics

    async def unlocked(self, user_id: str) -> list[tuple[UserAchievement, Achievement]]:
        return self.held

    async def grant(self, user_id: str, achievement_ids: list[str]) -> list[str]:
        self.grants.append(list(achievement_ids))
        by_id = {row.id: row for row in self.catalog}
        fresh = [row_id for row_id in achievement_ids if row_id not in {
            held.achievement_id for held, _row in self.held
        }]
        for row_id in fresh:
            self.held.append(
                (
                    UserAchievement(
                        id=f"held-{row_id}",
                        user_id=user_id,
                        achievement_id=row_id,
                        unlocked_at=datetime.now(UTC),
                    ),
                    by_id[row_id],
                )
            )
        return fresh


def _service(metrics: AchievementMetrics) -> tuple[AchievementService, FakeAchievementRepository]:
    catalog = [
        _row("EARLY", AchievementMetric.LEVEL, 5),
        _row("LATE", AchievementMetric.LEVEL, 50),
    ]
    repository = FakeAchievementRepository(catalog, metrics)
    return AchievementService(repository), repository  # type: ignore[arg-type]


async def test_syncing_grants_what_is_earned_and_nothing_else() -> None:
    service, repository = _service(AchievementMetrics(level=10))

    granted = await service.sync("user-1")

    assert [row.code for row in granted] == ["EARLY"]
    assert repository.grants == [["ach-EARLY"]]


async def test_syncing_twice_grants_nothing_the_second_time() -> None:
    """The property that makes it safe to call this at the end of every match."""
    service, repository = _service(AchievementMetrics(level=10))

    await service.sync("user-1")
    again = await service.sync("user-1")

    assert again == []
    # Not merely "granted nothing" -- it did not attempt a write at all.
    assert len(repository.grants) == 1


async def test_a_later_sync_picks_up_what_a_higher_reading_unlocked() -> None:
    service, repository = _service(AchievementMetrics(level=10))
    await service.sync("user-1")

    repository._metrics = AchievementMetrics(level=60)
    granted = await service.sync("user-1")

    assert [row.code for row in granted] == ["LATE"]


async def test_an_empty_catalog_is_not_an_error() -> None:
    """An install that has not seeded yet still has to be able to settle a
    match, so this returns rather than reaching for metrics it cannot use."""
    repository = FakeAchievementRepository([], AchievementMetrics(level=99))
    service = AchievementService(repository)  # type: ignore[arg-type]

    assert await service.sync("user-1") == []
    assert repository.grants == []
