"""The PvE REST endpoints, and the lesson-to-monster mapping behind them."""

from datetime import UTC, datetime
from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.pve.battle import (
    list_course_monsters,
    list_monsters,
    list_my_battles,
    preview_lesson,
)
from app.core.exceptions import CourseNotFoundError, LessonNotFoundError
from app.models.auth.user import User
from app.models.content.course import Course
from app.models.content.lesson import Lesson
from app.models.content.unit import Unit
from app.models.game.monster import Monster
from app.models.pve.lesson_battle import BattleStatus, LessonBattle
from app.schemas.pve.battle import (
    BattleHistoryEntry,
    CourseMonsterRead,
    CourseMonstersRead,
    MonsterCatalogRead,
    MonsterPreview,
)
from app.services.pve.battle_service import BattleService

COURSE = "course-1"
LESSONS_PER_UNIT = 8


def _user() -> User:
    return User(id="user-1", email="user@example.com")


def _monster(code: str, tier: int, *, is_boss: bool = False) -> Monster:
    return Monster(
        id=f"monster-{code}",
        code=code,
        name=code.title(),
        description="",
        tier=tier,
        max_hp=60 + 20 * tier,
        attack_damage=8 + 2 * tier,
        damage_reduction_permille=0,
        enrage_after_rounds=5,
        enrage_multiplier_permille=1500,
        is_boss=is_boss,
        weak_topic_id=None,
        art_code=code,
        sort_order=tier,
        is_active=True,
    )


CATALOG = [
    _monster("SLIME", 1),
    _monster("SLIME_KING", 1, is_boss=True),
    _monster("GOBLIN", 2),
    _monster("GOBLIN_CHIEF", 2, is_boss=True),
]


def _unit(index: int) -> Unit:
    return Unit(
        id=f"unit-{index}",
        title=f"Unit {index}",
        description="",
        course_id=COURSE,
        order_index=index,
    )


def _lesson(unit_index: int, order_index: int) -> Lesson:
    return Lesson(
        id=f"lesson-{unit_index}-{order_index}",
        title=f"Lesson {order_index}",
        unit_id=f"unit-{unit_index}",
        order_index=order_index,
        is_bank=False,
    )


class FakeMonsterRepository:
    def __init__(self, catalog: list[Monster] | None = None) -> None:
        self.catalog = CATALOG if catalog is None else catalog

    async def list_monsters(self, *, include_inactive: bool = False) -> list[Monster]:
        return self.catalog

    async def get_by_code(self, code: str) -> Monster | None:
        return next((row for row in self.catalog if row.code == code), None)


class FakeLessonRepository:
    def __init__(self, lessons: list[Lesson]) -> None:
        self.lessons = lessons

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return next((row for row in self.lessons if row.id == lesson_id), None)

    async def list_by_unit(
        self, unit_id: str, *, include_bank: bool = False
    ) -> list[Lesson]:
        return [row for row in self.lessons if row.unit_id == unit_id]

    async def list_path_by_course(self, course_id: str) -> list[Lesson]:
        return list(self.lessons)


class FakeUnitRepository:
    def __init__(self, units: list[Unit]) -> None:
        self.units = units

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return next((row for row in self.units if row.id == unit_id), None)

    async def list_by_course(self, course_id: str) -> list[Unit]:
        return list(self.units)


class FakeCourseRepository:
    def __init__(self, course: Course | None) -> None:
        self.course = course

    async def get_by_id(self, course_id: str) -> Course | None:
        return self.course


class FakeBattleRepository:
    def __init__(
        self, *, cleared: set[str] | None = None, history: list[LessonBattle] | None = None
    ) -> None:
        self.cleared = cleared or set()
        self.history = history or []

    async def has_won(self, user_id: str, lesson_id: str) -> bool:
        return lesson_id in self.cleared

    async def cleared_lessons(self, user_id: str, lesson_ids: list[str]) -> set[str]:
        return {lesson_id for lesson_id in lesson_ids if lesson_id in self.cleared}

    async def best_result(self, user_id: str, lesson_id: str) -> LessonBattle | None:
        return next(
            (row for row in self.history if row.lesson_id == lesson_id), None
        )

    async def list_for_user(
        self, user_id: str, limit: int, offset: int
    ) -> list[LessonBattle]:
        return self.history[offset : offset + limit]


def _service(
    *,
    lessons: list[Lesson] | None = None,
    units: list[Unit] | None = None,
    course: Course | None = None,
    cleared: set[str] | None = None,
    history: list[LessonBattle] | None = None,
    catalog: list[Monster] | None = None,
) -> BattleService:
    return BattleService(
        monsters=FakeMonsterRepository(catalog),  # type: ignore[arg-type]
        lessons=FakeLessonRepository(lessons or []),  # type: ignore[arg-type]
        units=FakeUnitRepository(units or []),  # type: ignore[arg-type]
        courses=FakeCourseRepository(  # type: ignore[arg-type]
            course if course is not None else Course(id=COURSE, title="TOEIC")
        ),
        battles=FakeBattleRepository(cleared=cleared, history=history),  # type: ignore[arg-type]
    )


