"""The lesson-battle engine, driven end to end against a fake socket.

Two things every test here is really guarding.

One: a battle answer is a study answer. `FakePersistence.check_answer` stands in
for `ProgressService.check_answer`, and the calls it records are the proof that
fighting a monster writes the same progress the lesson screen would -- even
though the engine no longer waits for that call before swinging.

Two: the monster keeps its own time. Nothing the player does pauses it, and
`_force_swing` is how a test reaches into the fight to make its cast land now
rather than waiting five real seconds for it.
"""

import asyncio
from typing import Any

import pytest

from app.models.auth.user import User
from app.models.content.challenge import ChallengeDifficulty, ChallengeType
from app.models.game.skill import SkillEffect
from app.models.progress.user_lesson_progress import LessonProgressStatus
from app.models.pve.lesson_battle import BattleEndReason, BattleStatus
from app.schemas.content.course_content import ChallengeOptionPublicRead, ChallengePublicRead
from app.schemas.content.quiz import AnswerCheckResult, QuizSetWithAnswers
from app.schemas.pve.events import ErrorCode, LessonProgressChange, ServerEvent
from app.services.game.loadout import EquippedSkill, PlayerLoadout, default_loadout
from app.services.game.settlement import ExpAward, GoldAward
from app.services.pve import battle_runtime, clock
from app.services.pve import monster as monster_module
from app.services.pve.battle_runtime import BattleEngine
from app.services.pve.monster import MonsterProfile
from app.services.pve.persistence import (
    BattleResult,
    BattleRewards,
    BattleSetup,
    BattleStartRejected,
)
from app.services.pve.registry import BattleRegistry
from app.services.pve.state import LiveBattle

QUESTION_COUNT = 4
LESSON = "lesson-1"
EXP_PER_WIN = 50
GOLD_PER_WIN = 20


