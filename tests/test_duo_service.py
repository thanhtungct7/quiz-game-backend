from datetime import UTC, datetime

import pytest

from app.core.exceptions import (
    DuoMatchNotFoundError,
    DuoRoomNotFoundError,
    NotMatchMemberError,
)
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatch, DuoMatchMode, DuoMatchStatus
from app.models.duo.duo_rating import DuoRating
from app.models.game.duo_match_skill_use import DuoMatchSkillUse
from app.models.game.user_game_profile import UserGameProfile
from app.services.duo.duo_service import DuoService
from app.services.duo.registry import DuoRegistry
from app.services.duo.scoring import MatchOutcome
from app.services.duo.state import LiveMatch, MatchSettings, PlayerConn
from app.services.game.leveling import exp_for_level
from app.services.game.player_card import PlayerStanding
from app.services.game.season import RankTier

ONE = "player-one"
TWO = "player-two"


class FakeDuoMatchRepository:
    def __init__(
        self, matches: list[DuoMatch], skill_uses: list[DuoMatchSkillUse] | None = None
    ) -> None:
        self.matches = matches
        self.skill_uses = skill_uses or []

    async def list_for_user(self, user_id: str, limit: int, offset: int) -> list[DuoMatch]:
        owned = [
            match
            for match in self.matches
            if user_id in (match.player_one_id, match.player_two_id)
        ]
        return owned[offset : offset + limit]

    async def get_by_id(self, match_id: str) -> DuoMatch | None:
        return next((match for match in self.matches if match.id == match_id), None)

    async def list_skill_uses(self, match_id: str) -> list[DuoMatchSkillUse]:
        return list(self.skill_uses)


class FakeDuoRatingRepository:
    def __init__(
        self,
        ratings: dict[str, DuoRating] | None = None,
        users: dict[str, User] | None = None,
    ) -> None:
        self.ratings = ratings or {}
        self.users = users or {}

    async def get_by_user(self, user_id: str) -> DuoRating | None:
        return self.ratings.get(user_id)

    async def ratings_by_user_ids(self, user_ids: list[str]) -> dict[str, int]:
        return {
            user_id: self.ratings[user_id].rating
            for user_id in user_ids
            if user_id in self.ratings
        }

    async def users_by_ids(self, user_ids: list[str]) -> dict[str, User]:
        return {user_id: self.users[user_id] for user_id in user_ids if user_id in self.users}

    async def leaderboard(self, limit: int, offset: int = 0) -> list[tuple[DuoRating, User]]:
        rows = [
            (rating, self.users[user_id])
            for user_id, rating in self.ratings.items()
            if user_id in self.users
        ]
        rows.sort(key=lambda row: row[0].rating, reverse=True)
        return rows[offset : offset + limit]

    async def rank_of(self, user_id: str) -> int | None:
        own = self.ratings.get(user_id)
        if own is None:
            return None
        higher = sum(1 for rating in self.ratings.values() if rating.rating > own.rating)
        return higher + 1


class FakeGameProfileRepository:
    def __init__(self, profiles: dict[str, UserGameProfile] | None = None) -> None:
        self.profiles = profiles or {}

    async def profiles_by_user_ids(self, user_ids: list[str]) -> dict[str, UserGameProfile]:
        return {
            user_id: self.profiles[user_id]
            for user_id in user_ids
            if user_id in self.profiles
        }


def _match(
    *,
    winner_id: str | None = ONE,
    status: DuoMatchStatus = DuoMatchStatus.FINISHED,
) -> DuoMatch:
    return DuoMatch(
        id="match-1",
        mode=DuoMatchMode.RANDOM,
        status=status,
        end_reason=None,
        player_one_id=ONE,
        player_two_id=TWO,
        winner_id=winner_id,
        player_one_score=4200,
        player_two_score=3100,
        player_one_correct=7,
        player_two_correct=5,
        player_one_hp_left=64,
        player_two_hp_left=0,
        question_count=10,
        time_per_question=15,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        duration_seconds=140,
    )


