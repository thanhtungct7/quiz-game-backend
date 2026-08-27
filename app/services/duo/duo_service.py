"""Read-side of the duo feature: history, stats and the leaderboard.

Live matches are not visible here — they only reach the database once they
finish. The one exception is `preview_room`, which reads the in-memory registry
so a player can check a friend's room code before opening a socket.
"""

from app.core.exceptions import (
    DuoMatchNotFoundError,
    DuoRoomNotFoundError,
    NotMatchMemberError,
)
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatch, DuoMatchStatus
from app.models.duo.duo_match_round import DuoMatchRound
from app.models.duo.duo_rating import DEFAULT_RATING
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.schemas.duo.duo import (
    DuoLeaderboardEntry,
    DuoLeaderboardRead,
    DuoMatchDetail,
    DuoMatchSummary,
    DuoPlayerRead,
    DuoRoomPreview,
    DuoRoundRead,
    DuoStatsRead,
)
from app.services.duo.registry import DuoRegistry
from app.services.duo.registry import registry as default_registry
from app.services.duo.scoring import MatchOutcome


class DuoService:
    def __init__(
        self,
        matches: DuoMatchRepository,
        ratings: DuoRatingRepository,
        challenges: ChallengeRepository,
        registry: DuoRegistry | None = None,
    ) -> None:
        self.matches = matches
        self.ratings = ratings
        self.challenges = challenges
        self.registry = registry or default_registry

    async def list_history(
        self, user_id: str, limit: int, offset: int
    ) -> list[DuoMatchSummary]:
        """Paged list of the user's finished/cancelled matches, newest first,
        each paired with the opponent's profile and current rating."""
        records = await self.matches.list_for_user(user_id, limit, offset)
        opponent_ids = [
            opponent_id
            for record in records
            if (opponent_id := _opponent_id(record, user_id)) is not None
        ]
        opponents = await self.ratings.users_by_ids(opponent_ids)
        ratings = await self.ratings.ratings_by_user_ids(opponent_ids)
        return [_to_summary(record, user_id, opponents, ratings) for record in records]

    async def get_match(self, user_id: str, match_id: str) -> DuoMatchDetail:
        """Full round-by-round breakdown of one match.

        Only a player who took part may view it — anyone else gets
        NotMatchMemberError, not just a 404, so the two failure cases stay
        distinguishable.
        """
        record = await self.matches.get_with_rounds(match_id)
        if record is None:
            raise DuoMatchNotFoundError(match_id)
        if user_id not in (record.player_one_id, record.player_two_id):
            raise NotMatchMemberError(match_id)

        opponent_ids = [opponent_id] if (opponent_id := _opponent_id(record, user_id)) else []
        opponents = await self.ratings.users_by_ids(opponent_ids)
        ratings = await self.ratings.ratings_by_user_ids(opponent_ids)

        challenge_ids = [
            entry.challenge_id for entry in record.rounds if entry.challenge_id is not None
        ]
        questions = {
            challenge.id: challenge.question
            for challenge in await self.challenges.list_by_ids(challenge_ids)
        }

        summary = _to_summary(record, user_id, opponents, ratings)
        is_player_one = record.player_one_id == user_id
        return DuoMatchDetail(
            **summary.model_dump(),
            rounds=[
                _to_round(entry, questions, is_player_one) for entry in record.rounds
            ],
        )

    async def get_stats(self, user_id: str) -> DuoStatsRead:
        """Rating and win/loss summary; a player with no rating row yet
        (never finished a match) gets the default rating and all-zero stats."""
        record = await self.ratings.get_by_user(user_id)
        if record is None:
            return DuoStatsRead(
                rating=DEFAULT_RATING,
                matches_played=0,
                wins=0,
                losses=0,
                draws=0,
                win_rate=0.0,
                current_streak=0,
                best_streak=0,
            )
        played = record.matches_played
        return DuoStatsRead(
            rating=record.rating,
            matches_played=played,
            wins=record.wins,
            losses=record.losses,
            draws=record.draws,
            win_rate=round(record.wins / played * 100, 1) if played else 0.0,
            current_streak=record.current_streak,
            best_streak=record.best_streak,
        )

    async def get_leaderboard(self, user_id: str, limit: int) -> DuoLeaderboardRead:
        """Top N players by rating, plus the caller's own rank even when
        they fall outside that top N."""
        rows = await self.ratings.leaderboard(limit)
        entries = [
            DuoLeaderboardEntry(
                rank=index + 1,
                user_id=rating.user_id,
                username=user.username,
                avatar_url=user.avatar_url,
                rating=rating.rating,
                matches_played=rating.matches_played,
                wins=rating.wins,
            )
            for index, (rating, user) in enumerate(rows)
        ]
        return DuoLeaderboardRead(
            entries=entries, my_rank=await self.ratings.rank_of(user_id)
        )

    async def preview_room(self, room_code: str) -> DuoRoomPreview:
        """Let a would-be joiner see the host and settings for a live room
        before opening a socket. Reads the in-memory registry, not the
        database, since a WAITING room has no row yet."""
        match = self.registry.get_by_code(room_code)
        if match is None or match.status is not DuoMatchStatus.WAITING:
            raise DuoRoomNotFoundError(room_code)
        host = match.players.get(match.host_id)
        if host is None:
            raise DuoRoomNotFoundError(room_code)
        return DuoRoomPreview(
            room_code=match.room_code or room_code,
            host=host.to_read(),
            settings=match.settings.to_read(),
            player_count=len(match.players),
        )


