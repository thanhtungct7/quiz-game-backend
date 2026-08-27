"""Database access for the duo match engine.

The engine holds no session of its own: a WebSocket connection can stay open
for minutes and must not pin a transaction for that long. Instead each of the
few moments that actually need the database opens a short session here.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.db.session import AsyncSessionFactory
from app.models.duo.duo_match import DuoMatch, DuoMatchEndReason, DuoMatchStatus
from app.models.duo.duo_match_round import DuoMatchRound
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.schemas.content.quiz import QuizSetWithAnswers
from app.services.content.quiz_service import QuizService
from app.services.duo import rating as elo
from app.services.duo.scoring import MatchOutcome
from app.services.duo.state import LiveMatch, MatchSettings, RoundRecord


@dataclass
class MatchResult:
    """The decided outcome of a match, ready to be written down."""

    outcome_by_user: dict[str, MatchOutcome]
    winner_id: str | None
    end_reason: DuoMatchEndReason
    duration_seconds: int


@dataclass
class RatingChange:
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


class DuoPersistence(Protocol):
    """What the engine needs from storage. Tests substitute a fake."""

    async def load_rating(self, user_id: str) -> int: ...

    async def draw_questions(self, settings: MatchSettings) -> QuizSetWithAnswers: ...

    async def create_match(self, match: LiveMatch) -> None: ...

    async def save_result(
        self, match: LiveMatch, result: MatchResult
    ) -> dict[str, RatingChange]: ...

    async def cancel_match(self, match: LiveMatch) -> None: ...


def elo_score(outcome: MatchOutcome) -> float:
    """Map a match outcome to the 1/0.5/0 score the Elo formula expects."""
    if outcome is MatchOutcome.WIN:
        return elo.WIN
    if outcome is MatchOutcome.LOSE:
        return elo.LOSS
    return elo.DRAW


class DatabaseDuoPersistence:
    async def load_rating(self, user_id: str) -> int:
        async with AsyncSessionFactory() as db:
            record = await DuoRatingRepository(db).get_by_user(user_id)
            return record.rating if record is not None else DEFAULT_RATING

    async def draw_questions(self, settings: MatchSettings) -> QuizSetWithAnswers:
        async with AsyncSessionFactory() as db:
            service = QuizService(
                challenges=ChallengeRepository(db),
                lessons=LessonRepository(db),
                units=UnitRepository(db),
            )
            return await service.generate_for_duo(
                count=settings.question_count,
                topic_ids=settings.topic_id_list,
                difficulties=settings.difficulty_list,
            )

    async def create_match(self, match: LiveMatch) -> None:
        player_ids = match.player_ids
        async with AsyncSessionFactory() as db:
            await DuoMatchRepository(db).create(
                DuoMatch(
                    id=match.match_id,
                    room_code=match.room_code,
                    mode=match.mode,
                    status=DuoMatchStatus.IN_PROGRESS,
                    player_one_id=player_ids[0],
                    player_two_id=player_ids[1] if len(player_ids) > 1 else None,
                    question_count=len(match.questions),
                    time_per_question=match.settings.time_per_question,
                    # The schema keeps a single topic; a multi-topic match is
                    # recorded as unfiltered rather than misattributed to one.
                    topic_id=(
                        match.settings.topic_ids[0]
                        if len(match.settings.topic_ids) == 1
                        else None
                    ),
                    difficulty=match.settings.difficulty,
                    started_at=match.started_wall or datetime.now(UTC),
                )
            )

    async def save_result(
        self, match: LiveMatch, result: MatchResult
    ) -> dict[str, RatingChange]:
        """Finalize a match row: apply rating changes, mark it FINISHED with
        the final scores, and write out every round played.

        Runs as one session so the match record and its rounds are written
        atomically; rating updates happen first so their result can be
        embedded in the same response.
        """
        player_ids = match.player_ids
        one_id = player_ids[0]
        two_id = player_ids[1] if len(player_ids) > 1 else None
        one = match.players[one_id]
        two = match.players[two_id] if two_id is not None else None

        async with AsyncSessionFactory() as db:
            matches = DuoMatchRepository(db)
            changes = await self._apply_ratings(DuoRatingRepository(db), match, result)

            record = await matches.get_by_id(match.match_id)
            if record is None:
                return changes

            await matches.finish(
                record,
                {
                    "status": DuoMatchStatus.FINISHED,
                    "end_reason": result.end_reason,
                    "winner_id": result.winner_id,
                    "player_one_score": one.score,
                    "player_two_score": two.score if two is not None else 0,
                    "player_one_correct": one.correct_count,
                    "player_two_correct": two.correct_count if two is not None else 0,
                    "finished_at": datetime.now(UTC),
                    "duration_seconds": result.duration_seconds,
                },
            )
            await matches.add_rounds(
                [
                    _to_round_row(match.match_id, entry, one_id, two_id)
                    for entry in match.rounds_log
                ]
            )
            return changes

    async def cancel_match(self, match: LiveMatch) -> None:
        async with AsyncSessionFactory() as db:
            matches = DuoMatchRepository(db)
            record = await matches.get_by_id(match.match_id)
            if record is None:
                return
            await matches.finish(
                record,
                {
                    "status": DuoMatchStatus.CANCELLED,
                    "end_reason": DuoMatchEndReason.CANCELLED,
                    "finished_at": datetime.now(UTC),
                },
            )

    async def _apply_ratings(
        self,
        ratings: DuoRatingRepository,
        match: LiveMatch,
        result: MatchResult,
    ) -> dict[str, RatingChange]:
        """Run the Elo update for both players and persist their new ratings/streaks.

        A match that never got a second player (e.g. an abandoned room) has
        nothing to rate, so it returns an empty change set.
        """
        player_ids = match.player_ids
        if len(player_ids) < 2:
            return {}

        one_id, two_id = player_ids[0], player_ids[1]
        one_record = await ratings.get_or_create(one_id)
        two_record = await ratings.get_or_create(two_id)

        score_one = elo_score(result.outcome_by_user[one_id])
        before_one, before_two = one_record.rating, two_record.rating
        after_one, after_two = elo.apply_result(before_one, before_two, score_one)

        await ratings.save(one_record, _rating_update(one_record, after_one, score_one))
        await ratings.save(
            two_record, _rating_update(two_record, after_two, 1.0 - score_one)
        )
        return {
            one_id: RatingChange(before=before_one, after=after_one),
            two_id: RatingChange(before=before_two, after=after_two),
        }


def _rating_update(record: DuoRating, new_rating: int, score: float) -> dict[str, object]:
    streak = elo.streak_after(record.current_streak, score)
    return {
        "rating": new_rating,
        "matches_played": record.matches_played + 1,
        "wins": record.wins + (1 if score == elo.WIN else 0),
        "losses": record.losses + (1 if score == elo.LOSS else 0),
        "draws": record.draws + (1 if score == elo.DRAW else 0),
        "current_streak": streak,
        "best_streak": max(record.best_streak, streak),
        "updated_at": datetime.now(UTC),
    }


def _to_round_row(
    match_id: str, record: RoundRecord, one_id: str, two_id: str | None
) -> DuoMatchRound:
    one_answer = record.answers.get(one_id)
    two_answer = record.answers.get(two_id) if two_id is not None else None
    return DuoMatchRound(
        match_id=match_id,
        round_index=record.round_index,
        challenge_id=record.challenge_id,
        player_one_option_id=one_answer.option_id if one_answer else None,
        player_two_option_id=two_answer.option_id if two_answer else None,
        player_one_correct=one_answer.is_correct if one_answer else False,
        player_two_correct=two_answer.is_correct if two_answer else False,
        player_one_elapsed_ms=one_answer.elapsed_ms if one_answer else None,
        player_two_elapsed_ms=two_answer.elapsed_ms if two_answer else None,
        player_one_points=one_answer.points if one_answer else 0,
        player_two_points=two_answer.points if two_answer else 0,
    )
