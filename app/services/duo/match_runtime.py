"""The duo match engine: matchmaking, round loop, grading and teardown.

One `asyncio.Task` per match drives the whole game (`_run_match`). A round ends
either when both connected players have answered — the task waits on an
`asyncio.Event` — or when the timer expires, whichever comes first. Because the
loop owns its own timing there are no detached callbacks to leak when a room is
torn down; cancelling the task cancels the match.

Answer timing is measured here, from `round_started_at`, and never taken from
the client.
"""

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel

from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.events import (
    AnswerOutcome,
    ChatMessageData,
    ConnectedData,
    ErrorCode,
    ErrorData,
    MatchFinishedData,
    MatchFoundData,
    MatchResumeData,
    MatchStartedData,
    OpponentAnsweredData,
    OpponentDisconnectedData,
    QueueWaitingData,
    RatingChange,
    RoomCreatedData,
    RoundResultData,
    RoundStartData,
    ServerEvent,
    envelope,
)
from app.services.duo.persistence import (
    DatabaseDuoPersistence,
    DuoPersistence,
    MatchResult,
)
from app.services.duo.persistence import RatingChange as PersistedRatingChange
from app.services.duo.registry import DuoRegistry
from app.services.duo.registry import registry as default_registry
from app.services.duo.scoring import (
    MatchOutcome,
    PlayerTotals,
    award_points,
    decide_outcome,
    invert,
)
from app.services.duo.state import (
    LiveMatch,
    MatchSettings,
    PlayerConn,
    QueueEntry,
    RoundRecord,
    SubmittedAnswer,
)

logger = logging.getLogger(__name__)

COUNTDOWN_SECONDS = 3.0
REVEAL_PAUSE_SECONDS = 1.5
BETWEEN_ROUNDS_SECONDS = 3.0
DISCONNECT_GRACE_SECONDS = 30
FINISHED_ROOM_TTL_SECONDS = 60
WAITING_ROOM_TTL_SECONDS = 600
QUEUE_TIMEOUT_SECONDS = 300
MIN_PLAYABLE_QUESTIONS = 3