class FailingBattleService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def preview_lesson(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def course_monsters(self, *_: object, **__: object) -> NoReturn:
        raise self.error


# --- catalog ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_catalog_lists_every_active_monster() -> None:
    result = await list_monsters(current_user=_user(), service=_service())

    assert [row.code for row in result] == [
        "SLIME",
        "SLIME_KING",
        "GOBLIN",
        "GOBLIN_CHIEF",
    ]
    assert isinstance(result[0], MonsterCatalogRead)


# --- one lesson ------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_lesson_preview_names_its_monster() -> None:
    service = _service(lessons=[_lesson(0, 0), _lesson(0, 1)], units=[_unit(0)])

    result = await preview_lesson(
        lesson_id="lesson-0-0", current_user=_user(), service=service
    )

    assert isinstance(result, MonsterPreview)
    assert result.monster.code == "SLIME"
    assert result.cleared is False
    assert result.best_hp_left is None


@pytest.mark.asyncio
async def test_the_closing_lesson_of_a_unit_previews_the_boss() -> None:
    service = _service(lessons=[_lesson(0, 0), _lesson(0, 1)], units=[_unit(0)])

    result = await preview_lesson(
        lesson_id="lesson-0-1", current_user=_user(), service=service
    )

    assert result.monster.code == "SLIME_KING"
    assert result.monster.is_boss is True


@pytest.mark.asyncio
async def test_a_later_unit_faces_a_higher_tier() -> None:
    service = _service(
        lessons=[_lesson(LESSONS_PER_UNIT, 0), _lesson(LESSONS_PER_UNIT, 1)],
        units=[_unit(LESSONS_PER_UNIT)],
    )

    result = await preview_lesson(
        lesson_id=f"lesson-{LESSONS_PER_UNIT}-0", current_user=_user(), service=service
    )

    assert result.monster.code == "GOBLIN"


@pytest.mark.asyncio
async def test_a_preview_reports_a_lesson_already_cleared() -> None:
    battle = LessonBattle(
        id="battle-1",
        user_id="user-1",
        lesson_id="lesson-0-0",
        monster_code="SLIME",
        status=BattleStatus.WON,
        player_hp_left=64,
        monster_hp_left=0,
        rounds_played=5,
        correct_count=5,
        best_combo=5,
        exp_awarded=35,
        gold_awarded=15,
        first_clear=True,
        started_at=datetime.now(UTC),
    )
    service = _service(
        lessons=[_lesson(0, 0)],
        units=[_unit(0)],
        cleared={"lesson-0-0"},
        history=[battle],
    )

    result = await preview_lesson(
        lesson_id="lesson-0-0", current_user=_user(), service=service
    )

    assert result.cleared is True
    assert result.best_hp_left == 64


@pytest.mark.asyncio
async def test_previewing_a_missing_lesson_is_a_404() -> None:
    with pytest.raises(HTTPException) as error:
        await preview_lesson(
            lesson_id="nope",
            current_user=_user(),
            service=FailingBattleService(LessonNotFoundError("nope")),  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_previewing_a_bank_lesson_is_a_404() -> None:
    """A bank lesson is storage for leftover questions, not a gate on the path."""
    bank = _lesson(0, 99)
    bank.is_bank = True
    service = _service(lessons=[bank], units=[_unit(0)])

    with pytest.raises(HTTPException) as error:
        await preview_lesson(
            lesson_id=bank.id, current_user=_user(), service=service
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


# --- a whole course --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_course_map_comes_back_in_one_request() -> None:
    lessons = [_lesson(0, index) for index in range(3)] + [
        _lesson(LESSONS_PER_UNIT, index) for index in range(2)
    ]
    service = _service(
        lessons=lessons,
        units=[_unit(0), _unit(LESSONS_PER_UNIT)],
        cleared={"lesson-0-0"},
    )

    result = await list_course_monsters(
        course_id=COURSE, current_user=_user(), service=service
    )

    assert isinstance(result, CourseMonstersRead)
    assert [row.monster_code for row in result.lessons] == [
        "SLIME",
        "SLIME",
        "SLIME_KING",
        "GOBLIN",
        "GOBLIN_CHIEF",
    ]
    assert [row.cleared for row in result.lessons] == [True, False, False, False, False]
    assert isinstance(result.lessons[0], CourseMonsterRead)


@pytest.mark.asyncio
async def test_a_missing_course_is_a_404() -> None:
    with pytest.raises(HTTPException) as error:
        await list_course_monsters(
            course_id="nope",
            current_user=_user(),
            service=FailingBattleService(CourseNotFoundError("nope")),  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


# --- history ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_returns_finished_battles() -> None:
    battle = LessonBattle(
        id="battle-1",
        user_id="user-1",
        lesson_id="lesson-0-0",
        monster_code="SLIME",
        status=BattleStatus.WON,
        end_reason=None,
        player_hp_left=64,
        monster_hp_left=0,
        rounds_played=5,
        correct_count=5,
        best_combo=5,
        exp_awarded=35,
        gold_awarded=15,
        first_clear=True,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )

    result = await list_my_battles(
        current_user=_user(),
        service=_service(history=[battle]),
        limit=20,
        offset=0,
    )

    assert [row.id for row in result] == ["battle-1"]
    assert isinstance(result[0], BattleHistoryEntry)
    assert result[0].first_clear is True
