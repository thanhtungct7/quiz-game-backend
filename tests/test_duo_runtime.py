"""The realtime duo engine, driven through fake sockets.

There are no rounds to step through here. Each player holds their own deck and
answers at their own pace, so a test drives one side as far as it likes without
touching the other -- which is the whole property these tests exist to pin
down.
"""

import asyncio
from typing import Any

import pytest

from app.models.auth.user import User
from app.models.content.challenge import ChallengeDifficulty, ChallengeType
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchStatus
from app.models.game.skill import SkillEffect
from app.schemas.content.course_content import ChallengeOptionPublicRead, ChallengePublicRead
from app.schemas.content.quiz import QuizSetWithAnswers
from app.schemas.duo.events import ErrorCode, ServerEvent
from app.services.duo import clock, match_runtime
from app.services.duo.match_runtime import DuoEngine
from app.services.duo.persistence import (
    MatchResult,
    MatchRewards,
    MatchStartRejected,
    RatingChange,
)
from app.services.duo.registry import DuoRegistry
from app.services.duo.scoring import MatchOutcome
from app.services.duo.state import LiveMatch, MatchSettings
from app.services.game.combat import COMBO_TIER_2, MAX_HP
from app.services.game.loadout import EquippedSkill, PlayerLoadout, default_loadout
from app.services.game.player_card import PlayerStanding
from app.services.game.settlement import ExpAward, GoldAward

# The speed reference an answer is scored against, not a deadline: nothing cuts
# a player off mid-question any more.
TIME_PER_QUESTION = 8
QUESTION_COUNT = 3
# Stand-in payouts; the real amounts are the concern of tests/test_rewards.py.
EXP_PER_MATCH = 40
GOLD_PER_MATCH = 20

# How many correct answers in a row it takes to knock a full-health opponent
# out, with room to spare. A deck this size is never cleared before the health
# bar runs out, which is what makes it the deck for the knockout tests.
KO_DECK = 8


class FakeWebSocket:
    """Records what the engine sends instead of touching a real socket."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def send_json(self, payload: dict[str, Any]) -> None:
        # Yield to the event loop like a real socket write would: without a
        # suspension point a fake can hide bugs that only bite when the engine
        # actually gives up control mid-operation.
        await asyncio.sleep(0)
        if self.closed:
            raise RuntimeError("socket closed")
        self.sent.append(payload)

    # --- assertions helpers ---

    def types(self) -> list[str]:
        return [message["type"] for message in self.sent]

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


class FakePersistence:
    def __init__(self, question_count: int = QUESTION_COUNT) -> None:
        self.question_count = question_count
        self.ratings: dict[str, int] = {}
        # Full standing override, for the tests that care about what a
        # player's card says rather than just their rating.
        self.standings: dict[str, PlayerStanding] = {}
        self.created: list[LiveMatch] = []
        self.saved: list[tuple[LiveMatch, MatchResult]] = []
        self.cancelled: list[LiveMatch] = []
        self.started: list[LiveMatch] = []
        self.refunded: list[LiveMatch] = []
        # Set to make the next start fail, without standing up an energy table.
        self.reject_start: MatchStartRejected | None = None
        # Per-user override so a test can hand a player a class and skills
        # without standing up the whole catalog.
        self.loadouts: dict[str, PlayerLoadout] = {}

    async def load_standing(self, user_id: str) -> PlayerStanding:
        await asyncio.sleep(0)
        return self.standings.get(
            user_id, PlayerStanding(rating=self.ratings.get(user_id, 1000))
        )

    async def draw_questions(self, settings: MatchSettings) -> QuizSetWithAnswers:
        await asyncio.sleep(0)
        questions = [_question(index) for index in range(self.question_count)]
        return QuizSetWithAnswers(
            questions=questions,
            answer_key={question.id: [f"{question.id}-correct"] for question in questions},
            explanations={question.id: f"because {question.id}" for question in questions},
        )

    async def start_players(
        self, match: LiveMatch
    ) -> dict[str, PlayerLoadout] | MatchStartRejected:
        await asyncio.sleep(0)
        if self.reject_start is not None:
            return self.reject_start
        self.started.append(match)
        return {
            user_id: self.loadouts.get(user_id, default_loadout(user_id))
            for user_id in match.player_ids
        }

    async def refund_start(self, match: LiveMatch) -> None:
        await asyncio.sleep(0)
        self.refunded.append(match)

    async def create_match(self, match: LiveMatch) -> None:
        await asyncio.sleep(0)
        self.created.append(match)

    async def save_result(self, match: LiveMatch, result: MatchResult) -> MatchRewards:
        # A real save hits PostgreSQL and suspends here.
        await asyncio.sleep(0)
        self.saved.append((match, result))
        one_id, two_id = match.player_ids[0], match.player_ids[1]
        outcome = result.outcome_by_user[one_id]
        delta = 16 if outcome is MatchOutcome.WIN else (-16 if outcome is MatchOutcome.LOSE else 0)
        return MatchRewards(
            rating={
                one_id: RatingChange(before=1000, after=1000 + delta),
                two_id: RatingChange(before=1000, after=1000 - delta),
            },
            exp={
                user_id: ExpAward(
                    before=0, after=EXP_PER_MATCH, level_before=1, level_after=1
                )
                for user_id in (one_id, two_id)
            },
            gold={
                user_id: GoldAward(before=0, after=GOLD_PER_MATCH)
                for user_id in (one_id, two_id)
            },
        )

    async def cancel_match(self, match: LiveMatch) -> None:
        await asyncio.sleep(0)
        self.cancelled.append(match)


def _question(index: int) -> ChallengePublicRead:
    challenge_id = f"q{index}"
    return ChallengePublicRead(
        id=challenge_id,
        lesson_id="lesson-1",
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
        ],
    )


def _user(user_id: str) -> User:
    return User(id=user_id, email=f"{user_id}@example.com", username=user_id.upper())


def _settings(question_count: int = QUESTION_COUNT) -> MatchSettings:
    return MatchSettings(
        question_count=question_count, time_per_question=TIME_PER_QUESTION
    )


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse every pause the loop is timed by, so a full match runs in
    milliseconds. The engine reads these off the module on every use, so
    patching them here is enough."""
    monkeypatch.setattr(clock, "COUNTDOWN_SECONDS", 0.01)
    monkeypatch.setattr(clock, "TICK_SECONDS", 0.002)
    monkeypatch.setattr(clock, "SNAPSHOT_SECONDS", 0.004)
    monkeypatch.setattr(clock, "CORRECT_LOCKOUT_MS", 4)
    monkeypatch.setattr(clock, "WRONG_LOCKOUT_MS", 8)
    monkeypatch.setattr(clock, "STUN_SECONDS", 0.5)
    monkeypatch.setattr(clock, "SECONDS_PER_ROUND", 0.2)
    monkeypatch.setattr(clock, "SKILL_MIN_COOLDOWN_SECONDS", 0.05)