class DuoEngine:
    def __init__(
        self,
        persistence: DuoPersistence | None = None,
        registry: DuoRegistry | None = None,
    ) -> None:
        self.persistence: DuoPersistence = persistence or DatabaseDuoPersistence()
        self.registry = registry or default_registry

    # --- connection ---------------------------------------------------------

    async def on_connect(self, user: User, websocket: WebSocket) -> None:
        """Greet a freshly authenticated socket and re-attach it if the user
        dropped out of a match that is still running."""
        match = self.registry.match_of_user(user.id)
        rating = await self.persistence.load_rating(user.id)
        await _send(
            websocket,
            ServerEvent.CONNECTED,
            ConnectedData(
                user=PlayerConn(
                    user_id=user.id,
                    username=user.username,
                    avatar_url=user.avatar_url,
                    rating=rating,
                ).to_read(),
                active_match_id=match.match_id if match is not None else None,
            ),
        )
        if match is not None:
            await self._reattach(match, user, websocket)

    async def on_disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """Route a dropped socket: pull it off the queue, cancel a waiting
        room, or start the forfeit grace timer for a match in progress."""
        async with self.registry.lock:
            queued = self.registry.dequeue(user_id)
        if queued is not None:
            return

        match = self.registry.match_of_user(user_id)
        if match is None:
            return
        player = match.players.get(user_id)
        # A stale socket from a connection the user already replaced.
        if player is None or player.websocket is not websocket:
            return

        player.websocket = None
        player.connected = False

        if match.status is DuoMatchStatus.WAITING:
            await self._cancel_waiting_room(match)
            return

        if match.status is DuoMatchStatus.IN_PROGRESS:
            await self._notify_opponent(
                match,
                user_id,
                ServerEvent.OPPONENT_DISCONNECTED,
                OpponentDisconnectedData(grace_seconds=DISCONNECT_GRACE_SECONDS),
            )
            self._close_round_if_everyone_answered(match)
            player.grace_task = asyncio.create_task(self._grace_timer(match, user_id))

    async def _reattach(self, match: LiveMatch, user: User, websocket: WebSocket) -> None:
        player = match.players.get(user.id)
        if player is None:
            return

        player.websocket = websocket
        player.connected = True
        if player.grace_task is not None:
            player.grace_task.cancel()
            player.grace_task = None

        opponent = match.opponent_of(user.id)
        if opponent is None or match.status is not DuoMatchStatus.IN_PROGRESS:
            return

        question: ChallengePublicRead | None = None
        remaining: int | None = None
        if 0 <= match.round_index < len(match.questions):
            question = match.questions[match.round_index]
            if match.round_started_at is not None:
                spent = match.elapsed_ms() // 1000
                remaining = max(0, match.settings.time_per_question - spent)

        await _send(
            websocket,
            ServerEvent.MATCH_RESUME,
            MatchResumeData(
                match_id=match.match_id,
                opponent=opponent.to_read(),
                settings=match.settings.to_read(),
                round_index=match.round_index,
                total_rounds=len(match.questions),
                your_score=player.score,
                opponent_score=opponent.score,
                question=question,
                seconds_remaining=remaining,
                already_answered=user.id in match.round_answers,
            ),
        )
        await self._notify_opponent(match, user.id, ServerEvent.OPPONENT_RECONNECTED, None)

    # --- matchmaking --------------------------------------------------------

    async def join_queue(
        self, user: User, websocket: WebSocket, settings: MatchSettings
    ) -> None:
        """Match against a waiting opponent immediately, or start waiting.

        The lookup and match creation happen under the registry lock so two
        players joining at the same instant can never be paired twice.
        """
        rating = await self.persistence.load_rating(user.id)
        entry = QueueEntry(
            user_id=user.id,
            rating=rating,
            settings=settings,
            websocket=websocket,
            username=user.username,
            avatar_url=user.avatar_url,
        )

        async with self.registry.lock:
            if self.registry.match_of_user(user.id) is not None:
                await _send_error(
                    websocket, ErrorCode.ALREADY_IN_MATCH, "You are already in a match"
                )
                return
            if self.registry.is_queued(user.id):
                await _send_error(
                    websocket, ErrorCode.ALREADY_IN_QUEUE, "You are already searching"
                )
                return

            opponent = self.registry.find_opponent(entry)
            if opponent is None:
                self.registry.enqueue(entry)
                position = self.registry.queue_position(user.id)
                await _send(
                    websocket,
                    ServerEvent.QUEUE_WAITING,
                    QueueWaitingData(position=position, waited_seconds=0),
                )
                return

            self.registry.dequeue(opponent.user_id)
            match = _build_match(
                mode=DuoMatchMode.RANDOM,
                host_id=opponent.user_id,
                settings=opponent.settings,
                entries=[opponent, entry],
            )
            self.registry.register(match)

        await self._announce_match_found(match, auto_start=True)
        match.task = asyncio.create_task(self._run_match(match))

    async def leave_queue(self, user_id: str, websocket: WebSocket) -> None:
        async with self.registry.lock:
            entry = self.registry.dequeue(user_id)
        if entry is None:
            await _send_error(websocket, ErrorCode.NOT_IN_QUEUE, "You are not searching")
            return
        await _send(websocket, ServerEvent.QUEUE_LEFT, None)

    # --- friend rooms -------------------------------------------------------

    async def create_room(
        self, user: User, websocket: WebSocket, settings: MatchSettings
    ) -> None:
        """Open a private WAITING room hosted by this user, for a friend to join by code."""
        rating = await self.persistence.load_rating(user.id)
        async with self.registry.lock:
            if self.registry.is_busy(user.id):
                await _send_error(
                    websocket, ErrorCode.ALREADY_IN_MATCH, "You are already in a match"
                )
                return
            code = self.registry.new_room_code()
            match = LiveMatch(
                match_id=str(uuid4()),
                mode=DuoMatchMode.FRIEND,
                host_id=user.id,
                settings=settings,
                room_code=code,
            )
            match.players[user.id] = PlayerConn(
                user_id=user.id,
                username=user.username,
                avatar_url=user.avatar_url,
                rating=rating,
                websocket=websocket,
            )
            self.registry.register(match)

        await _send(
            websocket,
            ServerEvent.ROOM_CREATED,
            RoomCreatedData(
                match_id=match.match_id,
                room_code=code,
                settings=settings.to_read(),
            ),
        )

    async def join_room(self, user: User, websocket: WebSocket, room_code: str) -> None:
        """Seat the second player in a friend room; the host still has to call start_match."""
        rating = await self.persistence.load_rating(user.id)
        async with self.registry.lock:
            if self.registry.is_busy(user.id):
                await _send_error(
                    websocket, ErrorCode.ALREADY_IN_MATCH, "You are already in a match"
                )
                return
            match = self.registry.get_by_code(room_code)
            if match is None or match.status is not DuoMatchStatus.WAITING:
                await _send_error(websocket, ErrorCode.ROOM_NOT_FOUND, "Room not found")
                return
            if match.is_full:
                await _send_error(websocket, ErrorCode.ROOM_FULL, "Room is full")
                return

            match.players[user.id] = PlayerConn(
                user_id=user.id,
                username=user.username,
                avatar_url=user.avatar_url,
                rating=rating,
                websocket=websocket,
            )
            self.registry.bind_player(match, user.id)

        await self._announce_match_found(match, auto_start=False)

    async def start_match(self, user_id: str, websocket: WebSocket) -> None:
        """Host-only trigger that kicks off the countdown and the match loop task."""
        match = self.registry.match_of_user(user_id)
        if match is None:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "You are not in a room")
            return
        if match.host_id != user_id:
            await _send_error(websocket, ErrorCode.NOT_HOST, "Only the host can start")
            return
        if match.status is not DuoMatchStatus.WAITING:
            await _send_error(
                websocket, ErrorCode.MATCH_ALREADY_STARTED, "The match already started"
            )
            return
        if not match.is_full:
            await _send_error(
                websocket, ErrorCode.NOT_ENOUGH_PLAYERS, "Waiting for an opponent"
            )
            return
        match.task = asyncio.create_task(self._run_match(match))

    # --- gameplay -----------------------------------------------------------

    async def submit_answer(
        self, user_id: str, websocket: WebSocket, round_index: int, option_id: str
    ) -> None:
        """Grade one answer against the server-held key and close the round
        once both connected players have answered.

        `elapsed_ms` is measured from the server's own round clock, so a
        client cannot inflate its speed bonus by lying about timing.
        """
        match = self.registry.match_of_user(user_id)
        if match is None or match.status is not DuoMatchStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No match in progress")
            return
        # An open round is the current index with a live clock; anything else
        # is a late or replayed submission.
        if match.round_index != round_index or match.round_started_at is None:
            await _send_error(websocket, ErrorCode.ROUND_CLOSED, "This round is closed")
            return
        if user_id in match.round_answers:
            await _send_error(
                websocket, ErrorCode.ALREADY_ANSWERED, "You already answered this round"
            )
            return

        question = match.questions[round_index]
        if option_id not in {option.id for option in question.options}:
            await _send_error(
                websocket, ErrorCode.INVALID_OPTION, "That option is not on this question"
            )
            return

        elapsed_ms = match.elapsed_ms()
        is_correct = option_id in match.answer_key.get(question.id, [])
        points = award_points(is_correct, elapsed_ms, match.settings.time_per_question)

        match.round_answers[user_id] = SubmittedAnswer(
            option_id=option_id,
            elapsed_ms=elapsed_ms,
            is_correct=is_correct,
            points=points,
        )
        player = match.players[user_id]
        player.score += points
        player.correct_count += 1 if is_correct else 0
        player.total_elapsed_ms += elapsed_ms

        await self._notify_opponent(
            match,
            user_id,
            ServerEvent.ROUND_OPPONENT_ANSWERED,
            OpponentAnsweredData(round_index=round_index),
        )
        self._close_round_if_everyone_answered(match)

    async def leave_match(self, user_id: str) -> None:
        """Voluntary exit: cancel a waiting room, or forfeit a match in progress."""
        match = self.registry.match_of_user(user_id)
        if match is None:
            return
        if match.status is DuoMatchStatus.WAITING:
            await self._cancel_waiting_room(match)
            return
        if match.status is DuoMatchStatus.IN_PROGRESS:
            await self._finish(
                match, DuoMatchEndReason.OPPONENT_LEFT, forfeit_user_id=user_id
            )

    async def send_chat(self, user_id: str, websocket: WebSocket, message: str) -> None:
        match = self.registry.match_of_user(user_id)
        if match is None:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "You are not in a match")
            return
        await self._broadcast(
            match,
            ServerEvent.CHAT_MESSAGE,
            ChatMessageData(user_id=user_id, message=message, sent_at=datetime.now(UTC)),
        )

    # --- match loop ---------------------------------------------------------

    async def _run_match(self, match: LiveMatch) -> None:
        """The task body that drives one match end-to-end: countdown, every
        round in sequence, then a normal finish — or an abort if anything
        throws."""
        try:
            await asyncio.sleep(COUNTDOWN_SECONDS)
            if not await self._prepare(match):
                return
            for index in range(len(match.questions)):
                if match.status is not DuoMatchStatus.IN_PROGRESS:
                    break
                await self._run_round(match, index)
            if match.status is DuoMatchStatus.IN_PROGRESS:
                await self._finish(match, DuoMatchEndReason.COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Duo match %s crashed", match.match_id)
            await self._abort(match)

    async def _prepare(self, match: LiveMatch) -> bool:
        """Draw the question set and persist the match row before round one.

        Returns False (and aborts the match) when there aren't enough
        questions to play, so the caller knows not to start the round loop.
        """
        question_set = await self.persistence.draw_questions(match.settings)
        if len(question_set.questions) < MIN_PLAYABLE_QUESTIONS:
            await self._broadcast_error(
                match,
                ErrorCode.NO_QUESTIONS_AVAILABLE,
                "Not enough questions match those filters",
            )
            await self._abort(match)
            return False

        match.questions = question_set.questions
        match.answer_key = question_set.answer_key
        match.explanations = question_set.explanations
        match.status = DuoMatchStatus.IN_PROGRESS
        match.started_wall = datetime.now(UTC)

        await self.persistence.create_match(match)
        match.persisted = True

        await self._broadcast(
            match,
            ServerEvent.MATCH_STARTED,
            MatchStartedData(match_id=match.match_id, total_rounds=len(match.questions)),
        )
        return True

    async def _run_round(self, match: LiveMatch, index: int) -> None:
        """Broadcast one question, wait for both answers or the timer, then
        reveal the result. Runs synchronously inside the match task, so the
        round's lifetime is exactly this call."""
        question = match.questions[index]
        limit = match.settings.time_per_question

        match.round_index = index
        match.round_answers = {}
        match.round_closed = asyncio.Event()
        match.round_started_at = time.monotonic()

        await self._broadcast(
            match,
            ServerEvent.ROUND_START,
            RoundStartData(
                round_index=index,
                total_rounds=len(match.questions),
                question=question,
                time_limit_seconds=limit,
                deadline_at=datetime.now(UTC) + timedelta(seconds=limit),
            ),
        )

        closed_early = True
        try:
            await asyncio.wait_for(match.round_closed.wait(), timeout=limit)
        except TimeoutError:
            closed_early = False

        # Stop the clock before pausing, so nothing lands after the round shut.
        match.round_started_at = None
        if closed_early:
            await asyncio.sleep(REVEAL_PAUSE_SECONDS)

        if match.status is not DuoMatchStatus.IN_PROGRESS:
            return

        match.rounds_log.append(
            RoundRecord(
                round_index=index,
                challenge_id=question.id,
                answers=dict(match.round_answers),
            )
        )
        await self._broadcast_round_result(match, index, question)
        await asyncio.sleep(BETWEEN_ROUNDS_SECONDS)

    def _close_round_if_everyone_answered(self, match: LiveMatch) -> None:
        """Advance as soon as nobody is still expected to answer.

        Only connected players count, so a player dropping mid-round does not
        leave the other one waiting out the full timer.
        """
        expected = {player.user_id for player in match.connected_players()}
        if expected and expected <= set(match.round_answers):
            match.round_closed.set()

    # --- ending -------------------------------------------------------------

    async def _finish(
        self,
        match: LiveMatch,
        end_reason: DuoMatchEndReason,
        forfeit_user_id: str | None = None,
    ) -> None:
        """Settle the match once: decide WIN/LOSE/DRAW, persist the result
        and rating changes, then push MATCH_FINISHED to both players.

        Guarded by the FINISHED status check so a race between a timeout,
        a forfeit and normal completion can only ever run this once.
        """
        if match.status is DuoMatchStatus.FINISHED:
            return
        match.status = DuoMatchStatus.FINISHED
        match.finished_at = time.monotonic()
        self._cancel_task(match)
        self._cancel_grace_timers(match)

        player_ids = match.player_ids
        if len(player_ids) < 2:
            await self._teardown(match)
            return

        one_id, two_id = player_ids[0], player_ids[1]
        outcomes = _decide_outcomes(match, one_id, two_id, forfeit_user_id)
        winner_id = next(
            (user_id for user_id, outcome in outcomes.items() if outcome is MatchOutcome.WIN),
            None,
        )
        duration = _duration_seconds(match)

        changes: dict[str, PersistedRatingChange] = {}
        if match.persisted:
            changes = await self.persistence.save_result(
                match,
                MatchResult(
                    outcome_by_user=outcomes,
                    winner_id=winner_id,
                    end_reason=end_reason,
                    duration_seconds=duration,
                ),
            )

        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            change = changes.get(user_id)
            await _send(
                player.websocket,
                ServerEvent.MATCH_FINISHED,
                MatchFinishedData(
                    match_id=match.match_id,
                    result=outcomes[user_id],
                    end_reason=end_reason,
                    your_score=player.score,
                    opponent_score=opponent.score,
                    your_correct=player.correct_count,
                    opponent_correct=opponent.correct_count,
                    total_rounds=len(match.questions),
                    duration_seconds=duration,
                    rating=RatingChange(
                        before=change.before if change else player.rating,
                        after=change.after if change else player.rating,
                        delta=change.delta if change else 0,
                    ),
                ),
            )

        await self._teardown(match)

    async def _grace_timer(self, match: LiveMatch, user_id: str) -> None:
        """Forfeit on behalf of a player who never comes back."""
        try:
            await asyncio.sleep(DISCONNECT_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        player = match.players.get(user_id)
        if player is None or player.connected:
            return
        if match.status is not DuoMatchStatus.IN_PROGRESS:
            return
        await self._finish(
            match, DuoMatchEndReason.OPPONENT_TIMEOUT, forfeit_user_id=user_id
        )

    async def _cancel_waiting_room(self, match: LiveMatch) -> None:
        """A lobby that lost a player is closed; nobody's rating is touched."""
        match.status = DuoMatchStatus.CANCELLED
        self._cancel_task(match)
        if match.persisted:
            await self.persistence.cancel_match(match)
        await self._teardown(match)

    async def close_idle_room(self, match: LiveMatch) -> None:
        """Drop a lobby nobody ever joined, telling whoever is still sitting in it."""
        await self._broadcast_error(
            match, ErrorCode.ROOM_NOT_FOUND, "The room was closed after being idle"
        )
        await self._cancel_waiting_room(match)

    async def shutdown(self) -> None:
        """Cancel every running match loop so the process can exit promptly."""
        for match in self.registry.all_matches():
            self._cancel_grace_timers(match)
            if match.task is not None:
                match.task.cancel()
                match.task = None

    async def _abort(self, match: LiveMatch) -> None:
        match.status = DuoMatchStatus.CANCELLED
        if match.persisted:
            await self.persistence.cancel_match(match)
        await self._teardown(match)

    async def _teardown(self, match: LiveMatch) -> None:
        self._cancel_grace_timers(match)
        async with self.registry.lock:
            self.registry.unregister(match.match_id)

    def _cancel_task(self, match: LiveMatch) -> None:
        task = match.task
        # Never cancel the task we are currently running inside.
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            match.task = None

    def _cancel_grace_timers(self, match: LiveMatch) -> None:
        # A grace timer is what calls `_finish` on a forfeit, so this runs
        # inside one of these very tasks. Cancelling the current task would
        # raise at the next await — mid-way through saving the result — and
        # kill the match silently, so skip it and only drop the reference.
        current = asyncio.current_task()
        for player in match.players.values():
            task = player.grace_task
            if task is not None and task is not current:
                task.cancel()
            player.grace_task = None

    # --- sending ------------------------------------------------------------

    async def _announce_match_found(self, match: LiveMatch, auto_start: bool) -> None:
        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            await _send(
                player.websocket,
                ServerEvent.MATCH_FOUND,
                MatchFoundData(
                    match_id=match.match_id,
                    room_code=match.room_code,
                    mode=match.mode,
                    opponent=opponent.to_read(),
                    settings=match.settings.to_read(),
                    host_id=match.host_id,
                    auto_start=auto_start,
                ),
            )

    async def _broadcast_round_result(
        self, match: LiveMatch, index: int, question: ChallengePublicRead
    ) -> None:
        correct_ids = match.answer_key.get(question.id, [])
        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            await _send(
                player.websocket,
                ServerEvent.ROUND_RESULT,
                RoundResultData(
                    round_index=index,
                    correct_option_ids=correct_ids,
                    explanation=match.explanations.get(question.id),
                    you=_outcome_of(match.round_answers.get(user_id)),
                    opponent=_outcome_of(match.round_answers.get(opponent.user_id)),
                    your_score=player.score,
                    opponent_score=opponent.score,
                ),
            )

    async def _broadcast(
        self, match: LiveMatch, event: ServerEvent, data: BaseModel | None
    ) -> None:
        for player in match.players.values():
            await _send(player.websocket, event, data)

    async def _broadcast_error(
        self, match: LiveMatch, code: ErrorCode, message: str
    ) -> None:
        for player in match.players.values():
            await _send_error(player.websocket, code, message)

    async def _notify_opponent(
        self, match: LiveMatch, user_id: str, event: ServerEvent, data: BaseModel | None
    ) -> None:
        opponent = match.opponent_of(user_id)
        if opponent is not None:
            await _send(opponent.websocket, event, data)


# --- module helpers --------------------------------------------------------


def _build_match(
    mode: DuoMatchMode,
    host_id: str,
    settings: MatchSettings,
    entries: list[QueueEntry],
) -> LiveMatch:
    """Build a fresh in-memory match from queue entries (random matchmaking path)."""
    match = LiveMatch(
        match_id=str(uuid4()),
        mode=mode,
        host_id=host_id,
        settings=settings,
    )
    for entry in entries:
        match.players[entry.user_id] = PlayerConn(
            user_id=entry.user_id,
            username=entry.username,
            avatar_url=entry.avatar_url,
            rating=entry.rating,
            websocket=entry.websocket,
        )
    return match


def _decide_outcomes(
    match: LiveMatch,
    one_id: str,
    two_id: str,
    forfeit_user_id: str | None,
) -> dict[str, MatchOutcome]:
    """A forfeit always loses regardless of score; otherwise fall back to
    the normal score/speed tiebreak in scoring.decide_outcome."""
    if forfeit_user_id is not None:
        other_id = two_id if forfeit_user_id == one_id else one_id
        return {forfeit_user_id: MatchOutcome.LOSE, other_id: MatchOutcome.WIN}

    one, two = match.players[one_id], match.players[two_id]
    outcome_one = decide_outcome(_totals(one), _totals(two))
    return {one_id: outcome_one, two_id: invert(outcome_one)}


def _totals(player: PlayerConn) -> PlayerTotals:
    return PlayerTotals(
        score=player.score,
        correct_count=player.correct_count,
        total_elapsed_ms=player.total_elapsed_ms,
    )


def _duration_seconds(match: LiveMatch) -> int:
    if match.started_wall is None:
        return 0
    return max(0, int((datetime.now(UTC) - match.started_wall).total_seconds()))


def _outcome_of(answer: SubmittedAnswer | None) -> AnswerOutcome:
    if answer is None:
        return AnswerOutcome(option_id=None, correct=False, elapsed_ms=None, points=0)
    return AnswerOutcome(
        option_id=answer.option_id,
        correct=answer.is_correct,
        elapsed_ms=answer.elapsed_ms,
        points=answer.points,
    )


async def _send(
    websocket: WebSocket | None, event: ServerEvent, data: BaseModel | None
) -> None:
    """Best-effort delivery. A dead socket is handled by the disconnect path."""
    if websocket is None:
        return
    with contextlib.suppress(Exception):
        await websocket.send_json(envelope(event, data))


async def _send_error(websocket: WebSocket | None, code: ErrorCode, message: str) -> None:
    await _send(websocket, ServerEvent.ERROR, ErrorData(code=code, message=message))


engine = DuoEngine()
