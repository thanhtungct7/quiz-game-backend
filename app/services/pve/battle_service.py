"""The read-only side of PvE: what the app draws around a fight.

Which monsters exist, which one guards a lesson, which ones sit on a course
map, and what the player has fought lately. The fight itself never comes
through here -- it lives on the WebSocket.
"""

from app.core.exceptions import (
    CourseNotFoundError,
    LessonNotFoundError,
    MonsterUnavailableError,
)
from app.models.content.lesson import Lesson
from app.repository.content.course_repository import CourseRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.game.monster_repository import MonsterRepository
from app.repository.pve.lesson_battle_repository import LessonBattleRepository
from app.schemas.pve.battle import (
    BattleHistoryEntry,
    CourseMonsterRead,
    CourseMonstersRead,
    MonsterCatalogRead,
    MonsterPreview,
)
from app.services.pve.catalog import resolve_for_lesson


class BattleService:
    def __init__(
        self,
        *,
        monsters: MonsterRepository,
        lessons: LessonRepository,
        units: UnitRepository,
        courses: CourseRepository,
        battles: LessonBattleRepository,
    ) -> None:
        self.monsters = monsters
        self.lessons = lessons
        self.units = units
        self.courses = courses
        self.battles = battles

    async def list_monsters(self) -> list[MonsterCatalogRead]:
        return [
            MonsterCatalogRead.model_validate(row) for row in await self.monsters.list_monsters()
        ]

    async def preview_lesson(self, user_id: str, lesson_id: str) -> MonsterPreview:
        """Who guards this gate, and how this player has fared against them."""
        lesson = await self.lessons.get_by_id(lesson_id)
        if lesson is None or lesson.is_bank:
            raise LessonNotFoundError(lesson_id)

        unit = await self.units.get_by_id(lesson.unit_id)
        if unit is None:
            raise LessonNotFoundError(lesson_id)

        siblings = await self.lessons.list_by_unit(lesson.unit_id)
        profile = resolve_for_lesson(
            await self.monsters.list_monsters(),
            unit_order_index=unit.order_index,
            lesson_order_index=lesson.order_index,
            last_order_index=_last_order_index(siblings, lesson),
        )
        if profile is None:
            raise MonsterUnavailableError(lesson_id)

        row = await self.monsters.get_by_code(profile.code)
        last = await self.battles.best_result(user_id, lesson_id)
        return MonsterPreview(
            lesson_id=lesson_id,
            lesson_title=lesson.title,
            monster=MonsterCatalogRead.model_validate(row),
            cleared=await self.battles.has_won(user_id, lesson_id),
            best_hp_left=last.player_hp_left if last is not None else None,
            last_played_at=last.started_at if last is not None else None,
        )

    async def course_monsters(self, user_id: str, course_id: str) -> CourseMonstersRead:
        """Every gate of one course in a single request.

        The map draws hundreds of gates at once, so asking per lesson would be
        one request per gate on every launch.
        """
        course = await self.courses.get_by_id(course_id)
        if course is None:
            raise CourseNotFoundError(course_id)

        lessons = await self.lessons.list_path_by_course(course_id)
        units = {unit.id: unit for unit in await self.units.list_by_course(course_id)}
        catalog = await self.monsters.list_monsters()
        cleared = await self.battles.cleared_lessons(user_id, [lesson.id for lesson in lessons])
        last_order = _last_order_by_unit(lessons)

        entries: list[CourseMonsterRead] = []
        for lesson in lessons:
            unit = units.get(lesson.unit_id)
            if unit is None:
                continue
            profile = resolve_for_lesson(
                catalog,
                unit_order_index=unit.order_index,
                lesson_order_index=lesson.order_index,
                last_order_index=last_order[lesson.unit_id],
            )
            if profile is None:
                continue
            entries.append(
                CourseMonsterRead(
                    unit_id=lesson.unit_id,
                    lesson_id=lesson.id,
                    monster_code=profile.code,
                    is_boss=profile.is_boss,
                    art_code=profile.art_code,
                    cleared=lesson.id in cleared,
                )
            )
        return CourseMonstersRead(course_id=course_id, lessons=entries)

    async def history(self, user_id: str, limit: int, offset: int) -> list[BattleHistoryEntry]:
        return [
            BattleHistoryEntry.model_validate(row)
            for row in await self.battles.list_for_user(user_id, limit, offset)
        ]


def _last_order_index(siblings: list[Lesson], lesson: Lesson) -> int:
    return max((row.order_index for row in siblings), default=lesson.order_index)


def _last_order_by_unit(lessons: list[Lesson]) -> dict[str, int]:
    """The closing lesson of each unit, which is where its boss stands."""
    last: dict[str, int] = {}
    for lesson in lessons:
        current = last.get(lesson.unit_id)
        if current is None or lesson.order_index > current:
            last[lesson.unit_id] = lesson.order_index
    return last
