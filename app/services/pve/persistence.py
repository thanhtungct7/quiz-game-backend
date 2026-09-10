"""Database access for the lesson-battle engine.

The engine holds no session of its own: a WebSocket stays open for the length
of a fight and must not pin a transaction for that long. Each of the few
moments that actually need the database opens a short session here.

The important one is `check_answer`. It goes through the very same
`ProgressService.check_answer` the ordinary lesson screen calls, so a battle
answer records an attempt, rolls the lesson's progress forward, refills energy
and extends the streak with no new code at the progress layer -- and so that
correctness has exactly one source of truth.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    InvalidAnswerSubmissionError,
)
from app.db.session import AsyncSessionFactory
from app.models.progress.user_lesson_progress import LessonProgressStatus
from app.models.pve.lesson_battle import BattleEndReason, BattleStatus, LessonBattle
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.course_repository import CourseRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.game.achievement_repository import AchievementRepository
from app.repository.game.activity_repository import ActivityRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.monster_repository import MonsterRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.repository.progress.user_progress_repository import UserProgressRepository
from app.repository.pve.lesson_battle_repository import LessonBattleRepository
from app.schemas.content.quiz import AnswerCheckResult, QuizSetWithAnswers
from app.schemas.pve.events import ErrorCode, LessonProgressChange
from app.services.content.quiz_service import QuizService
from app.services.game.achievement_service import AchievementService
from app.services.game.lesson_rewards import LessonRewardService
from app.services.game.loadout import PlayerLoadout
from app.services.game.loadout_builder import LoadoutBuilder
from app.services.game.settlement import (
    BattlePayout,
    ExpAward,
    GameSettlementService,
    GoldAward,
    LootDrop,
    StreakChange,
)
from app.services.progress.progress_service import ProgressService
from app.services.pve.catalog import resolve_for_lesson
from app.services.pve.monster import MonsterProfile
from app.services.pve.rewards import BattleRewardInput, compute_battle_reward, replayed
from app.services.pve.state import LiveBattle

# A path lesson holds about ten questions; the cap is only here so a bank
# lesson opened by mistake cannot turn into a hundred-round fight.
MAX_ROUNDS = 20


@dataclass(frozen=True)
class BattleSetup:
    """Everything resolved once, before round one, and read-only from then on."""

    monster: MonsterProfile
    loadout: PlayerLoadout
    questions: QuizSetWithAnswers
    lesson_title: str


@dataclass(frozen=True)
class BattleStartRejected:
    """The battle may not begin. Nothing was written."""

    reason: ErrorCode
    message: str


@dataclass(frozen=True)
class BattleResult:
    """The decided outcome of a battle, ready to be written down."""

    status: BattleStatus
    end_reason: BattleEndReason


@dataclass(frozen=True)
class BattleRewards:
    """Everything a finished battle paid out."""

    exp: ExpAward | None = None
    gold: GoldAward | None = None
    first_clear: bool = False
    loot: LootDrop | None = None
    streak: StreakChange | None = None

    @classmethod
    def empty(cls) -> "BattleRewards":
        return cls()


class BattlePersistence(Protocol):
    """What the engine needs from storage. Tests substitute a fake."""

    async def load_setup(
        self, user_id: str, lesson_id: str
    ) -> BattleSetup | BattleStartRejected: ...

    async def check_answer(
        self, user_id: str, challenge_id: str, option_ids: list[str]
    ) -> AnswerCheckResult | None: ...

    async def create_battle(self, battle: LiveBattle) -> None: ...

    async def save_result(self, battle: LiveBattle, result: BattleResult) -> BattleRewards: ...

    async def complete_lesson(self, user_id: str, lesson_id: str) -> None: ...

    async def lesson_progress(self, user_id: str, lesson_id: str) -> LessonProgressChange: ...


class DatabaseBattlePersistence:
    async def load_setup(self, user_id: str, lesson_id: str) -> BattleSetup | BattleStartRejected:
        """Draw the questions, pick the monster and resolve the player's build.

        All in one session, and all before the battle is announced: a fight
        that cannot be fought should fail at the door rather than half-open.
        """
        async with AsyncSessionFactory() as db:
            lessons = LessonRepository(db)
            lesson = await lessons.get_by_id(lesson_id)
            # A bank lesson is storage for leftover imported questions, not a
            # gate on the path, so nothing guards it.
            if lesson is None or lesson.is_bank:
                return BattleStartRejected(
                    reason=ErrorCode.LESSON_NOT_FOUND, message="Lesson not found"
                )

            questions = await QuizService(
                challenges=ChallengeRepository(db),
                lessons=lessons,
                units=UnitRepository(db),
            ).generate_for_battle(lesson_id, count=MAX_ROUNDS)
            if not questions.questions:
                return BattleStartRejected(
                    reason=ErrorCode.NO_QUESTIONS_AVAILABLE,
                    message="This lesson has no questions to fight with",
                )

            profile = await self._monster_for(db, lesson.unit_id, lesson.order_index)
            if profile is None:
                return BattleStartRejected(
                    reason=ErrorCode.MONSTER_UNAVAILABLE,
                    message="No monster is available for this lesson",
                )

            loadout = await LoadoutBuilder(
                profiles=GameProfileRepository(db),
                catalog=CatalogRepository(db),
                skills=UserSkillRepository(db),
                items=ItemRepository(db),
            ).build(user_id)

            return BattleSetup(
                monster=profile,
                loadout=loadout,
                questions=questions,
                lesson_title=lesson.title,
            )

    async def _monster_for(
        self, db: AsyncSession, unit_id: str, lesson_order_index: int
    ) -> MonsterProfile | None:
        """Which monster guards this lesson, derived rather than stored."""
        unit = await UnitRepository(db).get_by_id(unit_id)
        if unit is None:
            return None

        siblings = await LessonRepository(db).list_by_unit(unit_id)
        last_order = max((row.order_index for row in siblings), default=lesson_order_index)
        return resolve_for_lesson(
            await MonsterRepository(db).list_monsters(),
            unit_order_index=unit.order_index,
            lesson_order_index=lesson_order_index,
            last_order_index=last_order,
        )

    async def check_answer(
        self, user_id: str, challenge_id: str, option_ids: list[str]
    ) -> AnswerCheckResult | None:
        """Grade one answer through the ordinary study path.

        This is the join between the two features: the same call the lesson
        screen makes, so the battle's answers are study, not a parallel record
        of it. Returns None for an answer the content layer refuses, which the
        engine treats as a miss rather than as a crash.

        Both shapes of the answer are handed over and the service picks the one
        the challenge type calls for -- a single choice for SELECT and ASSIST,
        the whole sequence for ORDER.
        """
        async with AsyncSessionFactory() as db:
            service = ProgressService(
                progress=UserProgressRepository(db),
                challenges=ChallengeRepository(db),
                lessons=LessonRepository(db),
                units=UnitRepository(db),
                courses=CourseRepository(db),
                rewards=LessonRewardService(
                    profiles=GameProfileRepository(db),
                    activity=ActivityRepository(db),
                    achievements=AchievementService(AchievementRepository(db)),
                ),
            )
            try:
                return await service.check_answer(
                    user_id,
                    challenge_id,
                    option_ids[0] if len(option_ids) == 1 else None,
                    selected_option_ids=list(option_ids),
                )
            except (
                ChallengeNotFoundError,
                ChallengeOptionNotFoundError,
                InvalidAnswerSubmissionError,
            ):
                return None

    async def create_battle(self, battle: LiveBattle) -> None:
        async with AsyncSessionFactory() as db:
            await LessonBattleRepository(db).create(
                LessonBattle(
                    id=battle.battle_id,
                    user_id=battle.user_id,
                    lesson_id=battle.lesson_id,
                    monster_code=battle.monster.profile.code,
                    status=BattleStatus.IN_PROGRESS,
                    player_hp_left=battle.hp,
                    monster_hp_left=battle.monster.hp,
                    started_at=battle.started_wall or datetime.now(UTC),
                )
            )

    async def save_result(self, battle: LiveBattle, result: BattleResult) -> BattleRewards:
        """Finalize a battle: claim it, pay for a win, then write the row.

        The claim is what makes this safe to re-enter: the body below commits
        more than once, so without it a retry could pay twice. A caller that
        loses the claim writes nothing and reports no rewards.
        """
        async with AsyncSessionFactory() as db:
            battles = LessonBattleRepository(db)
            if not await battles.claim_for_settlement(battle.battle_id, result.status):
                return BattleRewards.empty()

            rewards = BattleRewards.empty()
            if result.status is BattleStatus.WON:
                rewards = await self._pay(db, battle)

            record = await battles.get_by_id(battle.battle_id)
            if record is None:
                return rewards

            await battles.finish(
                record,
                {
                    "end_reason": result.end_reason,
                    "player_hp_left": max(0, battle.hp),
                    "monster_hp_left": max(0, battle.monster.hp),
                    "rounds_played": battle.rounds_played,
                    "correct_count": battle.correct_count,
                    "best_combo": battle.best_combo,
                    "exp_awarded": rewards.exp.delta if rewards.exp else 0,
                    "gold_awarded": rewards.gold.delta if rewards.gold else 0,
                    "first_clear": rewards.first_clear,
                    "finished_at": datetime.now(UTC),
                },
            )
            return rewards

    async def _pay(self, db: AsyncSession, battle: LiveBattle) -> BattleRewards:
        """Hand over what the win was worth, at whichever of the two prices applies."""
        full = compute_battle_reward(
            BattleRewardInput(
                won=True,
                correct_count=battle.correct_count,
                is_boss=battle.monster.profile.is_boss,
                flawless=not battle.took_damage,
            )
        )
        share = replayed(full)
        settlement = await GameSettlementService(
            profiles=GameProfileRepository(db),
            ledger=GoldTransactionRepository(db),
            items=ItemRepository(db),
            activity=ActivityRepository(db),
            achievements=AchievementService(AchievementRepository(db)),
        ).settle_battle(
            user_id=battle.user_id,
            battle_id=battle.battle_id,
            lesson_id=battle.lesson_id,
            payout=BattlePayout(
                first_clear_exp=full.exp,
                first_clear_gold=full.gold,
                replay_exp=share.exp,
                replay_gold=share.gold,
            ),
            # Chests are a boss reward, so an ordinary gate stays worth doing
            # without turning the path into a slot machine.
            roll_chest=battle.monster.profile.is_boss,
        )
        return BattleRewards(
            exp=settlement.exp,
            gold=settlement.gold,
            first_clear=settlement.first_clear,
            loot=settlement.loot,
            streak=settlement.streak,
        )

    async def complete_lesson(self, user_id: str, lesson_id: str) -> None:
        """Clearing the gate finishes the lesson behind it.

        A fight ends when the monster falls, which is normally well before the
        pool has been exhausted -- so leaving completion to the mastered count
        would mean beating a gate and finding the next lesson still locked. The
        gate *is* the lesson on the learn path, so winning it completes the
        lesson, and the same first-completion payout the study path gives
        (energy, streak) is paid here exactly once.
        """
        async with AsyncSessionFactory() as db:
            await ProgressService(
                progress=UserProgressRepository(db),
                challenges=ChallengeRepository(db),
                lessons=LessonRepository(db),
                units=UnitRepository(db),
                courses=CourseRepository(db),
                rewards=LessonRewardService(
                    profiles=GameProfileRepository(db),
                    activity=ActivityRepository(db),
                    achievements=AchievementService(AchievementRepository(db)),
                ),
            ).mark_lesson_completed(user_id, lesson_id)

    async def lesson_progress(self, user_id: str, lesson_id: str) -> LessonProgressChange:
        """Where the lesson stands now that the battle's answers are in."""
        async with AsyncSessionFactory() as db:
            progress = UserProgressRepository(db)
            challenges = ChallengeRepository(db)
            row = await progress.get_lesson_progress(user_id, lesson_id)
            if row is None:
                return LessonProgressChange(
                    status=LessonProgressStatus.NOT_STARTED,
                    correct=0,
                    total=await challenges.count_by_lesson(lesson_id),
                )
            return LessonProgressChange(
                status=row.status,
                correct=row.correct_challenge_count,
                total=row.total_challenge_count,
            )
