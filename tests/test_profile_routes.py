"""The two profile endpoints, exercised without a database or a server."""

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException, status

from app.api.routes.profile.profile import read_my_profile, read_public_profile
from app.core.exceptions import UserNotFoundError
from app.main import app
from app.models.auth.user import User
from app.schemas.game.game import EnergyRead
from app.schemas.profile.profile import (
    LearningStatsRead,
    PublicProfileRead,
    PvpStatsRead,
    SelfProfileRead,
)
from app.services.game.cefr import CefrBand
from app.services.game.season import RankTier

PROFILE_PREFIX = "/api/v1/profile"


def _make_user() -> User:
    user = User(id="user-1", email="player@example.com", username="Player", is_active=True)
    user.created_at = datetime.now(UTC)
    return user


def _public_card(user_id: str = "user-1") -> PublicProfileRead:
    return PublicProfileRead(
        id=user_id,
        username="Player",
        bio=None,
        avatar_url=None,
        joined_at=datetime.now(UTC),
        level=1,
        cefr=CefrBand.A1,
        toeic_estimate=10,
        class_code=None,
        day_streak=0,
        best_day_streak=0,
        pvp=PvpStatsRead(
            rating=1000,
            tier=RankTier.BRONZE,
            matches_played=0,
            wins=0,
            losses=0,
            draws=0,
            win_rate=0.0,
        ),
        learning=LearningStatsRead(
            challenges_attempted=0,
            challenges_mastered=0,
            total_attempts=0,
            accuracy=0.0,
        ),
    )


class FakeProfileService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.public_calls: list[str] = []

    async def get_self(self, user_id: str) -> SelfProfileRead:
        if self.error is not None:
            raise self.error
        return SelfProfileRead(
            **_public_card(user_id).model_dump(),
            email="player@example.com",
            has_uploaded_avatar=False,
            gold=0,
            energy=EnergyRead(current=5, maximum=5, next_regen_at=None),
            total_exp=0,
            exp_for_current_level=0,
            exp_for_next_level=100,
            exp_to_next_level=100,
            next_cefr=CefrBand.A2,
            next_cefr_at_level=11,
        )

    async def get_public(self, user_id: str) -> PublicProfileRead:
        self.public_calls.append(user_id)
        if self.error is not None:
            raise self.error
        return _public_card(user_id)


# --- routing ---------------------------------------------------------------


def test_me_is_declared_before_the_user_id_route() -> None:
    """Routes match in declaration order. The other way round, "me" would be
    read as a user id and `/profile/me` would never be reached.

    Asserted through the OpenAPI schema rather than by walking route objects:
    the schema is a stable public surface, while the internal shape of a
    router's `.routes` changes between FastAPI releases.
    """
    paths = list(app.openapi()["paths"])

    assert paths.index(f"{PROFILE_PREFIX}/me") < paths.index(f"{PROFILE_PREFIX}/{{user_id}}")


def test_the_profile_router_is_mounted() -> None:
    assert f"{PROFILE_PREFIX}/me" in app.openapi()["paths"]
    assert f"{PROFILE_PREFIX}/{{user_id}}" in app.openapi()["paths"]


# --- reading your own ------------------------------------------------------


async def test_reading_my_profile_uses_the_authenticated_id() -> None:
    service = FakeProfileService()

    card = await read_my_profile(_make_user(), service)  # type: ignore[arg-type]

    assert card.id == "user-1"
    assert card.email == "player@example.com"


# --- reading somebody else's ----------------------------------------------


async def test_reading_a_public_profile_passes_the_requested_id() -> None:
    service = FakeProfileService()

    card = await read_public_profile("user-2", _make_user(), service)  # type: ignore[arg-type]

    assert service.public_calls == ["user-2"]
    assert card.id == "user-2"


async def test_an_unknown_player_is_a_404() -> None:
    service = FakeProfileService(error=UserNotFoundError("nobody"))

    with pytest.raises(HTTPException) as raised:
        await read_public_profile("nobody", _make_user(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


async def test_the_404_does_not_echo_the_id_back() -> None:
    """A user id is not secret, but reflecting the caller's own input into an
    error message is how probing endpoints get built."""
    service = FakeProfileService(error=UserNotFoundError("nobody"))

    with pytest.raises(HTTPException) as raised:
        await read_public_profile("nobody", _make_user(), service)  # type: ignore[arg-type]

    assert "nobody" not in str(raised.value.detail)