@pytest.fixture
def engine() -> DuoEngine:
    return DuoEngine(persistence=FakePersistence(), registry=DuoRegistry())


async def _settle(seconds: float = 0.08) -> None:
    await asyncio.sleep(seconds)


async def _pair(
    engine: DuoEngine, question_count: int = QUESTION_COUNT
) -> tuple[FakeWebSocket, FakeWebSocket, User, User]:
    one, two = _user("alice"), _user("bob")
    socket_one, socket_two = FakeWebSocket(), FakeWebSocket()
    settings = _settings(question_count)
    await engine.join_queue(one, socket_one, settings)  # type: ignore[arg-type]
    await engine.join_queue(two, socket_two, settings)  # type: ignore[arg-type]
    return socket_one, socket_two, one, two


async def _engine_with(
    question_count: int,
) -> tuple[DuoEngine, FakeWebSocket, FakeWebSocket, User, User]:
    engine = DuoEngine(
        persistence=FakePersistence(question_count=question_count), registry=DuoRegistry()
    )
    socket_one, socket_two, one, two = await _pair(engine, question_count)
    return engine, socket_one, socket_two, one, two


def _current(socket: FakeWebSocket) -> dict[str, Any]:
    """The question this player has on screen right now."""
    data = socket.last(ServerEvent.QUESTION_PUSH)
    assert data is not None
    return data


async def _answer(
    engine: DuoEngine, user: User, socket: FakeWebSocket, *, correct: bool
) -> None:
    push = _current(socket)
    suffix = "correct" if correct else "wrong"
    await engine.submit_answer(
        user.id,
        socket,  # type: ignore[arg-type]
        push["token"],
        f"{push['question']['id']}-{suffix}",
    )


async def _play(
    engine: DuoEngine, user: User, socket: FakeWebSocket, *, correct: bool
) -> None:
    """Answer what is on screen and wait for the next question to arrive."""
    await _answer(engine, user, socket, correct=correct)
    await _settle(0.04)


async def _use(engine: DuoEngine, user: User, socket: FakeWebSocket, code: str) -> None:
    await engine.use_skill(user.id, socket, code)  # type: ignore[arg-type]


# --- matchmaking -----------------------------------------------------------


async def test_first_player_waits_and_second_pairs_with_them(engine: DuoEngine) -> None:
    socket_one, socket_two, one, two = await _pair(engine)

    assert socket_one.first(ServerEvent.QUEUE_WAITING) == {
        "position": 1,
        "waited_seconds": 0,
    }
    found_one = socket_one.first(ServerEvent.MATCH_FOUND)
    found_two = socket_two.first(ServerEvent.MATCH_FOUND)
    assert found_one is not None and found_two is not None
    assert found_one["opponent"]["id"] == two.id
    assert found_two["opponent"]["id"] == one.id
    assert found_one["match_id"] == found_two["match_id"]
    assert found_one["auto_start"] is True

    await engine.leave_match(one.id)


async def test_queueing_twice_is_rejected(engine: DuoEngine) -> None:
    user = _user("alice")
    socket = FakeWebSocket()
    await engine.join_queue(user, socket, _settings())  # type: ignore[arg-type]
    await engine.join_queue(user, socket, _settings())  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.ALREADY_IN_QUEUE.value]