class FakeWebSocket:
    """Records what the engine sends instead of touching a real socket."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_json(self, payload: dict[str, Any]) -> None:
        # Yield to the event loop like a real socket write would.
        await asyncio.sleep(0)
        self.sent.append(payload)

    def first(self, event: ServerEvent) -> dict[str, Any] | None:
        for message in self.sent:
            if message["type"] == event.value:
                return message["data"]
        return None

    def last(self, event: ServerEvent) -> dict[str, Any] | None:
        found = [m["data"] for m in self.sent if m["type"] == event.value]
        return found[-1] if found else None

    def all_of(self, event: ServerEvent) -> list[dict[str, Any]]:
        return [m["data"] for m in self.sent if m["type"] == event.value]

    def errors(self) -> list[str]:
        return [data["code"] for data in self.all_of(ServerEvent.ERROR)]


def _monster(
    *,
    max_hp: int = 40,
    attack_damage: int = 10,
    enrage_after_rounds: int = 5,
    is_boss: bool = False,
) -> MonsterProfile:
    return MonsterProfile(
        code="SLIME",
        name="Slime",
        tier=1,
        max_hp=max_hp,
        attack_damage=attack_damage,
        damage_reduction_permille=0,
        enrage_after_rounds=enrage_after_rounds,
        enrage_multiplier_permille=1500,
        is_boss=is_boss,
        art_code="SLIME",
    )


def _question(index: int) -> ChallengePublicRead:
    challenge_id = f"q{index}"
    return ChallengePublicRead(
        id=challenge_id,
        lesson_id=LESSON,
        type=ChallengeType.SELECT,
        question=f"Question {index}",
        difficulty=ChallengeDifficulty.EASY,
        topic_id=None,
        order_index=index,
        passage=None,
        options=[
            ChallengeOptionPublicRead(
                id=f"{challenge_id}-correct",
                text="right",
                order_index=1,
                image_src=None,
                audio_src=None,
            ),
            ChallengeOptionPublicRead(
                id=f"{challenge_id}-wrong",
                text="wrong",
                order_index=2,
                image_src=None,
                audio_src=None,
            ),
            ChallengeOptionPublicRead(
                id=f"{challenge_id}-wrong-2",
                text="also wrong",
                order_index=3,
                image_src=None,
                audio_src=None,
            ),
        ],
    )


class FakePersistence:
    def __init__(
        self,
        *,
        question_count: int = QUESTION_COUNT,
        monster: MonsterProfile | None = None,
        loadout: PlayerLoadout | None = None,
        reject: BattleStartRejected | None = None,
    ) -> None:
        self.question_count = question_count
        self.monster = monster or _monster()
        self.loadout = loadout
        self.reject = reject
        self.created: list[LiveBattle] = []
        self.saved: list[tuple[LiveBattle, BattleResult]] = []
        # Every answer that reached the study path, in order.
        self.graded: list[tuple[str, str, str]] = []
        # Every lesson the engine asked to be marked finished, in order.
        self.completed: list[tuple[str, str]] = []
        self.progress = LessonProgressChange(
            status=LessonProgressStatus.IN_PROGRESS, correct=1, total=QUESTION_COUNT
        )

    async def load_setup(self, user_id: str, lesson_id: str) -> BattleSetup | BattleStartRejected:
        await asyncio.sleep(0)
        if self.reject is not None:
            return self.reject
        questions = [_question(index) for index in range(self.question_count)]
        return BattleSetup(
            monster=self.monster,
            loadout=self.loadout or default_loadout(user_id),
            questions=QuizSetWithAnswers(
                questions=questions,
                answer_key={q.id: [f"{q.id}-correct"] for q in questions},
                explanations={q.id: f"because {q.id}" for q in questions},
            ),
            lesson_title="Present simple",
        )

    async def check_answer(
        self, user_id: str, challenge_id: str, option_id: str
    ) -> AnswerCheckResult | None:
        # A real grade hits PostgreSQL and suspends here.
        await asyncio.sleep(0)
        self.graded.append((user_id, challenge_id, option_id))
        return AnswerCheckResult(
            challenge_id=challenge_id,
            selected_option_id=option_id,
            correct=option_id.endswith("-correct"),
            correct_option_ids=[f"{challenge_id}-correct"],
            explanation=f"because {challenge_id}",
        )

    async def create_battle(self, battle: LiveBattle) -> None:
        await asyncio.sleep(0)
        self.created.append(battle)

    async def save_result(self, battle: LiveBattle, result: BattleResult) -> BattleRewards:
        await asyncio.sleep(0)
        self.saved.append((battle, result))
        if result.status is not BattleStatus.WON:
            return BattleRewards.empty()
        return BattleRewards(
            exp=ExpAward(before=0, after=EXP_PER_WIN, level_before=1, level_after=1),
            gold=GoldAward(before=0, after=GOLD_PER_WIN),
            first_clear=True,
        )

    async def complete_lesson(self, user_id: str, lesson_id: str) -> None:
        await asyncio.sleep(0)
        self.completed.append((user_id, lesson_id))

    async def lesson_progress(self, user_id: str, lesson_id: str) -> LessonProgressChange:
        await asyncio.sleep(0)
        return self.progress


def _user(user_id: str = "alice") -> User:
    return User(id=user_id, email=f"{user_id}@example.com", username=user_id.upper())


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the simulation far faster than real time.

    The tick rate and the two lockouts are the engine's only pacing; the
    monster's cast interval is not patched, because a test that wants a blow
    should say so through `_force_swing` rather than race a timer.
    """
    monkeypatch.setattr(battle_runtime, "TICK_SECONDS", 0.005)
    monkeypatch.setattr(battle_runtime, "SNAPSHOT_SECONDS", 0.005)
    monkeypatch.setattr(battle_runtime, "CORRECT_LOCKOUT_MS", 5)
    monkeypatch.setattr(battle_runtime, "WRONG_LOCKOUT_MS", 5)