def _opponent_id(record: DuoMatch, user_id: str) -> str | None:
    return record.player_two_id if record.player_one_id == user_id else record.player_one_id


def _outcome_for(record: DuoMatch, user_id: str) -> MatchOutcome | None:
    if record.status is not DuoMatchStatus.FINISHED:
        return None
    if record.winner_id is None:
        return MatchOutcome.DRAW
    return MatchOutcome.WIN if record.winner_id == user_id else MatchOutcome.LOSE


def _to_summary(
    record: DuoMatch,
    user_id: str,
    opponents: dict[str, User],
    ratings: dict[str, int],
) -> DuoMatchSummary:
    is_player_one = record.player_one_id == user_id
    opponent_id = _opponent_id(record, user_id)
    opponent_user = opponents.get(opponent_id) if opponent_id else None
    return DuoMatchSummary(
        match_id=record.id,
        mode=record.mode,
        status=record.status,
        end_reason=record.end_reason,
        outcome=_outcome_for(record, user_id),
        opponent=(
            DuoPlayerRead(
                id=opponent_user.id,
                username=opponent_user.username,
                avatar_url=opponent_user.avatar_url,
                rating=ratings.get(opponent_user.id, DEFAULT_RATING),
            )
            if opponent_user is not None
            else None
        ),
        my_score=record.player_one_score if is_player_one else record.player_two_score,
        opponent_score=record.player_two_score if is_player_one else record.player_one_score,
        my_correct=record.player_one_correct if is_player_one else record.player_two_correct,
        opponent_correct=(
            record.player_two_correct if is_player_one else record.player_one_correct
        ),
        question_count=record.question_count,
        duration_seconds=record.duration_seconds,
        finished_at=record.finished_at,
        created_at=record.created_at,
    )


def _to_round(
    entry: DuoMatchRound, questions: dict[str, str], is_player_one: bool
) -> DuoRoundRead:
    return DuoRoundRead(
        round_index=entry.round_index,
        challenge_id=entry.challenge_id,
        question=questions.get(entry.challenge_id) if entry.challenge_id else None,
        my_option_id=(
            entry.player_one_option_id if is_player_one else entry.player_two_option_id
        ),
        opponent_option_id=(
            entry.player_two_option_id if is_player_one else entry.player_one_option_id
        ),
        my_correct=entry.player_one_correct if is_player_one else entry.player_two_correct,
        opponent_correct=(
            entry.player_two_correct if is_player_one else entry.player_one_correct
        ),
        my_elapsed_ms=(
            entry.player_one_elapsed_ms if is_player_one else entry.player_two_elapsed_ms
        ),
        opponent_elapsed_ms=(
            entry.player_two_elapsed_ms if is_player_one else entry.player_one_elapsed_ms
        ),
        my_points=entry.player_one_points if is_player_one else entry.player_two_points,
        opponent_points=entry.player_two_points if is_player_one else entry.player_one_points,
    )