async def test_players_wanting_different_settings_are_not_paired(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two = FakeWebSocket(), FakeWebSocket()
    await engine.join_queue(
        _user("alice"), socket_one, _settings()  # type: ignore[arg-type]
    )
    await engine.join_queue(
        _user("bob"),
        socket_two,  # type: ignore[arg-type]
        MatchSettings(question_count=QUESTION_COUNT, time_per_question=30),
    )

    assert socket_one.first(ServerEvent.MATCH_FOUND) is None
    assert socket_two.first(ServerEvent.QUEUE_WAITING) is not None


# --- the deck --------------------------------------------------------------


async def test_the_match_opens_with_both_decks_full(engine: DuoEngine) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    started = socket_one.first(ServerEvent.MATCH_STARTED)
    assert started is not None
    assert started["deck_size"] == QUESTION_COUNT
    assert started["speed_reference_seconds"] == TIME_PER_QUESTION
    assert started["your_hp"] == MAX_HP
    assert started["opponent_hp"] == MAX_HP
    assert started["deadline_at"] > started["server_time_ms"]

    # Both players open on the same question, in the same order.
    assert _current(socket_one)["question"]["id"] == _current(socket_two)["question"]["id"]
    assert _current(socket_one)["deck_remaining"] == QUESTION_COUNT
    assert _current(socket_one)["retry"] is False

    await engine.leave_match(one.id)


async def test_a_pushed_question_never_reveals_the_answer(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    push = _current(socket_one)
    for option in push["question"]["options"]:
        assert "correct" not in option
    assert "correct_option_ids" not in push
    assert "explanation" not in push

    await engine.leave_match(one.id)


async def test_the_faster_player_moves_on_without_the_other(engine: DuoEngine) -> None:
    """The point of the whole rewrite: nobody waits for anybody."""
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    await _play(engine, one, socket_one, correct=True)
    await _play(engine, one, socket_one, correct=True)

    # Three questions handed out to the player who answered, one to the player
    # who has not touched their screen.
    assert len(socket_one.all_of(ServerEvent.QUESTION_PUSH)) == 3
    assert len(socket_two.all_of(ServerEvent.QUESTION_PUSH)) == 1
    assert _current(socket_one)["deck_remaining"] == 1
    assert _current(socket_two)["deck_remaining"] == QUESTION_COUNT

    await engine.leave_match(one.id)


async def test_a_wrong_answer_comes_round_again_at_the_back_of_the_deck(
    engine: DuoEngine,
) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    first_id = _current(socket_one)["question"]["id"]
    await _play(engine, one, socket_one, correct=False)
    # The deck is not shorter for having been wrong: the same question is
    # still owed, it has just gone to the back.
    assert _current(socket_one)["deck_remaining"] == QUESTION_COUNT
    assert _current(socket_one)["question"]["id"] != first_id

    await _play(engine, one, socket_one, correct=True)
    await _play(engine, one, socket_one, correct=True)

    back_again = _current(socket_one)
    assert back_again["question"]["id"] == first_id
    assert back_again["retry"] is True
    assert back_again["deck_remaining"] == 1

    await engine.leave_match(one.id)


async def test_answering_the_same_question_twice_is_rejected(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    push = _current(socket_one)
    await engine.submit_answer(
        one.id, socket_one, push["token"], f"{push['question']['id']}-correct"  # type: ignore[arg-type]
    )
    await engine.submit_answer(
        one.id, socket_one, push["token"], f"{push['question']['id']}-wrong"  # type: ignore[arg-type]
    )

    assert ErrorCode.QUESTION_CLOSED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_answering_a_stale_token_is_rejected(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    push = _current(socket_one)
    await engine.submit_answer(
        one.id, socket_one, "not-the-token", f"{push['question']['id']}-correct"  # type: ignore[arg-type]
    )

    assert ErrorCode.QUESTION_CLOSED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_an_option_from_another_question_is_rejected(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    await engine.submit_answer(
        one.id, socket_one, _current(socket_one)["token"], "not-an-option"  # type: ignore[arg-type]
    )

    assert ErrorCode.INVALID_OPTION.value in socket_one.errors()
    await engine.leave_match(one.id)


# --- blows -----------------------------------------------------------------


async def test_a_correct_answer_lands_its_blow_at_once(engine: DuoEngine) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    await _answer(engine, one, socket_one, correct=True)

    result = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert result is not None
    assert result["correct"] is True
    assert result["blow"]["damage"] > 0
    assert result["opponent_hp"] < MAX_HP
    assert result["points"] > 0
    assert result["your_mana"] > 0
    assert result["your_combo"] == 1
    assert result["explanation"] == "because q0"

    # The other side is told the moment it lands, not at the end of a round --
    # and they have not answered anything yet.
    landed = socket_two.last(ServerEvent.OPPONENT_ANSWERED)
    assert landed is not None
    assert landed["damage"] == result["blow"]["damage"]
    assert landed["your_hp"] == result["opponent_hp"]
    assert socket_two.last(ServerEvent.ANSWER_RESULT) is None

    await engine.leave_match(one.id)


async def test_a_wrong_answer_lands_nothing_and_costs_no_health(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    await _answer(engine, one, socket_one, correct=False)

    result = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert result is not None
    assert result["blow"] is None
    assert result["points"] == 0
    assert result["opponent_hp"] == MAX_HP
    # Being wrong costs tempo and the combo, never health.
    tick = socket_one.last(ServerEvent.STATE_TICK)
    assert tick is not None
    assert tick["your_hp"] == MAX_HP
    assert socket_two.last(ServerEvent.OPPONENT_ANSWERED)["damage"] == 0  # type: ignore[index]

    await engine.leave_match(one.id)


async def test_a_miss_resets_the_combo(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    for correct in (True, True, False):
        await _play(engine, one, socket_one, correct=correct)

    combos = [data["your_combo"] for data in socket_one.all_of(ServerEvent.ANSWER_RESULT)]
    assert combos == [1, 2, 0]

    await engine.leave_match(one.id)


async def test_a_snapshot_reports_both_sides(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()
    await _play(engine, one, socket_one, correct=True)

    tick = socket_one.last(ServerEvent.STATE_TICK)
    assert tick is not None
    assert tick["your_hp"] == MAX_HP
    assert tick["opponent_hp"] < MAX_HP
    assert tick["your_deck_remaining"] == QUESTION_COUNT - 1
    assert tick["opponent_deck_remaining"] == QUESTION_COUNT
    assert tick["your_score"] > 0
    assert tick["opponent_score"] == 0

    await engine.leave_match(one.id)


# --- how a match ends ------------------------------------------------------


async def test_clearing_the_deck_wins_the_match(engine: DuoEngine) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["end_reason"] == DuoMatchEndReason.DECK_CLEARED.value
    assert finished_one["result"] == MatchOutcome.WIN.value
    assert finished_two["result"] == MatchOutcome.LOSE.value
    assert finished_one["your_deck_cleared"] is True
    assert finished_two["your_deck_cleared"] is False
    assert finished_one["your_correct"] == QUESTION_COUNT
    # Won on the deck rather than the health bar: the loser is still standing.
    assert finished_two["your_hp_left"] > 0
    assert finished_one["rating"]["delta"] == -finished_two["rating"]["delta"]
    assert finished_one["exp"]["delta"] == EXP_PER_MATCH
    assert finished_one["gold"]["delta"] == GOLD_PER_MATCH


async def test_clearing_the_deck_beats_a_healthier_opponent(engine: DuoEngine) -> None:
    """Answering every question right is the race; health is the fight. The
    race wins."""
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    # Walk in badly beaten up but still able to finish the deck -- the state
    # this rule exists to decide.
    match.players[one.id].hp = 5

    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None
    assert finished_one["end_reason"] == DuoMatchEndReason.DECK_CLEARED.value
    assert finished_one["result"] == MatchOutcome.WIN.value
    assert finished_one["your_hp_left"] < finished_one["opponent_hp_left"]
    assert socket_two.last(ServerEvent.MATCH_FINISHED) is not None


async def test_running_the_opponent_to_zero_ends_the_match() -> None:
    engine, socket_one, socket_two, one, _two = await _engine_with(KO_DECK)
    await _settle()

    for _ in range(KO_DECK):
        if socket_one.last(ServerEvent.MATCH_FINISHED) is not None:
            break
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["end_reason"] == DuoMatchEndReason.KNOCKOUT.value
    assert finished_one["result"] == MatchOutcome.WIN.value
    assert finished_two["result"] == MatchOutcome.LOSE.value
    assert finished_one["opponent_hp_left"] == 0
    assert finished_one["your_hp_left"] > 0
    # The knockout arrives before the deck runs out, so nobody cleared it.
    assert finished_one["your_deck_cleared"] is False


async def test_the_killing_blow_is_announced_before_the_match_ends() -> None:
    engine, socket_one, socket_two, one, _two = await _engine_with(KO_DECK)
    await _settle()

    for _ in range(KO_DECK):
        if socket_one.last(ServerEvent.MATCH_FINISHED) is not None:
            break
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    # The client can animate the blow that ended it instead of the bar jumping
    # to zero on the summary screen.
    landed = socket_two.last(ServerEvent.OPPONENT_ANSWERED)
    assert landed is not None
    assert landed["your_hp"] == 0
    assert landed["damage"] > 0


async def test_running_out_of_time_is_a_draw_when_neither_played(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    # Bring the cap forward rather than waiting it out: the deadline is what
    # is under test, not how long it is.
    match.deadline_at = clock.seconds()
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["end_reason"] == DuoMatchEndReason.TIME_UP.value
    assert finished_one["result"] == MatchOutcome.DRAW.value
    assert finished_two["result"] == MatchOutcome.DRAW.value
    assert finished_one["your_hp_left"] == MAX_HP
    assert finished_one["opponent_hp_left"] == MAX_HP


async def test_running_out_of_time_decides_on_health(engine: DuoEngine) -> None:
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()
    await _play(engine, one, socket_one, correct=True)

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    match.deadline_at = clock.seconds()
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["result"] == MatchOutcome.WIN.value
    assert finished_two["result"] == MatchOutcome.LOSE.value


async def test_a_forfeiting_player_is_the_only_one_marked_as_such(
    engine: DuoEngine,
) -> None:
    socket_one, _socket_two, _one, two = await _pair(engine)
    await _settle()

    await engine.leave_match(two.id)
    await _settle(0.2)

    assert len(engine.persistence.saved) == 1  # type: ignore[attr-defined]
    _match, result = engine.persistence.saved[0]  # type: ignore[attr-defined]
    # Only the quitter pays the penalty; the player left behind is a winner.
    assert result.forfeit_user_id == two.id
    assert socket_one.last(ServerEvent.MATCH_FINISHED) is not None


async def test_aborting_a_settled_match_does_not_cancel_it(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    match = engine.persistence.saved[0][0]  # type: ignore[attr-defined]
    assert match.status is DuoMatchStatus.FINISHED
    await engine._abort(match)

    # The payout stands: no cancel is written over a finished match.
    assert engine.persistence.cancelled == []  # type: ignore[attr-defined]
    assert match.status is DuoMatchStatus.FINISHED


async def test_a_match_that_was_never_persisted_reports_no_payout() -> None:
    engine, socket_one, _socket_two, one, _two = await _engine_with(question_count=1)
    await _settle(0.2)

    # Too few questions to play: aborted before anything was written, so there
    # is nothing to pay out and no zeroed-out award to misread as one.
    assert ErrorCode.NO_QUESTIONS_AVAILABLE.value in socket_one.errors()
    assert engine.persistence.saved == []  # type: ignore[attr-defined]
    assert engine.persistence.created == []  # type: ignore[attr-defined]
    # Nothing was charged either: the question draw is checked before the
    # players are started, so an unplayable match costs nobody anything.
    assert engine.persistence.started == []  # type: ignore[attr-defined]
    assert socket_one.last(ServerEvent.MATCH_FINISHED) is None
    assert engine.registry.match_of_user(one.id) is None


async def test_a_finished_match_frees_both_players(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, two = await _pair(engine)
    await _settle()

    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    assert engine.registry.match_of_user(one.id) is None
    assert engine.registry.match_of_user(two.id) is None


# --- disconnects -----------------------------------------------------------


async def test_dropping_out_of_a_running_match_warns_the_opponent(
    engine: DuoEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_runtime, "DISCONNECT_GRACE_SECONDS", 60)
    socket_one, socket_two, one, two = await _pair(engine)
    await _settle()

    await engine.on_disconnect(one.id, socket_one)  # type: ignore[arg-type]

    warning = socket_two.last(ServerEvent.OPPONENT_DISCONNECTED)
    assert warning == {"grace_seconds": 60}
    assert engine.registry.match_of_user(two.id) is not None

    await engine.leave_match(two.id)


async def test_never_coming_back_forfeits_the_match(
    engine: DuoEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_runtime, "DISCONNECT_GRACE_SECONDS", 1)
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    await engine.on_disconnect(one.id, socket_one)  # type: ignore[arg-type]
    await _settle(1.3)

    finished = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished is not None
    assert finished["result"] == MatchOutcome.WIN.value
    assert finished["end_reason"] == DuoMatchEndReason.OPPONENT_TIMEOUT.value


async def test_reconnecting_within_grace_resumes_the_match(
    engine: DuoEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(match_runtime, "DISCONNECT_GRACE_SECONDS", 60)
    socket_one, socket_two, one, two = await _pair(engine)
    await _settle()
    await _play(engine, one, socket_one, correct=True)

    await engine.on_disconnect(one.id, socket_one)  # type: ignore[arg-type]
    replacement = FakeWebSocket()
    await engine.on_connect(one, replacement)  # type: ignore[arg-type]

    resume = replacement.first(ServerEvent.MATCH_RESUME)
    assert resume is not None
    assert resume["opponent"]["id"] == two.id
    assert resume["your_score"] > 0
    assert resume["your_deck_remaining"] == QUESTION_COUNT - 1
    assert resume["opponent_deck_remaining"] == QUESTION_COUNT
    assert resume["opponent_hp"] < MAX_HP
    assert resume["your_mana"] > 0
    assert resume["your_combo"] == 1
    # The question that was on screen comes back with the token that answers
    # it, so the reconnected client can carry on where it left off.
    assert resume["token"] is not None
    assert resume["question"] is not None
    assert socket_two.last(ServerEvent.OPPONENT_RECONNECTED) is not None

    await engine.leave_match(one.id)


async def test_leaving_on_purpose_hands_the_win_to_the_opponent(
    engine: DuoEngine,
) -> None:
    _socket_one, socket_two, one, _two = await _pair(engine)
    await _settle()

    await engine.leave_match(one.id)
    await _settle()

    finished = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished is not None
    assert finished["result"] == MatchOutcome.WIN.value
    assert finished["end_reason"] == DuoMatchEndReason.OPPONENT_LEFT.value


async def test_a_lobby_that_loses_a_player_is_cancelled(engine: DuoEngine) -> None:
    host = _user("alice")
    socket = FakeWebSocket()
    await engine.create_room(host, socket, _settings())  # type: ignore[arg-type]

    await engine.on_disconnect(host.id, socket)  # type: ignore[arg-type]

    assert engine.registry.match_of_user(host.id) is None


# --- friend rooms ----------------------------------------------------------


async def test_friend_room_flow_from_code_to_first_question(engine: DuoEngine) -> None:
    host, guest = _user("alice"), _user("bob")
    socket_host, socket_guest = FakeWebSocket(), FakeWebSocket()

    await engine.create_room(host, socket_host, _settings())  # type: ignore[arg-type]
    created = socket_host.first(ServerEvent.ROOM_CREATED)
    assert created is not None
    code = created["room_code"]
    assert len(code) == 6

    await engine.join_room(guest, socket_guest, code)  # type: ignore[arg-type]
    found = socket_guest.first(ServerEvent.MATCH_FOUND)
    assert found is not None
    assert found["auto_start"] is False
    assert found["host_id"] == host.id

    await engine.start_match(host.id, socket_host)  # type: ignore[arg-type]
    await _settle()

    assert socket_guest.first(ServerEvent.QUESTION_PUSH) is not None
    await engine.leave_match(host.id)


async def test_only_the_host_may_start(engine: DuoEngine) -> None:
    host, guest = _user("alice"), _user("bob")
    socket_host, socket_guest = FakeWebSocket(), FakeWebSocket()
    await engine.create_room(host, socket_host, _settings())  # type: ignore[arg-type]
    created = socket_host.first(ServerEvent.ROOM_CREATED)
    assert created is not None
    await engine.join_room(guest, socket_guest, created["room_code"])  # type: ignore[arg-type]

    await engine.start_match(guest.id, socket_guest)  # type: ignore[arg-type]

    assert ErrorCode.NOT_HOST.value in socket_guest.errors()
    await engine.leave_match(host.id)


async def test_starting_alone_is_rejected(engine: DuoEngine) -> None:
    host = _user("alice")
    socket = FakeWebSocket()
    await engine.create_room(host, socket, _settings())  # type: ignore[arg-type]

    await engine.start_match(host.id, socket)  # type: ignore[arg-type]

    assert ErrorCode.NOT_ENOUGH_PLAYERS.value in socket.errors()


async def test_an_unknown_room_code_is_reported(engine: DuoEngine) -> None:
    socket = FakeWebSocket()
    await engine.join_room(_user("bob"), socket, "ZZZZZZ")  # type: ignore[arg-type]

    assert socket.errors() == [ErrorCode.ROOM_NOT_FOUND.value]


async def test_a_third_player_cannot_join_a_full_room(engine: DuoEngine) -> None:
    host, guest, extra = _user("alice"), _user("bob"), _user("carol")
    socket_host, socket_guest, socket_extra = (
        FakeWebSocket(),
        FakeWebSocket(),
        FakeWebSocket(),
    )
    await engine.create_room(host, socket_host, _settings())  # type: ignore[arg-type]
    created = socket_host.first(ServerEvent.ROOM_CREATED)
    assert created is not None
    await engine.join_room(guest, socket_guest, created["room_code"])  # type: ignore[arg-type]

    await engine.join_room(extra, socket_extra, created["room_code"])  # type: ignore[arg-type]

    assert socket_extra.errors() == [ErrorCode.ROOM_FULL.value]
    await engine.leave_match(host.id)


# --- content shortage ------------------------------------------------------


async def test_a_match_without_enough_questions_is_aborted() -> None:
    engine, socket_one, socket_two, one, _two = await _engine_with(question_count=1)
    await _settle()

    assert ErrorCode.NO_QUESTIONS_AVAILABLE.value in socket_one.errors()
    assert ErrorCode.NO_QUESTIONS_AVAILABLE.value in socket_two.errors()
    assert engine.registry.match_of_user(one.id) is None


async def test_an_aborted_match_is_never_persisted() -> None:
    engine, _socket_one, _socket_two, _one, _two = await _engine_with(question_count=1)
    await _settle()

    assert engine.persistence.created == []  # type: ignore[attr-defined]
    assert engine.persistence.saved == []  # type: ignore[attr-defined]


async def test_the_players_are_started_exactly_once(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()
    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    assert len(engine.persistence.started) == 1  # type: ignore[attr-defined]


async def test_a_played_match_is_persisted_once(engine: DuoEngine) -> None:
    persistence = engine.persistence
    assert isinstance(persistence, FakePersistence)
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    assert len(persistence.created) == 1
    assert len(persistence.saved) == 1
    saved_match, result = persistence.saved[0]
    assert saved_match.status is DuoMatchStatus.FINISHED
    assert result.winner_id == one.id


# --- combat ----------------------------------------------------------------


async def test_a_five_hit_combo_stuns_the_opponent() -> None:
    engine, socket_one, socket_two, one, two = await _engine_with(KO_DECK)
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    # Walk in on the brink of the combo tier, with an opponent healthy enough
    # to survive the critical -- otherwise the knockout lands first.
    match.players[one.id].combo = COMBO_TIER_2 - 1
    match.players[two.id].hp = MAX_HP

    await _answer(engine, one, socket_one, correct=True)

    crit = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert crit is not None
    assert crit["blow"]["is_critical"] is True
    assert crit["blow"]["stuns_opponent"] is True
    assert match.players[two.id].hp > 0
    assert match.players[two.id].is_stunned(clock.seconds())

    # The stunned player is told, and is refused if they try to answer anyway.
    landed = socket_two.last(ServerEvent.OPPONENT_ANSWERED)
    assert landed is not None
    assert landed["your_stunned_until"] > 0
    await _answer(engine, two, socket_two, correct=True)
    assert ErrorCode.STUNNED.value in socket_two.errors()

    await engine.leave_match(one.id)


async def test_a_stunned_player_is_handed_no_new_questions() -> None:
    engine, socket_one, socket_two, one, two = await _engine_with(KO_DECK)
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    await _play(engine, two, socket_two, correct=True)
    pushes_before = len(socket_two.all_of(ServerEvent.QUESTION_PUSH))

    # Stun them between two questions: the next one has to wait it out.
    match.players[two.id].question_token = None
    match.players[two.id].current_index = None
    match.players[two.id].stunned_until = clock.seconds() + 5
    await _settle(0.1)

    assert len(socket_two.all_of(ServerEvent.QUESTION_PUSH)) == pushes_before
    await engine.leave_match(one.id)


async def test_a_class_sets_the_starting_health_and_mana() -> None:
    persistence = FakePersistence(question_count=4)
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    socket_one, _socket_two, one, two = await _pair(engine, question_count=4)
    persistence.loadouts = {
        one.id: _loadout(one.id, max_hp=130, starting_mana=10),
        two.id: _loadout(two.id, max_hp=80, starting_mana=30),
    }
    await _settle()

    started = socket_one.first(ServerEvent.MATCH_STARTED)
    assert started is not None
    assert started["your_hp"] == 130
    assert started["your_max_hp"] == 130
    assert started["opponent_hp"] == 80
    assert started["your_mana"] == 10

    await engine.leave_match(one.id)


async def test_a_player_with_no_class_still_plays_on_baseline_stats(
    engine: DuoEngine,
) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()

    started = socket_one.first(ServerEvent.MATCH_STARTED)
    assert started is not None
    assert started["your_hp"] == MAX_HP
    assert started["your_mana"] == 0

    await engine.leave_match(one.id)


async def test_a_streak_is_folded_into_the_health_a_player_starts_with() -> None:
    persistence = FakePersistence(question_count=4)
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    socket_one, _socket_two, one, two = await _pair(engine, question_count=4)
    # A loadout arrives with the buff already applied; the engine reads three
    # finished numbers and never has to know where they came from.
    persistence.loadouts = {
        one.id: _loadout(one.id, max_hp=MAX_HP + 15, starting_mana=10),
        two.id: _loadout(two.id, max_hp=MAX_HP, starting_mana=0),
    }
    await _settle()

    started = socket_one.first(ServerEvent.MATCH_STARTED)
    assert started is not None
    assert started["your_hp"] == MAX_HP + 15
    assert started["opponent_hp"] == MAX_HP

    await engine.leave_match(one.id)


# --- skills ----------------------------------------------------------------


def _equipped(
    effect: SkillEffect,
    *,
    code: str = "TEST_SKILL",
    mana_cost: int = 10,
    magnitude: int = 0,
    duration_rounds: int = 0,
    slot: int = 0,
) -> EquippedSkill:
    return EquippedSkill(
        skill_id=f"skill-{code}",
        code=code,
        name=code.title(),
        slot=slot,
        effect=effect,
        mana_cost=mana_cost,
        magnitude=magnitude,
        duration_rounds=duration_rounds,
    )


def _loadout(
    user_id: str,
    *skills: EquippedSkill,
    max_hp: int = MAX_HP,
    starting_mana: int = 100,
    damage_permille: int = 1000,
) -> PlayerLoadout:
    return PlayerLoadout(
        user_id=user_id,
        level=10,
        class_code="TESTER",
        max_hp=max_hp,
        starting_mana=starting_mana,
        damage_permille=damage_permille,
        skills=skills,
    )


async def _pair_with_skills(
    *,
    one_skills: tuple[EquippedSkill, ...] = (),
    two_skills: tuple[EquippedSkill, ...] = (),
    question_count: int = KO_DECK,
    one_max_hp: int = MAX_HP,
    two_max_hp: int = MAX_HP,
) -> tuple[DuoEngine, FakeWebSocket, FakeWebSocket, User, User]:
    persistence = FakePersistence(question_count=question_count)
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    socket_one, socket_two, one, two = await _pair(engine, question_count)
    persistence.loadouts = {
        one.id: _loadout(one.id, *one_skills, max_hp=one_max_hp),
        two.id: _loadout(two.id, *two_skills, max_hp=two_max_hp),
    }
    return engine, socket_one, socket_two, one, two


async def test_a_skill_costs_mana_and_is_announced_to_both_players() -> None:
    skill = _equipped(SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=40, magnitude=500)
    engine, socket_one, socket_two, one, _two = await _pair_with_skills(
        one_skills=(skill,)
    )
    await _settle()

    await _use(engine, one, socket_one, "SHIELD")

    used_one = socket_one.last(ServerEvent.SKILL_USED)
    used_two = socket_two.last(ServerEvent.SKILL_USED)
    assert used_one is not None and used_two is not None
    assert used_one["skill_code"] == "SHIELD"
    assert used_one["user_id"] == one.id
    assert used_one["your_mana"] == 100 - 40
    assert used_one["ready_again_at"] > 0
    # Both sides know what was cast; only the caster is told when it recharges.
    assert used_two["skill_code"] == "SHIELD"
    assert used_two["ready_again_at"] == 0

    await engine.leave_match(one.id)


async def test_an_unequipped_skill_is_refused() -> None:
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills()
    await _settle()

    await _use(engine, one, socket_one, "SHIELD")

    assert ErrorCode.SKILL_NOT_EQUIPPED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_a_skill_without_the_mana_is_refused() -> None:
    skill = _equipped(SkillEffect.HEAL, code="BIG_HEAL", mana_cost=90, magnitude=20)
    persistence = FakePersistence(question_count=4)
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    socket_one, _socket_two, one, two = await _pair(engine, question_count=4)
    persistence.loadouts = {
        one.id: _loadout(one.id, skill, starting_mana=10),
        two.id: _loadout(two.id),
    }
    await _settle()

    await _use(engine, one, socket_one, "BIG_HEAL")

    assert ErrorCode.NOT_ENOUGH_MANA.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_a_skill_cannot_be_recast_before_it_recharges() -> None:
    skill = _equipped(SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=10, magnitude=500)
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(skill,)
    )
    await _settle()

    await _use(engine, one, socket_one, "SHIELD")
    await _use(engine, one, socket_one, "SHIELD")

    assert ErrorCode.SKILL_ON_COOLDOWN.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_a_shield_halves_the_next_blow_taken() -> None:
    shield = _equipped(
        SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=10, magnitude=500
    )
    engine, socket_one, socket_two, one, two = await _pair_with_skills(
        two_skills=(shield,)
    )
    await _settle()

    await _play(engine, one, socket_one, correct=True)
    plain = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert plain is not None
    plain_damage = plain["blow"]["damage"]

    await _use(engine, two, socket_two, "SHIELD")
    await _play(engine, one, socket_one, correct=True)

    shielded = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert shielded is not None
    # Combo also rose between the two, so the shielded blow can only be
    # compared against half of a blow that was already smaller.
    assert shielded["blow"]["damage"] <= round(plain_damage * 0.5) + 1

    await engine.leave_match(one.id)


async def test_a_shield_is_spent_by_the_blow_it_absorbs() -> None:
    shield = _equipped(
        SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=10, magnitude=500
    )
    engine, socket_one, socket_two, one, two = await _pair_with_skills(
        two_skills=(shield,)
    )
    await _settle()

    await _use(engine, two, socket_two, "SHIELD")
    await _play(engine, one, socket_one, correct=True)
    shielded = socket_one.last(ServerEvent.ANSWER_RESULT)
    await _play(engine, one, socket_one, correct=True)
    after = socket_one.last(ServerEvent.ANSWER_RESULT)

    assert shielded is not None and after is not None
    assert after["blow"]["damage"] > shielded["blow"]["damage"]

    await engine.leave_match(one.id)


async def test_a_damage_boost_multiplies_the_blow() -> None:
    boost = _equipped(
        SkillEffect.DOUBLE_DAMAGE, code="STRIKE_X2", mana_cost=10, magnitude=200
    )
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(boost,)
    )
    await _settle()

    await _play(engine, one, socket_one, correct=True)
    plain = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert plain is not None
    plain_damage = plain["blow"]["damage"]

    await _use(engine, one, socket_one, "STRIKE_X2")
    await _play(engine, one, socket_one, correct=True)

    boosted = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert boosted is not None
    assert boosted["blow"]["damage"] > plain_damage * 1.5

    await engine.leave_match(one.id)


async def test_a_heal_restores_health_but_never_past_the_maximum() -> None:
    heal = _equipped(SkillEffect.HEAL, code="MEND", mana_cost=10, magnitude=25)
    engine, socket_one, socket_two, one, two = await _pair_with_skills(
        two_skills=(heal,)
    )
    await _settle()

    await _play(engine, one, socket_one, correct=True)
    hurt = socket_two.last(ServerEvent.OPPONENT_ANSWERED)
    assert hurt is not None
    assert hurt["your_hp"] < MAX_HP

    await _use(engine, two, socket_two, "MEND")
    healed = socket_two.last(ServerEvent.SKILL_USED)
    assert healed is not None
    assert healed["your_hp"] > hurt["your_hp"]
    assert healed["your_hp"] <= MAX_HP

    await engine.leave_match(one.id)


async def test_a_mana_burn_drains_the_opponent() -> None:
    burn = _equipped(SkillEffect.MANA_BURN, code="DRAIN", mana_cost=10, magnitude=40)
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(burn,)
    )
    await _settle()

    await _use(engine, one, socket_one, "DRAIN")

    used = socket_one.last(ServerEvent.SKILL_USED)
    assert used is not None
    assert used["opponent_hp"] == MAX_HP
    match = engine.registry.match_of_user(one.id)
    assert match is not None
    assert match.players[match.player_ids[1]].mana == 100 - 40

    await engine.leave_match(one.id)


async def test_revealing_options_is_private_to_the_caster() -> None:
    reveal = _equipped(
        SkillEffect.REMOVE_OPTIONS, code="REVEAL", mana_cost=10, magnitude=2
    )
    engine, socket_one, socket_two, one, _two = await _pair_with_skills(
        one_skills=(reveal,)
    )
    await _settle()

    await _use(engine, one, socket_one, "REVEAL")

    mine = socket_one.last(ServerEvent.SKILL_USED)
    theirs = socket_two.last(ServerEvent.SKILL_USED)
    assert mine is not None and theirs is not None
    # The fake questions carry one wrong option, and at least one wrong answer
    # always survives, so nothing is removed here -- but the shape matters.
    assert mine["private"] is not None
    assert "removed_option_ids" in mine["private"]
    assert theirs["private"] is None

    await engine.leave_match(one.id)


async def test_revealing_options_with_no_question_open_is_refused() -> None:
    reveal = _equipped(
        SkillEffect.REMOVE_OPTIONS, code="REVEAL", mana_cost=10, magnitude=2
    )
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(reveal,)
    )
    await _settle()

    # Answered, and still inside the lockout that follows: there is nothing on
    # screen to narrow, and the mana must not be taken for it.
    await _answer(engine, one, socket_one, correct=True)
    await _use(engine, one, socket_one, "REVEAL")

    assert ErrorCode.QUESTION_CLOSED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_a_time_penalty_holds_the_opponents_next_question_back() -> None:
    freeze = _equipped(
        SkillEffect.TIME_PENALTY, code="FREEZE", mana_cost=10, magnitude=5
    )
    engine, socket_one, socket_two, one, two = await _pair_with_skills(
        one_skills=(freeze,)
    )
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    await _play(engine, two, socket_two, correct=True)
    pushes_before = len(socket_two.all_of(ServerEvent.QUESTION_PUSH))

    await _use(engine, one, socket_one, "FREEZE")
    # No deadline left to shorten, so the five seconds are taken off the other
    # end: the target waits that much longer for their next question.
    assert match.players[two.id].lockout_until >= clock.seconds() + 4
    await _settle(0.1)
    assert len(socket_two.all_of(ServerEvent.QUESTION_PUSH)) == pushes_before
    # And only the target is slowed.
    assert match.players[one.id].lockout_until < clock.seconds() + 1

    await engine.leave_match(one.id)


async def test_combo_keep_forgives_one_miss() -> None:
    focus = _equipped(
        SkillEffect.COMBO_KEEP, code="FOCUS", mana_cost=10, magnitude=1, duration_rounds=1
    )
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(focus,)
    )
    await _settle()

    await _play(engine, one, socket_one, correct=True)
    await _use(engine, one, socket_one, "FOCUS")
    await _play(engine, one, socket_one, correct=False)

    kept = socket_one.last(ServerEvent.ANSWER_RESULT)
    assert kept is not None
    # The miss would normally have reset this to zero.
    assert kept["your_combo"] == 1

    await engine.leave_match(one.id)


async def test_a_skill_cannot_be_cast_while_stunned() -> None:
    shield = _equipped(
        SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=10, magnitude=500
    )
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(shield,)
    )
    await _settle()

    match = engine.registry.match_of_user(one.id)
    assert match is not None
    match.players[one.id].stunned_until = clock.seconds() + 5

    await _use(engine, one, socket_one, "SHIELD")

    assert ErrorCode.STUNNED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_every_skill_fired_is_logged_for_the_match() -> None:
    shield = _equipped(
        SkillEffect.DAMAGE_REDUCTION, code="SHIELD", mana_cost=10, magnitude=500
    )
    engine, socket_one, _socket_two, one, _two = await _pair_with_skills(
        one_skills=(shield,), question_count=4
    )
    await _settle()

    await _use(engine, one, socket_one, "SHIELD")
    for _ in range(4):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.3)

    saved = engine.persistence.saved  # type: ignore[attr-defined]
    assert saved
    match, _result = saved[0]
    assert [entry.skill_code for entry in match.skill_log] == ["SHIELD"]
    assert match.skill_log[0].mana_spent == 10
    assert match.skill_log[0].user_id == one.id


# --- energy ----------------------------------------------------------------


async def test_a_player_with_no_energy_cannot_start_a_match() -> None:
    persistence = FakePersistence()
    persistence.reject_start = MatchStartRejected(
        reason=ErrorCode.NOT_ENOUGH_ENERGY,
        message="Not enough energy to start a match",
        user_ids=("alice",),
    )
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    socket_one, socket_two, one, _two = await _pair(engine)
    await _settle(0.2)

    assert ErrorCode.NOT_ENOUGH_ENERGY.value in socket_one.errors()
    assert ErrorCode.NOT_ENOUGH_ENERGY.value in socket_two.errors()
    # Nothing was written, and the players are free to try again.
    assert persistence.created == []
    assert persistence.saved == []
    assert engine.registry.match_of_user(one.id) is None


async def test_a_rejected_start_is_not_refunded() -> None:
    # Energy is checked for everyone before it is taken from anyone, so a
    # rejection has nothing to give back.
    persistence = FakePersistence()
    persistence.reject_start = MatchStartRejected(
        reason=ErrorCode.NOT_ENOUGH_ENERGY, message="no energy", user_ids=("alice",)
    )
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    await _pair(engine)
    await _settle(0.2)

    assert persistence.refunded == []


async def test_a_match_that_cannot_be_written_hands_the_energy_back() -> None:
    class ExplodingPersistence(FakePersistence):
        async def create_match(self, match: LiveMatch) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("the database went away")

    persistence = ExplodingPersistence()
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    _socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle(0.3)

    # `persisted` is still False when create_match raises, so this window is
    # only covered by the refund and nothing else.
    assert persistence.started != []
    assert persistence.created == []
    assert len(persistence.refunded) == 1
    assert engine.registry.match_of_user(one.id) is None


async def test_a_played_match_is_never_refunded(engine: DuoEngine) -> None:
    socket_one, _socket_two, one, _two = await _pair(engine)
    await _settle()
    for _ in range(QUESTION_COUNT):
        await _play(engine, one, socket_one, correct=True)
    await _settle(0.2)

    assert engine.persistence.refunded == []  # type: ignore[attr-defined]


async def test_a_match_aborted_before_the_start_charges_nothing() -> None:
    # Too few questions: the draw is checked before the players are started.
    engine, _socket_one, _socket_two, _one, _two = await _engine_with(question_count=1)
    await _settle(0.2)

    assert engine.persistence.started == []  # type: ignore[attr-defined]
    assert engine.persistence.refunded == []  # type: ignore[attr-defined]