def _engine(persistence: FakePersistence | None = None) -> BattleEngine:
    return BattleEngine(
        persistence=persistence or FakePersistence(),  # type: ignore[arg-type]
        registry=BattleRegistry(),
    )


async def _settle(seconds: float = 0.08) -> None:
    await asyncio.sleep(seconds)


async def _start(engine: BattleEngine, user: User | None = None) -> tuple[FakeWebSocket, User]:
    player = user or _user()
    socket = FakeWebSocket()
    await engine.start_battle(player, socket, LESSON)  # type: ignore[arg-type]
    await _settle()
    return socket, player


def _battle(engine: BattleEngine, user: User) -> LiveBattle:
    battle = engine.registry.battle_of_user(user.id)
    assert battle is not None
    return battle


def _current_question(socket: FakeWebSocket) -> dict[str, Any]:
    data = socket.last(ServerEvent.QUESTION_PUSH)
    assert data is not None
    return data


async def _answer(
    engine: BattleEngine, user: User, socket: FakeWebSocket, *, correct: bool
) -> None:
    pushed = _current_question(socket)
    question_id = pushed["question"]["id"]
    suffix = "correct" if correct else "wrong"
    await engine.submit_answer(
        user.id,
        socket,  # type: ignore[arg-type]
        pushed["token"],
        f"{question_id}-{suffix}",
    )
    await _settle()


async def _force_swing(engine: BattleEngine, user: User, times: int = 1) -> None:
    """Bring the monster's cast forward to now, `times` over."""
    battle = _battle(engine, user)
    for _ in range(times):
        battle.monster.cast_ready_at = clock.seconds()
        await _settle(0.04)


# --- starting --------------------------------------------------------------


async def test_a_battle_opens_on_the_lesson_s_monster() -> None:
    engine = _engine()

    socket, user = await _start(engine)

    started = socket.first(ServerEvent.BATTLE_STARTED)
    assert started is not None
    assert started["lesson_id"] == LESSON
    assert started["lesson_title"] == "Present simple"
    assert started["monster"]["code"] == "SLIME"
    assert started["monster_hp"] == 40
    assert started["questions_in_pool"] == QUESTION_COUNT
    # The client needs both rates to know how much to interpolate.
    assert started["tick_hz"] > 0
    assert started["snapshot_hz"] > 0
    assert started["monster"]["cast_interval_ms"] > 0
    await engine.leave_battle(user.id)


async def test_the_first_question_arrives_without_being_asked_for() -> None:
    engine = _engine()

    socket, user = await _start(engine)

    assert len(socket.all_of(ServerEvent.QUESTION_PUSH)) == 1
    assert _current_question(socket)["pool_pass"] == 0
    await engine.leave_battle(user.id)


async def test_starting_a_second_battle_is_rejected() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    await engine.start_battle(user, socket, "lesson-2")  # type: ignore[arg-type]

    assert ErrorCode.BATTLE_ALREADY_ACTIVE.value in socket.errors()
    await engine.leave_battle(user.id)


async def test_a_lesson_with_nothing_to_fight_is_refused() -> None:
    persistence = FakePersistence(
        reject=BattleStartRejected(reason=ErrorCode.NO_QUESTIONS_AVAILABLE, message="empty")
    )
    engine = _engine(persistence)
    socket = FakeWebSocket()

    await engine.start_battle(_user(), socket, LESSON)  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.NO_QUESTIONS_AVAILABLE.value]
    assert persistence.created == []


async def test_a_question_never_arrives_with_its_answer() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    pushed = _current_question(socket)
    for option in pushed["question"]["options"]:
        assert "correct" not in option
    assert "correct_option_ids" not in pushed
    assert "explanation" not in pushed

    await engine.leave_battle(user.id)


# --- the clock -------------------------------------------------------------


