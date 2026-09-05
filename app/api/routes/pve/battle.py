from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import BattleServiceDependency, CurrentUser
from app.api.routes.pve._pve_errors import raise_pve_http_error
from app.core.exceptions import ApplicationError
from app.schemas.pve.battle import (
    BattleHistoryEntry,
    CourseMonstersRead,
    MonsterCatalogRead,
    MonsterPreview,
)

router = APIRouter()


@router.get("/monsters", response_model=list[MonsterCatalogRead])
async def list_monsters(
    current_user: CurrentUser, service: BattleServiceDependency
) -> list[MonsterCatalogRead]:
    return await service.list_monsters()


@router.get("/lessons/{lesson_id}", response_model=MonsterPreview)
async def preview_lesson(
    lesson_id: str, current_user: CurrentUser, service: BattleServiceDependency
) -> MonsterPreview:
    try:
        return await service.preview_lesson(current_user.id, lesson_id)
    except ApplicationError as exc:
        raise_pve_http_error(exc)


@router.get("/courses/{course_id}/monsters", response_model=CourseMonstersRead)
async def list_course_monsters(
    course_id: str, current_user: CurrentUser, service: BattleServiceDependency
) -> CourseMonstersRead:
    try:
        return await service.course_monsters(current_user.id, course_id)
    except ApplicationError as exc:
        raise_pve_http_error(exc)


@router.get("/history", response_model=list[BattleHistoryEntry])
async def list_my_battles(
    current_user: CurrentUser,
    service: BattleServiceDependency,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BattleHistoryEntry]:
    return await service.history(current_user.id, limit, offset)
