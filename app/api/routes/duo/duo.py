from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DuoServiceDependency
from app.api.routes.duo._duo_errors import raise_duo_http_error
from app.core.exceptions import ApplicationError
from app.schemas.duo.duo import (
    DuoLeaderboardRead,
    DuoMatchDetail,
    DuoMatchSummary,
    DuoRoomPreview,
    DuoStatsRead,
    LeaderboardScope,
)

router = APIRouter()


@router.get("/matches", response_model=list[DuoMatchSummary])
async def list_my_matches(
    current_user: CurrentUser,
    service: DuoServiceDependency,
    limit: Annotated[int, Query(gt=0, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DuoMatchSummary]:
    return await service.list_history(current_user.id, limit, offset)


@router.get("/matches/{match_id}", response_model=DuoMatchDetail)
async def get_match(
    match_id: str, current_user: CurrentUser, service: DuoServiceDependency
) -> DuoMatchDetail:
    try:
        return await service.get_match(current_user.id, match_id)
    except ApplicationError as exc:
        raise_duo_http_error(exc)


@router.get("/me/stats", response_model=DuoStatsRead)
async def get_my_stats(
    current_user: CurrentUser, service: DuoServiceDependency
) -> DuoStatsRead:
    return await service.get_stats(current_user.id)


@router.get("/leaderboard", response_model=DuoLeaderboardRead)
async def get_leaderboard(
    current_user: CurrentUser,
    service: DuoServiceDependency,
    limit: Annotated[int, Query(gt=0, le=100)] = 50,
    season: LeaderboardScope = LeaderboardScope.CURRENT,
) -> DuoLeaderboardRead:
    return await service.get_leaderboard(current_user.id, limit, season)


@router.get("/rooms/{room_code}", response_model=DuoRoomPreview)
async def preview_room(
    room_code: str, current_user: CurrentUser, service: DuoServiceDependency
) -> DuoRoomPreview:
    try:
        return await service.preview_room(room_code.upper())
    except ApplicationError as exc:
        raise_duo_http_error(exc)