async def test_the_monster_swings_without_the_player_doing_anything() -> None:
    """The whole point of the rewrite: no answer, and health is still lost."""
    engine = _engine(FakePersistence(monster=_monster(max_hp=500, attack_damage=10)))
    socket, user = await _start(engine)

    await _force_swing(engine, user)

    swing = socket.first(ServerEvent.MONSTER_SWING)
    assert swing is not None
    assert swing["damage"] == 10
    assert swing["your_hp"] == 90
    assert swing["swing_index"] == 1
    # And the next cast is already winding up.
    assert swing["cast_ends_at"] > 0
    await engine.leave_battle(user.id)


async def test_the_snapshot_carries_the_cast_deadline_not_a_progress_bar() -> None:
    engine = _engine(FakePersistence(monster=_monster(max_hp=500)))
    socket, user = await _start(engine)

    tick = socket.last(ServerEvent.STATE_TICK)
    assert tick is not None
    assert tick["cast_ends_at"] > tick["t"]
    assert tick["next_swing"] == "ATTACK"
    assert tick["next_swing_damage"] == 10
    assert tick["monster_max_hp"] == 500
    await engine.leave_battle(user.id)


async def test_a_monster_left_alive_long_enough_starts_hitting_harder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Rage is on the fight's clock now, so a player who answers nothing meets
    # the enraged monster just the same.
    monkeypatch.setattr(monster_module, "SECONDS_PER_ROUND", 0.05)
    engine = _engine(
        FakePersistence(monster=_monster(max_hp=500, attack_damage=10, enrage_after_rounds=2))
    )
    socket, user = await _start(engine)

    await _force_swing(engine, user)
    await _settle(0.15)
    await _force_swing(engine, user)

    swings = socket.all_of(ServerEvent.MONSTER_SWING)
    assert swings[0] == {
        "damage": 10,
        "enraged": False,
        "your_hp": 90,
        "swing_index": 1,
        "cast_ends_at": swings[0]["cast_ends_at"],
    }
    assert swings[-1]["enraged"] is True
    assert swings[-1]["damage"] == 15
    await engine.leave_battle(user.id)


# --- the join with the learn path ------------------------------------------


async def test_every_answer_goes_through_the_study_path() -> None:
    persistence = FakePersistence()
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=False)

    assert [option for _user_id, _challenge, option in persistence.graded] == [
        "q0-correct",
        "q1-wrong",
    ]
    assert all(user_id == user.id for user_id, _, _ in persistence.graded)
    await engine.leave_battle(user.id)


async def test_the_engine_grades_the_blow_itself_rather_than_waiting() -> None:
    """A tick cannot wait on a query, so the answer key decides the damage.

    The study path is still told; it is simply no longer in the way.
    """
    persistence = FakePersistence()
    engine = _engine(persistence)
    socket, user = await _start(engine)

    pushed = _current_question(socket)
    await engine.submit_answer(
        user.id,
        socket,
        pushed["token"],
        "q0-correct",  # type: ignore[arg-type]
    )

    # Resolved on the same coroutine, before the study path has run at all.
    result = socket.first(ServerEvent.ANSWER_RESULT)
    assert result is not None
    assert result["correct"] is True
    assert result["blow"]["final_damage"] > 0
    await engine.leave_battle(user.id)


async def test_a_recycled_question_does_not_mark_the_lesson_twice() -> None:
    persistence = FakePersistence(question_count=2, monster=_monster(max_hp=500))
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=True)

    pushes = socket.all_of(ServerEvent.QUESTION_PUSH)
    assert [push["pool_pass"] for push in pushes[:3]] == [0, 0, 1]
    # Three answers given, two questions in the lesson, two marks written.
    assert len(persistence.graded) == 2
    await engine.leave_battle(user.id)


