"""The aggregator: what a profile reads when the rows behind it are missing,
empty or full."""

from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.exceptions import UserNotFoundError
from app.models.auth.user import User
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.models.game.user_game_profile import UserGameProfile
from app.repository.profile.profile_stats_repository import LearningTotals
from app.services.game.cefr import CefrBand, cefr_floor
from app.services.game.energy import MAX_ENERGY
from app.services.game.leveling import exp_for_level
from app.services.game.season import RankTier
from app.services.profile.profile_service import ProfileService


def _make_user(**overrides: object) -> User:
    user = User(
        id="user-1",
        email="player@example.com",
        username="Player",
        bio="Hello",
        is_active=True,
    )
    user.created_at = datetime.now(UTC)
    for name, value in overrides.items():
        setattr(user, name, value)
    return user


def _make_profile(**overrides: object) -> UserGameProfile:
    profile = UserGameProfile(user_id="user-1")
    profile.total_exp = 0
    profile.gold = 0
    profile.class_code = None
    profile.day_streak = 0
    profile.best_day_streak = 0
    profile.energy = MAX_ENERGY
    profile.energy_updated_at = datetime.now(UTC)
    for name, value in overrides.items():
        setattr(profile, name, value)
    return profile


def _make_rating(**overrides: object) -> DuoRating:
    rating = DuoRating(user_id="user-1")
    rating.rating = DEFAULT_RATING
    rating.matches_played = 0
    rating.wins = 0
    rating.losses = 0
    rating.draws = 0
    for name, value in overrides.items():
        setattr(rating, name, value)
    return rating


class FakeStatsRepository:
    def __init__(
        self,
        found: tuple[User, UserGameProfile | None, DuoRating | None] | None,
        totals: LearningTotals = (0, 0, 0),
    ) -> None:
        self.found = found
        self.totals = totals
        self.calls: list[str] = []

    async def identity_with_standing(
        self, user_id: str
    ) -> tuple[User, UserGameProfile | None, DuoRating | None] | None:
        self.calls.append("identity")
        return self.found

    async def learning_totals(self, user_id: str) -> LearningTotals:
        self.calls.append("learning")
        return self.totals


def _service(repository: FakeStatsRepository) -> ProfileService:
    return ProfileService(stats=repository, config=settings)  # type: ignore[arg-type]


# --- the missing rows ------------------------------------------------------


async def test_an_unknown_user_is_an_error_not_an_empty_card() -> None:
    service = _service(FakeStatsRepository(found=None))

    with pytest.raises(UserNotFoundError):
        await service.get_public("nobody")


async def test_an_account_older_than_the_game_layer_still_reads() -> None:
    """No game profile and no rating row: the two are created lazily, so this
    is an ordinary new account and not a hole in the leaderboard."""
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_public("user-1")

    assert card.level == 1
    assert card.cefr is CefrBand.A1
    assert card.class_code is None
    assert card.day_streak == 0
    assert card.pvp.rating == DEFAULT_RATING
    assert card.pvp.matches_played == 0
    assert card.pvp.win_rate == 0.0


async def test_a_player_with_no_profile_row_has_a_full_energy_bar() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_self("user-1")

    assert card.energy.current == MAX_ENERGY
    assert card.energy.next_regen_at is None
    assert card.gold == 0
    assert card.total_exp == 0


# --- what is derived rather than read --------------------------------------


async def test_level_is_recomputed_from_experience_not_read_from_the_column() -> None:
    # The cached column is deliberately wrong; the card must ignore it.
    profile = _make_profile(total_exp=exp_for_level(12), level=99)
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_public("user-1")

    assert card.level == 12


async def test_the_band_and_the_estimate_follow_the_level() -> None:
    profile = _make_profile(total_exp=exp_for_level(cefr_floor(CefrBand.B1)))
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_public("user-1")

    assert card.cefr is CefrBand.B1
    assert 0 < card.toeic_estimate <= 990


async def test_the_next_band_runs_out_at_the_top() -> None:
    profile = _make_profile(total_exp=exp_for_level(cefr_floor(CefrBand.C2)))
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_self("user-1")

    assert card.cefr is CefrBand.C2
    assert card.next_cefr is None
    assert card.next_cefr_at_level is None


async def test_tier_is_derived_from_rating() -> None:
    rating = _make_rating(rating=1950, matches_played=10, wins=7, losses=3)
    service = _service(FakeStatsRepository(found=(_make_user(), None, rating)))

    card = await service.get_public("user-1")

    assert card.pvp.tier is RankTier.MASTER
    assert card.pvp.win_rate == 70.0


# --- learning stats --------------------------------------------------------


async def test_accuracy_is_answers_that_landed_over_answers_given() -> None:
    service = _service(
        FakeStatsRepository(found=(_make_user(), None, None), totals=(40, 30, 60))
    )

    card = await service.get_public("user-1")

    assert card.learning.challenges_attempted == 40
    assert card.learning.challenges_mastered == 30
    assert card.learning.total_attempts == 60
    assert card.learning.accuracy == 50.0


async def test_a_player_who_has_answered_nothing_does_not_divide_by_zero() -> None:
    service = _service(
        FakeStatsRepository(found=(_make_user(), None, None), totals=(0, 0, 0))
    )

    card = await service.get_public("user-1")

    assert card.learning.accuracy == 0.0


# --- the round trips -------------------------------------------------------


async def test_a_profile_costs_exactly_two_queries() -> None:
    """The public card has a 100ms budget. If this starts failing, something
    began fetching per-field."""
    repository = FakeStatsRepository(found=(_make_user(), _make_profile(), None))
    service = _service(repository)

    await service.get_public("user-1")

    assert repository.calls == ["identity", "learning"]


# --- the private half stays private ----------------------------------------


async def test_the_public_card_carries_no_private_field() -> None:
    """The whole reason `PublicProfileRead` and `SelfProfileRead` are separate
    types. A field added to the wrong one is a leak, so this is asserted on the
    serialised payload rather than on the class."""
    user = _make_user(email="secret@example.com", avatar_file_id="drive-file-id")
    service = _service(FakeStatsRepository(found=(user, _make_profile(gold=999), None)))

    payload = (await service.get_public("user-1")).model_dump()

    assert "email" not in payload
    assert "gold" not in payload
    assert "energy" not in payload
    assert "has_uploaded_avatar" not in payload
    assert "secret@example.com" not in str(payload)


async def test_the_self_card_carries_the_private_half() -> None:
    user = _make_user(email="me@example.com")
    service = _service(FakeStatsRepository(found=(user, _make_profile(gold=42), None)))

    card = await service.get_self("user-1")

    assert card.email == "me@example.com"
    assert card.gold == 42


# --- placeholders for modules that do not exist yet ------------------------


async def test_the_character_and_achievement_fields_answer_empty_for_now() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_public("user-1")

    assert card.title is None
    assert card.companion_character is None
    assert card.skin_code is None
    assert card.achievements == []
