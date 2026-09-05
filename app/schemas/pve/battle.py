"""REST payloads for the PvE screens.

These are the read-only views the app needs *around* a battle: what monsters
exist, which one guards a lesson, which ones sit on a course map, and what the
player has fought lately. The fight itself is the WebSocket's job.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.pve.lesson_battle import BattleEndReason, BattleStatus


class MonsterCatalogRead(BaseModel):
    """One monster as the app draws it outside a fight."""

    code: str
    name: str
    description: str
    tier: int
    max_hp: int
    attack_damage: int
    is_boss: bool
    art_code: str

    model_config = {"from_attributes": True}


class MonsterPreview(BaseModel):
    """The monster guarding one lesson, and how the player has fared against it."""

    lesson_id: str
    lesson_title: str
    monster: MonsterCatalogRead
    cleared: bool
    best_hp_left: int | None
    last_played_at: datetime | None


class CourseMonsterRead(BaseModel):
    """One lesson's monster on the course map."""

    unit_id: str
    lesson_id: str
    monster_code: str
    is_boss: bool
    art_code: str
    cleared: bool


class CourseMonstersRead(BaseModel):
    """Every lesson's monster for a whole course, in one request.

    The map draws hundreds of gates at once; asking per lesson would be one
    request per gate on every launch.
    """

    course_id: str
    lessons: list[CourseMonsterRead]


class BattleHistoryEntry(BaseModel):
    id: str
    lesson_id: str
    monster_code: str
    status: BattleStatus
    end_reason: BattleEndReason | None
    player_hp_left: int
    monster_hp_left: int
    rounds_played: int
    correct_count: int
    best_combo: int
    exp_awarded: int
    gold_awarded: int
    first_clear: bool
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}