async def test_the_finish_reports_where_the_lesson_now_stands() -> None:
    persistence = FakePersistence(monster=_monster(max_hp=20))
    persistence.progress = LessonProgressChange(
        status=LessonProgressStatus.COMPLETED, correct=4, total=4
    )
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _settle()

    finished = socket.first(ServerEvent.BATTLE_FINISHED)
    assert finished is not None
    assert finished["lesson_progress"] == {
        "status": LessonProgressStatus.COMPLETED.value,
        "correct": 4,
        "total": 4,
    }


# --- fighting --------------------------------------------------------------


async def test_a_correct_answer_hurts_the_monster_at_once() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)

    result = socket.first(ServerEvent.ANSWER_RESULT)
    assert result is not None
    assert result["blow"]["final_damage"] > 0
    assert result["monster_hp"] < 40
    assert result["combo"] == 1
    await engine.leave_battle(user.id)


async def test_a_wrong_answer_costs_tempo_and_the_combo_never_health() -> None:
    engine = _engine(FakePersistence(monster=_monster(max_hp=500)))
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=False)

    results = socket.all_of(ServerEvent.ANSWER_RESULT)
    assert results[-1]["blow"] is None
    assert results[-1]["combo"] == 0
    # Health is the monster's business alone, and it has not swung yet.
    tick = socket.last(ServerEvent.STATE_TICK)
    assert tick is not None
    assert tick["your_hp"] == tick["your_max_hp"]
    await engine.leave_battle(user.id)


async def test_dropping_the_monster_ends_the_battle() -> None:
    persistence = FakePersistence(monster=_monster(max_hp=20))
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _settle()

    finished = socket.first(ServerEvent.BATTLE_FINISHED)
    assert finished is not None
    assert finished["outcome"] == BattleStatus.WON.value
    assert finished["end_reason"] == BattleEndReason.MONSTER_DOWN.value
    assert finished["monster_hp_left"] == 0
    assert finished["exp"]["delta"] == EXP_PER_WIN
    assert finished["gold"]["delta"] == GOLD_PER_WIN
    assert finished["first_clear"] is True
    assert finished["answers_given"] == 1
    assert finished["duration_ms"] >= 0


async def test_dropping_the_monster_finishes_the_lesson_behind_the_gate() -> None:
    """The gate *is* the lesson on the path.

    A fight ends when the monster falls, which is normally long before every
    question in the pool has been answered correctly -- so completion cannot be
    left to the mastered count, or a player would clear a gate and find the next
    lesson still locked behind a monster that is already dead.
    """
    persistence = FakePersistence(monster=_monster(max_hp=20))
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _settle()

    assert socket.first(ServerEvent.BATTLE_FINISHED) is not None
    assert persistence.completed == [(user.id, LESSON)]


async def test_walking_out_does_not_finish_the_lesson() -> None:
    """Only winning clears the gate. Abandoning pays nothing and unlocks nothing,
    though the answers already given keep the mastery they earned."""
    persistence = FakePersistence(monster=_monster(max_hp=500))
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await engine.leave_battle(user.id)
    await _settle()

    assert socket.first(ServerEvent.BATTLE_FINISHED) is not None
    assert persistence.completed == []


async def test_running_out_of_questions_goes_round_again_instead_of_losing() -> None:
    engine = _engine(FakePersistence(question_count=2, monster=_monster(max_hp=500)))
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=True)
    await _answer(engine, user, socket, correct=True)

    # The lesson ran out; the fight did not.
    assert socket.first(ServerEvent.BATTLE_FINISHED) is None
    assert len(socket.all_of(ServerEvent.QUESTION_PUSH)) >= 4
    await engine.leave_battle(user.id)


async def test_running_out_of_health_is_a_loss() -> None:
    engine = _engine(FakePersistence(monster=_monster(max_hp=500, attack_damage=60)))
    socket, user = await _start(engine)

    await _force_swing(engine, user, times=2)

    finished = socket.first(ServerEvent.BATTLE_FINISHED)
    assert finished is not None
    assert finished["outcome"] == BattleStatus.LOST.value
    assert finished["end_reason"] == BattleEndReason.PLAYER_DOWN.value
    assert finished["your_hp_left"] == 0
    assert finished["monster_swings"] == 2
    # Losing pays nothing, and costs nothing.
    assert finished["exp"]["delta"] == 0
    assert finished["gold"]["delta"] == 0


