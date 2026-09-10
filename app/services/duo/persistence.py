"""Database access for the duo match engine.

The engine holds no session of its own: a WebSocket connection can stay open
for minutes and must not pin a transaction for that long. Instead each of the
few moments that actually need the database opens a short session here.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.db.session import AsyncSessionFactory
from app.models.duo.duo_match import (
    STARTING_HP,
    DuoMatch,
    DuoMatchEndReason,
    DuoMatchStatus,
)
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.models.game.duo_match_skill_use import DuoMatchSkillUse
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.repository.game.achievement_repository import AchievementRepository
from app.repository.game.activity_repository import ActivityRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.season_repository import SeasonRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.schemas.content.quiz import QuizSetWithAnswers
from app.schemas.duo.events import ErrorCode
from app.services.content.quiz_service import QuizService
from app.services.duo import rating as elo
from app.services.duo.scoring import MatchOutcome
from app.services.duo.state import LiveMatch, MatchSettings
from app.services.game.achievement_service import AchievementService
from app.services.game.energy_service import EnergyService
from app.services.game.loadout import PlayerLoadout
from app.services.game.loadout_builder import LoadoutBuilder
from app.services.game.player_card import PlayerStanding, standing_of
from app.services.game.rewards import RewardInput
from app.services.game.settlement import (
    ExpAward,
    GameSettlementService,
    GoldAward,
    LootDrop,
    SeasonChange,
    StreakChange,
)


@dataclass
class MatchResult:
    """The decided outcome of a match, ready to be written down."""

    outcome_by_user: dict[str, MatchOutcome]
    winner_id: str | None
    end_reason: DuoMatchEndReason
    duration_seconds: int
    # Who walked out, if anyone. Only this player pays the forfeit penalty --
    # the opponent of a quitter is a winner, not a forfeiter.
    forfeit_user_id: str | None = None
    knockout: bool = False


@dataclass
class RatingChange:
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class MatchStartRejected:
    """The match may not begin. Nothing was written and nothing was charged."""

    reason: ErrorCode
    message: str
    user_ids: tuple[str, ...]


@dataclass
class MatchRewards:
    """Everything a finished match paid out, keyed by user id."""

    rating: dict[str, RatingChange]
    exp: dict[str, ExpAward]
    gold: dict[str, GoldAward]
    loot: dict[str, LootDrop | None] = field(default_factory=dict)
    season: dict[str, SeasonChange | None] = field(default_factory=dict)
    streak: dict[str, StreakChange | None] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> "MatchRewards":
        return cls(rating={}, exp={}, gold={})


class DuoPersistence(Protocol):
    """What the engine needs from storage. Tests substitute a fake."""

    async def load_standing(self, user_id: str) -> PlayerStanding: ...

    async def draw_questions(self, settings: MatchSettings) -> QuizSetWithAnswers: ...

    async def start_players(
        self, match: LiveMatch
    ) -> dict[str, PlayerLoadout] | MatchStartRejected: ...

    async def refund_start(self, match: LiveMatch) -> None: ...

    async def create_match(self, match: LiveMatch) -> None: ...

    async def save_result(self, match: LiveMatch, result: MatchResult) -> MatchRewards: ...

    async def cancel_match(self, match: LiveMatch) -> None: ...


def elo_score(outcome: MatchOutcome) -> float:
    """Map a match outcome to the 1/0.5/0 score the Elo formula expects."""
    if outcome is MatchOutcome.WIN:
        return elo.WIN
    if outcome is MatchOutcome.LOSE:
        return elo.LOSS
    return elo.DRAW


class DatabaseDuoPersistence:
    async def load_standing(self, user_id: str) -> PlayerStanding:
        """Everything on a player's card except their identity, in one session.

        Read at the door -- opening a socket, queueing, creating or joining a
        room -- rather than per round: a card is a snapshot, and the two rows
        behind it only move when a match settles.
        """
        async with AsyncSessionFactory() as db:
            record = await DuoRatingRepository(db).get_by_user(user_id)
            profile = await GameProfileRepository(db).get_by_user(user_id)
            return standing_of(
                profile, record.rating if record is not None else DEFAULT_RATING
            )

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

    async def start_players(
        self, match: LiveMatch
    ) -> dict[str, PlayerLoadout] | MatchStartRejected:
        """Charge the players and resolve what each brings, in one session.

        Per-match rather than per-player because `DatabaseDuoPersistence` opens
        a session per call: two players would otherwise mean two sessions, and
        energy could be taken from one and not the other.

        Energy is checked for everyone before it is taken from anyone, so a
        rejection leaves both bars untouched and needs no refund.
        """
        async with AsyncSessionFactory() as db:
            profiles = GameProfileRepository(db)
            energy = EnergyService(profiles=profiles)

            short = await energy.who_is_out_of_energy(match.player_ids)
            if short:
                return MatchStartRejected(
                    reason=ErrorCode.NOT_ENOUGH_ENERGY,
                    message="Not enough energy to start a match",
                    user_ids=tuple(short),
                )
            await energy.spend_for_match(match.player_ids)

            builder = LoadoutBuilder(
                profiles=profiles,
                catalog=CatalogRepository(db),
                skills=UserSkillRepository(db),
                items=ItemRepository(db),
            )
            return {
                user_id: await builder.build(user_id) for user_id in match.player_ids
            }

    async def refund_start(self, match: LiveMatch) -> None:
        """Give back what the start charged, for a match that never happened."""
        async with AsyncSessionFactory() as db:
            await EnergyService(profiles=GameProfileRepository(db)).refund_match(
                match.player_ids
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

    async def save_result(self, match: LiveMatch, result: MatchResult) -> MatchRewards:
        """Finalize a match: rating, experience and gold, then the match row
        and every round played.

        The first thing this does is claim the match. That claim is what makes
        the whole method safe to re-enter: the body below commits several times
        (each repository commits its own write), so without it a retry after a
        partial failure could pay a player twice. A caller that loses the claim
        gets an empty reward set and writes nothing.
        """
        player_ids = match.player_ids
        one_id = player_ids[0]
        two_id = player_ids[1] if len(player_ids) > 1 else None
        one = match.players[one_id]
        two = match.players[two_id] if two_id is not None else None

        async with AsyncSessionFactory() as db:
            matches = DuoMatchRepository(db)
            if not await matches.claim_for_settlement(match.match_id):
                return MatchRewards.empty()

            changes = await self._apply_ratings(DuoRatingRepository(db), match, result)
            settlements = await GameSettlementService(
                profiles=GameProfileRepository(db),
                ledger=GoldTransactionRepository(db),
                items=ItemRepository(db),
                seasons=SeasonRepository(db),
                activity=ActivityRepository(db),
                achievements=AchievementService(AchievementRepository(db)),
            ).settle_match(
                match_id=match.match_id,
                rewards=_reward_inputs(match, result),
                # The season ladder mirrors the all-time rating that was just
                # applied, so it moves with the same Elo result.
                season_ratings={
                    user_id: change.after for user_id, change in changes.items()
                },
            )
            rewards = MatchRewards(
                rating=changes,
                exp={user_id: s.exp for user_id, s in settlements.items()},
                gold={user_id: s.gold for user_id, s in settlements.items()},
                loot={user_id: s.loot for user_id, s in settlements.items()},
                season={user_id: s.season for user_id, s in settlements.items()},
                streak={user_id: s.streak for user_id, s in settlements.items()},
            )

            record = await matches.get_by_id(match.match_id)
            if record is None:
                return rewards

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
                    "player_one_hp_left": one.hp,
                    "player_two_hp_left": two.hp if two is not None else STARTING_HP,
                    "finished_at": datetime.now(UTC),
                    "duration_seconds": result.duration_seconds,
                },
            )
            await matches.add_skill_uses(
                [
                    DuoMatchSkillUse(
                        match_id=match.match_id,
                        round_index=entry.round_index,
                        user_id=entry.user_id,
                        skill_id=entry.skill_id,
                        skill_code=entry.skill_code,
                        mana_spent=entry.mana_spent,
                    )
                    for entry in match.skill_log
                ]
            )
            return rewards

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


def _reward_inputs(match: LiveMatch, result: MatchResult) -> dict[str, RewardInput]:
    """One payout description per player, from what the match already knows."""
    return {
        user_id: RewardInput(
            outcome=outcome,
            end_reason=result.end_reason,
            forfeited=user_id == result.forfeit_user_id,
            correct_count=match.players[user_id].correct_count,
            knockout=result.knockout,
        )
        for user_id, outcome in result.outcome_by_user.items()
        if user_id in match.players
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
