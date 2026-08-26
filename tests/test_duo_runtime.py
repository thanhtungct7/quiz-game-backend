import asyncio
from typing import Any

import pytest

from app.models.auth.user import User
from app.models.content.challenge import ChallengeDifficulty, ChallengeType
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchStatus
from app.schemas.content.course_content import ChallengeOptionPublicRead, ChallengePublicRead
from app.schemas.content.quiz import QuizSetWithAnswers
from app.schemas.duo.events import ErrorCode, ServerEvent
from app.services.duo import match_runtime
from app.services.duo.match_runtime import DuoEngine
from app.services.duo.persistence import MatchResult, RatingChange
from app.services.duo.registry import DuoRegistry
from app.services.duo.scoring import MatchOutcome
from app.services.duo.state import LiveMatch, MatchSettings

TIME_PER_QUESTION = 1
QUESTION_COUNT = 3


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
        self.created: list[LiveMatch] = []
        self.saved: list[tuple[LiveMatch, MatchResult]] = []
        self.cancelled: list[LiveMatch] = []

    async def load_rating(self, user_id: str) -> int:
        await asyncio.sleep(0)
        return self.ratings.get(user_id, 1000)

    async def draw_questions(self, settings: MatchSettings) -> QuizSetWithAnswers:
        await asyncio.sleep(0)
        questions = [_question(index) for index in range(self.question_count)]
        return QuizSetWithAnswers(
            questions=questions,
            answer_key={question.id: [f"{question.id}-correct"] for question in questions},
            explanations={question.id: f"because {question.id}" for question in questions},
        )

    async def create_match(self, match: LiveMatch) -> None:
        await asyncio.sleep(0)
        self.created.append(match)

    async def save_result(
        self, match: LiveMatch, result: MatchResult
    ) -> dict[str, RatingChange]:
        # A real save hits PostgreSQL and suspends here.
        await asyncio.sleep(0)
        self.saved.append((match, result))
        one_id, two_id = match.player_ids[0], match.player_ids[1]
        outcome = result.outcome_by_user[one_id]
        delta = 16 if outcome is MatchOutcome.WIN else (-16 if outcome is MatchOutcome.LOSE else 0)
        return {
            one_id: RatingChange(before=1000, after=1000 + delta),
            two_id: RatingChange(before=1000, after=1000 - delta),
        }

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


def _settings() -> MatchSettings:
    return MatchSettings(question_count=QUESTION_COUNT, time_per_question=TIME_PER_QUESTION)