def _rating(user_id: str, *, rating: int, played: int = 0, wins: int = 0) -> DuoRating:
    """A rating row as it comes back from the database: every counter set.

    Constructing the model outside a session skips the column defaults, which
    are applied on INSERT, so the counters have to be spelled out here.
    """
    return DuoRating(
        user_id=user_id,
        rating=rating,
        matches_played=played,
        wins=wins,
        losses=played - wins,
        draws=0,
        current_streak=0,
        best_streak=wins,
    )


def _service(
    matches: list[DuoMatch] | None = None,
    ratings: dict[str, DuoRating] | None = None,
    users: dict[str, User] | None = None,
    registry: DuoRegistry | None = None,
    profiles: dict[str, UserGameProfile] | None = None,
    skill_uses: list[DuoMatchSkillUse] | None = None,
) -> DuoService:
    return DuoService(
        matches=FakeDuoMatchRepository(matches or [], skill_uses),  # type: ignore[arg-type]
        ratings=FakeDuoRatingRepository(ratings, users),  # type: ignore[arg-type]
        profiles=FakeGameProfileRepository(profiles),  # type: ignore[arg-type]
        registry=registry or DuoRegistry(),
    )


# --- history ---------------------------------------------------------------


async def test_history_is_told_from_player_ones_side() -> None:
    service = _service(
        matches=[_match()],
        users={TWO: User(id=TWO, email="two@example.com", username="rival")},
        ratings={TWO: _rating(TWO, rating=1080)},
    )

    [summary] = await service.list_history(ONE, limit=10, offset=0)

    assert summary.outcome is MatchOutcome.WIN
    assert summary.my_score == 4200
    assert summary.opponent_score == 3100
    assert summary.my_correct == 7
    assert summary.opponent is not None
    assert summary.opponent.id == TWO
    assert summary.opponent.rating == 1080


async def test_a_history_opponent_is_a_full_player_card() -> None:
    """The card an opponent shows in history is the same one they showed in
    the lobby: level and class, not just a rating."""
    service = _service(
        matches=[_match()],
        users={TWO: User(id=TWO, email="two@example.com", username="rival")},
        ratings={TWO: _rating(TWO, rating=1520)},
        profiles={
            TWO: UserGameProfile(
                user_id=TWO,
                total_exp=exp_for_level(9),
                level=9,
                class_code="MAGE",
                day_streak=5,
            )
        },
    )

    [summary] = await service.list_history(ONE, limit=10, offset=0)

    assert summary.opponent is not None
    assert summary.opponent.level == 9
    assert summary.opponent.class_code == "MAGE"
    assert summary.opponent.day_streak == 5
    assert summary.opponent.tier is RankTier.PLATINUM


async def test_an_opponent_without_a_game_profile_lands_at_level_one() -> None:
    service = _service(
        matches=[_match()],
        users={TWO: User(id=TWO, email="two@example.com", username="rival")},
        ratings={TWO: _rating(TWO, rating=1080)},
    )

    [summary] = await service.list_history(ONE, limit=10, offset=0)

    assert summary.opponent is not None
    assert summary.opponent.level == 1
    assert summary.opponent.class_code is None


async def test_the_same_match_is_mirrored_for_player_two() -> None:
    service = _service(
        matches=[_match()],
        users={ONE: User(id=ONE, email="one@example.com", username="champ")},
    )

    [summary] = await service.list_history(TWO, limit=10, offset=0)

    assert summary.outcome is MatchOutcome.LOSE
    assert summary.my_score == 3100
    assert summary.opponent_score == 4200
    assert summary.my_correct == 5
    assert summary.opponent is not None
    assert summary.opponent.id == ONE


async def test_a_match_with_no_winner_reads_as_a_draw_for_both() -> None:
    service = _service(matches=[_match(winner_id=None)])

    [for_one] = await service.list_history(ONE, limit=10, offset=0)
    [for_two] = await service.list_history(TWO, limit=10, offset=0)

    assert for_one.outcome is MatchOutcome.DRAW
    assert for_two.outcome is MatchOutcome.DRAW


async def test_an_unfinished_match_has_no_outcome() -> None:
    service = _service(matches=[_match(status=DuoMatchStatus.ABANDONED, winner_id=None)])

    [summary] = await service.list_history(ONE, limit=10, offset=0)

    assert summary.outcome is None


