"""Read-side of the duo feature: history, stats and the leaderboard.

Live matches are not visible here — they only reach the database once they
finish. The one exception is `preview_room`, which reads the in-memory registry
so a player can check a friend's room code before opening a socket.
"""

from app.core.config import settings
from app.core.exceptions import (
    DuoMatchNotFoundError,
    DuoRoomNotFoundError,
    NotMatchMemberError,
)
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatch, DuoMatchStatus
from app.models.duo.duo_rating import DEFAULT_RATING
from app.models.game.user_game_profile import UserGameProfile
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.season_repository import SeasonRepository
from app.schemas.duo.duo import (
    DuoLeaderboardEntry,
    DuoLeaderboardRead,
    DuoMatchDetail,
    DuoMatchSummary,
    DuoRoomPreview,
    DuoSkillUseRead,
    DuoStatsRead,
    LeaderboardScope,
)
from app.services.auth.avatar_url import resolve_avatar_url
from app.services.duo.registry import DuoRegistry
from app.services.duo.registry import registry as default_registry
from app.services.duo.scoring import MatchOutcome
from app.services.game.player_card import build_player_card, standing_of
from app.services.game.season import tier_for_rating


class DuoService:
    def __init__(
        self,
        matches: DuoMatchRepository,
        ratings: DuoRatingRepository,
        profiles: GameProfileRepository,
        registry: DuoRegistry | None = None,
        seasons: SeasonRepository | None = None,
    ) -> None:
        self.matches = matches
        self.ratings = ratings
        # Required, not optional: without it every opponent on the history
        # page would silently read as a level-1 player with no class.
        self.profiles = profiles
        self.registry = registry or default_registry
        # Optional: without it the leaderboard simply serves the all-time board.
        self.seasons = seasons

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
        profiles = await self.profiles.profiles_by_user_ids(opponent_ids)
        return [
            _to_summary(record, user_id, opponents, ratings, profiles)
            for record in records
        ]

    async def get_match(self, user_id: str, match_id: str) -> DuoMatchDetail:
        """One finished match in full: the summary, the health both sides were
        left on, and every skill either of them fired.

        There is no answer-by-answer replay any more, and there cannot be: the
        two players work through their own decks at their own pace, so there is
        no shared round for a replay to be a list of.

        Only a player who took part may view it — anyone else gets
        NotMatchMemberError, not just a 404, so the two failure cases stay
        distinguishable.
        """
        record = await self.matches.get_by_id(match_id)
        if record is None:
            raise DuoMatchNotFoundError(match_id)
        if user_id not in (record.player_one_id, record.player_two_id):
            raise NotMatchMemberError(match_id)

        opponent_ids = [opponent_id] if (opponent_id := _opponent_id(record, user_id)) else []
        opponents = await self.ratings.users_by_ids(opponent_ids)
        ratings = await self.ratings.ratings_by_user_ids(opponent_ids)
        profiles = await self.profiles.profiles_by_user_ids(opponent_ids)

        summary = _to_summary(record, user_id, opponents, ratings, profiles)
        is_player_one = record.player_one_id == user_id
        return DuoMatchDetail(
            **summary.model_dump(),
            my_hp_left=(
                record.player_one_hp_left if is_player_one else record.player_two_hp_left
            ),
            opponent_hp_left=(
                record.player_two_hp_left if is_player_one else record.player_one_hp_left
            ),
            skill_uses=[
                DuoSkillUseRead(
                    round_index=use.round_index,
                    skill_code=use.skill_code,
                    mana_spent=use.mana_spent,
                    mine=use.user_id == user_id,
                )
                for use in await self.matches.list_skill_uses(match_id)
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

    async def get_leaderboard(
        self,
        user_id: str,
        limit: int,
        scope: LeaderboardScope = LeaderboardScope.CURRENT,
    ) -> DuoLeaderboardRead:
        """Top N players by rating, plus the caller's own rank even when
        they fall outside that top N.

        The seasonal board is the default. The all-time board reads
        `duo_ratings`, which is never reset, so both views stay available.
        """
        if scope is LeaderboardScope.CURRENT and self.seasons is not None:
            return await self._season_leaderboard(user_id, limit)

        rows = await self.ratings.leaderboard(limit)
        return DuoLeaderboardRead(
            entries=[
                DuoLeaderboardEntry(
                    rank=index + 1,
                    user_id=rating.user_id,
                    username=user.username,
                    avatar_url=resolve_avatar_url(user, settings),
                    rating=rating.rating,
                    matches_played=rating.matches_played,
                    wins=rating.wins,
                    tier=tier_for_rating(rating.rating),
                )
                for index, (rating, user) in enumerate(rows)
            ],
            my_rank=await self.ratings.rank_of(user_id),
            scope=LeaderboardScope.ALL_TIME,
        )

    async def _season_leaderboard(self, user_id: str, limit: int) -> DuoLeaderboardRead:
        assert self.seasons is not None
        season = await self.seasons.active()
        if season is None:
            # No season open yet: fall back rather than serving an empty board.
            return await self.get_leaderboard(user_id, limit, LeaderboardScope.ALL_TIME)

        rows = await self.seasons.leaderboard(season.id, limit)
        entries = [
            DuoLeaderboardEntry(
                rank=index + 1,
                user_id=rating.user_id,
                username=user.username,
                avatar_url=resolve_avatar_url(user, settings),
                rating=rating.rating,
                matches_played=rating.matches_played,
                wins=rating.wins,
                tier=tier_for_rating(rating.rating),
            )
            for index, (rating, user) in enumerate(rows)
        ]
        my_rank = next(
            (entry.rank for entry in entries if entry.user_id == user_id), None
        )
        return DuoLeaderboardRead(
            entries=entries,
            my_rank=my_rank,
            scope=LeaderboardScope.CURRENT,
            season_code=season.code,
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
    profiles: dict[str, UserGameProfile],
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
            build_player_card(
                user_id=opponent_user.id,
                username=opponent_user.username,
                avatar_url=resolve_avatar_url(opponent_user, settings),
                standing=standing_of(
                    profiles.get(opponent_user.id),
                    ratings.get(opponent_user.id, DEFAULT_RATING),
                ),
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
