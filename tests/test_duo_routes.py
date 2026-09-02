from datetime import UTC, datetime
from typing import NoReturn

import httpx
import pytest
from fastapi import HTTPException, status

from app.api.routes.duo.duo import (
    get_leaderboard,
    get_match,
    get_my_stats,
    list_my_matches,
    preview_room,
)
from app.core.exceptions import (
    DuoMatchNotFoundError,
    DuoRoomNotFoundError,
    NotMatchMemberError,
)
from app.main import app
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatchMode, DuoMatchStatus
from app.schemas.duo.duo import (
    DuoLeaderboardRead,
    DuoMatchDetail,
    DuoMatchSummary,
    DuoRoomPreview,
    DuoSettingsRead,
    DuoStatsRead,
    LeaderboardScope,
)
from app.schemas.game.player_card import PlayerCardRead
from app.services.duo.scoring import MatchOutcome
from app.services.game.season import RankTier


def _make_user() -> User:
    return User(id="user-1", email="user@example.com")


def _summary() -> DuoMatchSummary:
    return DuoMatchSummary(
        match_id="match-1",
        mode=DuoMatchMode.RANDOM,
        status=DuoMatchStatus.FINISHED,
        end_reason=None,
        outcome=MatchOutcome.WIN,
        opponent=PlayerCardRead(
            id="user-2",
            username="rival",
            avatar_url=None,
            rating=1080,
            tier=RankTier.BRONZE,
            level=7,
            class_code="MAGE",
            day_streak=3,
        ),
        my_score=4200,
        opponent_score=3100,
        my_correct=7,
        opponent_correct=5,
        question_count=10,
        duration_seconds=142,
        finished_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )


def _stats() -> DuoStatsRead:
    return DuoStatsRead(
        rating=1120,
        matches_played=10,
        wins=6,
        losses=3,
        draws=1,
        win_rate=60.0,
        current_streak=2,
        best_streak=4,
    )


class FailingDuoService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def list_history(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_match(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_stats(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_leaderboard(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def preview_room(self, *_: object, **__: object) -> NoReturn:
        raise self.error


class FakeDuoService:
    def __init__(
        self,
        history: list[DuoMatchSummary] | None = None,
        detail: DuoMatchDetail | None = None,
        stats: DuoStatsRead | None = None,
        leaderboard: DuoLeaderboardRead | None = None,
        room: DuoRoomPreview | None = None,
    ) -> None:
        self.history = history or []
        self.detail = detail
        self.stats = stats
        self.leaderboard = leaderboard
        self.room = room
        self.seen_room_code: str | None = None

    async def list_history(
        self, user_id: str, limit: int, offset: int
    ) -> list[DuoMatchSummary]:
        return self.history

    async def get_match(self, user_id: str, match_id: str) -> DuoMatchDetail:
        assert self.detail is not None
        return self.detail

    async def get_stats(self, user_id: str) -> DuoStatsRead:
        assert self.stats is not None
        return self.stats

    async def get_leaderboard(
        self,
        user_id: str,
        limit: int,
        scope: LeaderboardScope = LeaderboardScope.CURRENT,
    ) -> DuoLeaderboardRead:
        assert self.leaderboard is not None
        return self.leaderboard

    async def preview_room(self, room_code: str) -> DuoRoomPreview:
        self.seen_room_code = room_code
        assert self.room is not None
        return self.room


@pytest.mark.asyncio
async def test_list_my_matches_returns_history() -> None:
    service = FakeDuoService(history=[_summary()])

    result = await list_my_matches(
        current_user=_make_user(),
        service=service,  # type: ignore[arg-type]
        limit=20,
        offset=0,
    )

    assert [item.match_id for item in result] == ["match-1"]


@pytest.mark.asyncio
async def test_get_match_maps_missing_match_to_404() -> None:
    service = FailingDuoService(DuoMatchNotFoundError("match-9"))

    with pytest.raises(HTTPException) as error:
        await get_match(
            match_id="match-9",
            current_user=_make_user(),
            service=service,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_match_maps_a_stranger_to_403() -> None:
    service = FailingDuoService(NotMatchMemberError("match-1"))

    with pytest.raises(HTTPException) as error:
        await get_match(
            match_id="match-1",
            current_user=_make_user(),
            service=service,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_get_match_returns_detail_with_rounds() -> None:
    detail = DuoMatchDetail(
        **_summary().model_dump(),
        rounds=[],
        my_hp_left=40,
        opponent_hp_left=0,
        skill_uses=[],
    )
    service = FakeDuoService(detail=detail)

    result = await get_match(
        match_id="match-1",
        current_user=_make_user(),
        service=service,  # type: ignore[arg-type]
    )

    assert result.match_id == "match-1"
    assert result.rounds == []


@pytest.mark.asyncio
async def test_get_my_stats_returns_the_standing() -> None:
    service = FakeDuoService(stats=_stats())

    result = await get_my_stats(
        current_user=_make_user(),
        service=service,  # type: ignore[arg-type]
    )

    assert result.rating == 1120
    assert result.win_rate == 60.0


@pytest.mark.asyncio
async def test_get_leaderboard_returns_entries_and_my_rank() -> None:
    service = FakeDuoService(leaderboard=DuoLeaderboardRead(entries=[], my_rank=7))

    result = await get_leaderboard(
        current_user=_make_user(),
        service=service,  # type: ignore[arg-type]
        limit=50,
    )

    assert result.my_rank == 7


@pytest.mark.asyncio
async def test_preview_room_maps_unknown_code_to_404() -> None:
    service = FailingDuoService(DuoRoomNotFoundError("ZZZZZZ"))

    with pytest.raises(HTTPException) as error:
        await preview_room(
            room_code="zzzzzz",
            current_user=_make_user(),
            service=service,  # type: ignore[arg-type]
        )

    assert error.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_preview_room_upper_cases_the_code_before_lookup() -> None:
    service = FakeDuoService(
        room=DuoRoomPreview(
            room_code="AB12CD",
            host=PlayerCardRead(
                id="user-2",
                username="host",
                avatar_url=None,
                rating=1000,
                tier=RankTier.BRONZE,
                level=1,
                class_code=None,
                day_streak=0,
            ),
            settings=DuoSettingsRead(
                question_count=10, time_per_question=15, topic_ids=None, difficulty=None
            ),
            player_count=1,
        )
    )

    await preview_room(
        room_code="ab12cd",
        current_user=_make_user(),
        service=service,  # type: ignore[arg-type]
    )

    assert service.seen_room_code == "AB12CD"


@pytest.mark.asyncio
async def test_duo_routes_are_registered() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/openapi.json")

    paths = response.json()["paths"]
    assert "/api/v1/duo/matches" in paths
    assert "/api/v1/duo/matches/{match_id}" in paths
    assert "/api/v1/duo/me/stats" in paths
    assert "/api/v1/duo/leaderboard" in paths
    assert "/api/v1/duo/rooms/{room_code}" in paths