# --- answering rules -------------------------------------------------------


async def test_the_same_token_cannot_be_answered_twice() -> None:
    engine = _engine(FakePersistence(monster=_monster(max_hp=500)))
    socket, user = await _start(engine)

    token = _current_question(socket)["token"]
    await engine.submit_answer(user.id, socket, token, "q0-correct")  # type: ignore[arg-type]
    await engine.submit_answer(user.id, socket, token, "q0-wrong")  # type: ignore[arg-type]

    assert ErrorCode.QUESTION_CLOSED.value in socket.errors()
    assert len(socket.all_of(ServerEvent.ANSWER_RESULT)) == 1
    await engine.leave_battle(user.id)


async def test_a_stale_token_is_rejected() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    await engine.submit_answer(user.id, socket, "not-a-token", "q0-correct")  # type: ignore[arg-type]

    assert ErrorCode.QUESTION_CLOSED.value in socket.errors()
    await engine.leave_battle(user.id)


async def test_an_option_from_another_question_is_rejected() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    token = _current_question(socket)["token"]
    await engine.submit_answer(user.id, socket, token, "q3-correct")  # type: ignore[arg-type]

    assert ErrorCode.INVALID_OPTION.value in socket.errors()
    await engine.leave_battle(user.id)


# --- skills ----------------------------------------------------------------


def _skill(
    code: str,
    effect: SkillEffect,
    magnitude: int,
    cost: int = 10,
    duration_rounds: int = 0,
) -> EquippedSkill:
    return EquippedSkill(
        skill_id=f"skill-{code}",
        code=code,
        name=code.title(),
        slot=0,
        effect=effect,
        mana_cost=cost,
        magnitude=magnitude,
        duration_rounds=duration_rounds,
    )


def _loadout(user_id: str, *skills: EquippedSkill) -> PlayerLoadout:
    base = default_loadout(user_id)
    return PlayerLoadout(
        user_id=base.user_id,
        level=base.level,
        class_code=base.class_code,
        max_hp=base.max_hp,
        starting_mana=50,
        damage_permille=base.damage_permille,
        skills=tuple(skills),
    )


async def test_a_shield_soaks_the_monster_s_blow() -> None:
    user = _user()
    engine = _engine(
        FakePersistence(
            monster=_monster(max_hp=500, attack_damage=20),
            loadout=_loadout(
                user.id,
                _skill("SHIELD", SkillEffect.DAMAGE_REDUCTION, 500, duration_rounds=1),
            ),
        )
    )
    socket, _ = await _start(engine, user)

    await engine.use_skill(user.id, socket, "SHIELD")  # type: ignore[arg-type]
    await _force_swing(engine, user)

    swing = socket.first(ServerEvent.MONSTER_SWING)
    assert swing is not None
    assert swing["damage"] == 10
    assert swing["your_hp"] == 90
    await engine.leave_battle(user.id)


async def test_stealing_time_pushes_the_monster_s_cast_back() -> None:
    """TIME_PENALTY had no target under the old rules. It has one now."""
    user = _user()
    engine = _engine(
        FakePersistence(
            monster=_monster(max_hp=500),
            loadout=_loadout(user.id, _skill("SLOW", SkillEffect.TIME_PENALTY, 5)),
        )
    )
    socket, _ = await _start(engine, user)
    before = _battle(engine, user).monster.cast_ready_at

    await engine.use_skill(user.id, socket, "SLOW")  # type: ignore[arg-type]

    assert _battle(engine, user).monster.cast_ready_at == pytest.approx(before + 5)
    used = socket.first(ServerEvent.SKILL_USED)
    assert used is not None
    assert used["mana_spent"] == 10
    await engine.leave_battle(user.id)