@pytest.fixture(autouse=True)
def _fast_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Collapse the cosmetic pauses so a full match runs in milliseconds."""
    monkeypatch.setattr(match_runtime, "COUNTDOWN_SECONDS", 0.01)
    monkeypatch.setattr(match_runtime, "REVEAL_PAUSE_SECONDS", 0.01)
    monkeypatch.setattr(match_runtime, "BETWEEN_ROUNDS_SECONDS", 0.01)


@pytest.fixture
def engine() -> DuoEngine:
    return DuoEngine(persistence=FakePersistence(), registry=DuoRegistry())


async def _settle(seconds: float = 0.08) -> None:
    await asyncio.sleep(seconds)


async def _pair(engine: DuoEngine) -> tuple[FakeWebSocket, FakeWebSocket, User, User]:
    one, two = _user("alice"), _user("bob")
    socket_one, socket_two = FakeWebSocket(), FakeWebSocket()
    await engine.join_queue(one, socket_one, _settings())  # type: ignore[arg-type]
    await engine.join_queue(two, socket_two, _settings())  # type: ignore[arg-type]
    return socket_one, socket_two, one, two


def _current_round(socket: FakeWebSocket) -> dict[str, Any]:
    data = socket.last(ServerEvent.ROUND_START)
    assert data is not None
    return data


async def _answer(
    engine: DuoEngine, user: User, socket: FakeWebSocket, *, correct: bool
) -> None:
    round_data = _current_round(socket)
    question_id = round_data["question"]["id"]
    suffix = "correct" if correct else "wrong"
    await engine.submit_answer(
        user.id,
        socket,  # type: ignore[arg-type]
        round_data["round_index"],
        f"{question_id}-{suffix}",
    )


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


# --- gameplay --------------------------------------------------------------


async def test_round_start_never_reveals_the_answer(engine: DuoEngine) -> None:
    socket_one, _, one, _ = await _pair(engine)
    await _settle()

    round_data = _current_round(socket_one)
    for option in round_data["question"]["options"]:
        assert "correct" not in option
    assert "correct_option_ids" not in round_data
    assert "explanation" not in round_data
    assert round_data["time_limit_seconds"] == TIME_PER_QUESTION
    assert round_data["total_rounds"] == QUESTION_COUNT

    await engine.leave_match(one.id)


async def test_answering_twice_in_one_round_is_rejected(engine: DuoEngine) -> None:
    socket_one, _, one, _ = await _pair(engine)
    await _settle()

    await _answer(engine, one, socket_one, correct=True)
    await _answer(engine, one, socket_one, correct=False)

    assert ErrorCode.ALREADY_ANSWERED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_answering_a_stale_round_is_rejected(engine: DuoEngine) -> None:
    socket_one, _, one, _ = await _pair(engine)
    await _settle()

    round_data = _current_round(socket_one)
    await engine.submit_answer(
        one.id,
        socket_one,  # type: ignore[arg-type]
        round_data["round_index"] + 5,
        f"{round_data['question']['id']}-correct",
    )

    assert ErrorCode.ROUND_CLOSED.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_an_option_from_another_question_is_rejected(engine: DuoEngine) -> None:
    socket_one, _, one, _ = await _pair(engine)
    await _settle()

    round_data = _current_round(socket_one)
    await engine.submit_answer(
        one.id, socket_one, round_data["round_index"], "not-an-option"  # type: ignore[arg-type]
    )

    assert ErrorCode.INVALID_OPTION.value in socket_one.errors()
    await engine.leave_match(one.id)


async def test_opponent_is_told_an_answer_landed_but_not_which(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, _ = await _pair(engine)
    await _settle()

    await _answer(engine, one, socket_one, correct=True)

    notice = socket_two.last(ServerEvent.ROUND_OPPONENT_ANSWERED)
    assert notice == {"round_index": 0}

    await engine.leave_match(one.id)


async def test_both_answering_closes_the_round_before_the_timer(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, two = await _pair(engine)
    await _settle()

    await _answer(engine, one, socket_one, correct=True)
    await _answer(engine, two, socket_two, correct=False)
    # Well under the 1s round timer: the round must already have been graded.
    await _settle(0.1)

    result = socket_one.last(ServerEvent.ROUND_RESULT)
    assert result is not None
    assert result["you"]["correct"] is True
    assert result["opponent"]["correct"] is False
    assert result["you"]["points"] > 0
    assert result["opponent"]["points"] == 0
    assert result["explanation"] == "because q0"

    await engine.leave_match(one.id)


async def test_a_full_match_ends_with_opposite_results_and_zero_sum_rating(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, two = await _pair(engine)

    for _ in range(QUESTION_COUNT):
        await _settle()
        await _answer(engine, one, socket_one, correct=True)
        await _answer(engine, two, socket_two, correct=False)
    await _settle(0.2)

    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["result"] == MatchOutcome.WIN.value
    assert finished_two["result"] == MatchOutcome.LOSE.value
    assert finished_one["end_reason"] == DuoMatchEndReason.COMPLETED.value
    assert finished_one["your_correct"] == QUESTION_COUNT
    assert finished_two["your_correct"] == 0
    assert finished_one["your_score"] == finished_two["opponent_score"]
    assert finished_one["rating"]["delta"] == -finished_two["rating"]["delta"]


async def test_a_finished_match_frees_both_players(engine: DuoEngine) -> None:
    socket_one, socket_two, one, two = await _pair(engine)

    for _ in range(QUESTION_COUNT):
        await _settle()
        await _answer(engine, one, socket_one, correct=True)
        await _answer(engine, two, socket_two, correct=True)
    await _settle(0.2)

    assert engine.registry.match_of_user(one.id) is None
    assert engine.registry.match_of_user(two.id) is None


async def test_identical_play_is_a_draw_for_both(engine: DuoEngine) -> None:
    socket_one, socket_two, one, two = await _pair(engine)

    for _ in range(QUESTION_COUNT):
        await _settle()
        # Neither answers: equal score, equal correct count, equal time.
        await asyncio.sleep(TIME_PER_QUESTION + 0.05)

    await _settle(0.2)
    finished_one = socket_one.last(ServerEvent.MATCH_FINISHED)
    finished_two = socket_two.last(ServerEvent.MATCH_FINISHED)
    assert finished_one is not None and finished_two is not None
    assert finished_one["result"] == MatchOutcome.DRAW.value
    assert finished_two["result"] == MatchOutcome.DRAW.value


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
    socket_one, socket_two, one, two = await _pair(engine)
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
    await _answer(engine, one, socket_one, correct=True)

    await engine.on_disconnect(one.id, socket_one)  # type: ignore[arg-type]
    replacement = FakeWebSocket()
    await engine.on_connect(one, replacement)  # type: ignore[arg-type]

    resume = replacement.first(ServerEvent.MATCH_RESUME)
    assert resume is not None
    assert resume["your_score"] > 0
    assert resume["already_answered"] is True
    assert resume["opponent"]["id"] == two.id
    assert socket_two.last(ServerEvent.OPPONENT_RECONNECTED) is not None

    await engine.leave_match(one.id)


async def test_leaving_on_purpose_hands_the_win_to_the_opponent(
    engine: DuoEngine,
) -> None:
    socket_one, socket_two, one, two = await _pair(engine)
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

    assert socket_guest.first(ServerEvent.ROUND_START) is not None
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
    engine = DuoEngine(persistence=FakePersistence(question_count=1), registry=DuoRegistry())
    socket_one, socket_two, one, _ = await _pair(engine)
    await _settle()

    assert ErrorCode.NO_QUESTIONS_AVAILABLE.value in socket_one.errors()
    assert ErrorCode.NO_QUESTIONS_AVAILABLE.value in socket_two.errors()
    assert engine.registry.match_of_user(one.id) is None


async def test_an_aborted_match_is_never_persisted() -> None:
    persistence = FakePersistence(question_count=1)
    engine = DuoEngine(persistence=persistence, registry=DuoRegistry())
    await _pair(engine)
    await _settle()

    assert persistence.created == []
    assert persistence.saved == []


async def test_a_played_match_is_persisted_once(engine: DuoEngine) -> None:
    persistence = engine.persistence
    assert isinstance(persistence, FakePersistence)
    socket_one, socket_two, one, two = await _pair(engine)

    for _ in range(QUESTION_COUNT):
        await _settle()
        await _answer(engine, one, socket_one, correct=True)
        await _answer(engine, two, socket_two, correct=False)
    await _settle(0.2)

    assert len(persistence.created) == 1
    assert len(persistence.saved) == 1
    saved_match, result = persistence.saved[0]
    assert saved_match.status is DuoMatchStatus.FINISHED
    assert result.winner_id == one.id
    assert len(saved_match.rounds_log) == QUESTION_COUNT