async def test_an_opponent_the_lookup_missed_leaves_a_null_opponent() -> None:
    service = _service(matches=[_match()])

    [summary] = await service.list_history(ONE, limit=10, offset=0)

    assert summary.opponent is None


# --- match detail ----------------------------------------------------------


async def test_match_detail_is_mirrored_for_player_two() -> None:
    """No round-by-round replay any more -- the two players work through their
    own decks, so there is no shared round for a list to be of. What is still
    told from the reader's own side is the score, the health and the skills."""
    use = DuoMatchSkillUse(
        match_id="match-1",
        round_index=3,
        user_id=ONE,
        skill_id="skill-1",
        skill_code="SHIELD",
        mana_spent=30,
    )
    service = _service(matches=[_match()], skill_uses=[use])

    detail = await service.get_match(TWO, "match-1")

    assert detail.my_hp_left == 0
    assert detail.opponent_hp_left == 64
    assert detail.my_score == 3100
    assert detail.opponent_score == 4200
    [skill_use] = detail.skill_uses
    assert skill_use.skill_code == "SHIELD"
    assert skill_use.mine is False


async def test_reading_someone_elses_match_is_refused() -> None:
    service = _service(matches=[_match()])

    with pytest.raises(NotMatchMemberError):
        await service.get_match("stranger", "match-1")


async def test_reading_a_missing_match_raises() -> None:
    service = _service()

    with pytest.raises(DuoMatchNotFoundError):
        await service.get_match(ONE, "nope")


# --- stats and leaderboard -------------------------------------------------


async def test_stats_default_for_a_player_who_never_duelled() -> None:
    stats = await _service().get_stats(ONE)

    assert stats.matches_played == 0
    assert stats.rating == 1000
    assert stats.win_rate == 0.0


async def test_win_rate_is_a_percentage_of_matches_played() -> None:
    service = _service(ratings={ONE: _rating(ONE, rating=1150, played=8, wins=3)})

    stats = await service.get_stats(ONE)

    assert stats.win_rate == 37.5


async def test_leaderboard_is_ranked_and_carries_my_position() -> None:
    service = _service(
        ratings={
            ONE: _rating(ONE, rating=1100, played=5, wins=3),
            TWO: _rating(TWO, rating=1300, played=9, wins=7),
        },
        users={
            ONE: User(id=ONE, email="one@example.com", username="one"),
            TWO: User(id=TWO, email="two@example.com", username="two"),
        },
    )

    board = await service.get_leaderboard(ONE, limit=10)

    assert [entry.user_id for entry in board.entries] == [TWO, ONE]
    assert [entry.rank for entry in board.entries] == [1, 2]
    assert board.my_rank == 2


# --- room preview ----------------------------------------------------------


async def test_room_preview_reads_the_live_registry() -> None:
    registry = DuoRegistry()
    live = LiveMatch(
        match_id="match-1",
        mode=DuoMatchMode.FRIEND,
        host_id=ONE,
        settings=MatchSettings(question_count=10, time_per_question=15),
        room_code="AB12CD",
    )
    live.players[ONE] = PlayerConn(
        user_id=ONE, username="host", avatar_url=None, standing=PlayerStanding(rating=1000)
    )
    registry.register(live)
    service = _service(registry=registry)

    preview = await service.preview_room("AB12CD")

    assert preview.host.username == "host"
    assert preview.player_count == 1
    assert preview.settings.question_count == 10


async def test_a_room_that_already_started_is_not_previewable() -> None:
    registry = DuoRegistry()
    live = LiveMatch(
        match_id="match-1",
        mode=DuoMatchMode.FRIEND,
        host_id=ONE,
        settings=MatchSettings(question_count=10, time_per_question=15),
        room_code="AB12CD",
        status=DuoMatchStatus.IN_PROGRESS,
    )
    live.players[ONE] = PlayerConn(
        user_id=ONE, username="host", avatar_url=None, standing=PlayerStanding(rating=1000)
    )
    registry.register(live)
    service = _service(registry=registry)

    with pytest.raises(DuoRoomNotFoundError):
        await service.preview_room("AB12CD")