async def test_burning_mana_a_monster_does_not_have_is_refused_and_costs_nothing() -> None:
    user = _user()
    engine = _engine(
        FakePersistence(
            monster=_monster(max_hp=500),
            loadout=_loadout(user.id, _skill("BURN", SkillEffect.MANA_BURN, 20)),
        )
    )
    socket, _ = await _start(engine, user)

    await engine.use_skill(user.id, socket, "BURN")  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.SKILL_NO_TARGET.value]
    # The refusal happens before the cost, so the mana bar is untouched.
    assert _battle(engine, user).mana == 50
    await engine.leave_battle(user.id)


async def test_a_skill_that_is_not_equipped_is_refused() -> None:
    engine = _engine()
    socket, user = await _start(engine)

    await engine.use_skill(user.id, socket, "SHIELD")  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.SKILL_NOT_EQUIPPED.value]
    await engine.leave_battle(user.id)


async def test_a_skill_cannot_be_stacked_on_top_of_itself() -> None:
    user = _user()
    engine = _engine(
        FakePersistence(
            monster=_monster(max_hp=500),
            loadout=_loadout(user.id, _skill("HEAL", SkillEffect.HEAL, 5, cost=1)),
        )
    )
    socket, _ = await _start(engine, user)

    await engine.use_skill(user.id, socket, "HEAL")  # type: ignore[arg-type]
    await engine.use_skill(user.id, socket, "HEAL")  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.SKILL_ON_COOLDOWN.value]
    assert len(socket.all_of(ServerEvent.SKILL_USED)) == 1
    await engine.leave_battle(user.id)


async def test_removing_options_hides_wrong_ones_from_the_caster_only() -> None:
    user = _user()
    engine = _engine(
        FakePersistence(
            monster=_monster(max_hp=500),
            loadout=_loadout(user.id, _skill("REVEAL", SkillEffect.REMOVE_OPTIONS, 1)),
        )
    )
    socket, _ = await _start(engine, user)

    await engine.use_skill(user.id, socket, "REVEAL")  # type: ignore[arg-type]

    used = socket.first(ServerEvent.SKILL_USED)
    assert used is not None
    removed = used["private"]["removed_option_ids"]
    assert removed == ["q0-wrong"]
    # Never the answer, and never the last wrong option standing.
    assert "q0-correct" not in removed
    await engine.leave_battle(user.id)


# --- leaving ---------------------------------------------------------------


async def test_walking_out_abandons_the_battle() -> None:
    persistence = FakePersistence()
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await _answer(engine, user, socket, correct=True)
    await engine.leave_battle(user.id)

    _saved_battle, result = persistence.saved[-1]
    assert result.status is BattleStatus.ABANDONED
    assert result.end_reason is BattleEndReason.LEFT
    # The answer given before walking out keeps its progress.
    assert len(persistence.graded) == 1


async def test_a_dropped_socket_ends_the_battle() -> None:
    persistence = FakePersistence()
    engine = _engine(persistence)
    socket, user = await _start(engine)

    await engine.on_disconnect(user.id, socket)  # type: ignore[arg-type]

    _saved_battle, result = persistence.saved[-1]
    assert result.status is BattleStatus.ABANDONED
    assert engine.registry.battle_of_user(user.id) is None


async def test_a_stale_socket_does_not_end_a_battle_the_user_replaced() -> None:
    persistence = FakePersistence()
    engine = _engine(persistence)
    socket, user = await _start(engine)
    old_socket = FakeWebSocket()

    await engine.on_disconnect(user.id, old_socket)  # type: ignore[arg-type]

    assert persistence.saved == []
    assert engine.registry.battle_of_user(user.id) is not None
    await engine.leave_battle(user.id)
