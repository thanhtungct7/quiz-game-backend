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

from app.core.config import settings as app_settings
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.models.game.skill import SkillEffect
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.events import (
    AnswerOutcome,
    BlowRead,
    ChatMessageData,
    ConnectedData,
    ErrorCode,
    ErrorData,
    ExpChange,
    GoldChange,
    LootDropRead,
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
    SeasonChangeRead,
    ServerEvent,
    SkillUsedData,
    StreakChangeRead,
    envelope,
)
from app.services.auth.avatar_url import resolve_avatar_url
from app.services.duo.combat import (
    Blow,
    apply_damage,
    award_mana,
    combo_after,
    gain_mana,
    resolve_blow,
)
from app.services.duo.loadout import ActiveEffect, EquippedSkill, SkillUseRecord
from app.services.duo.persistence import (
    DatabaseDuoPersistence,
    DuoPersistence,
    MatchResult,
    MatchRewards,
    MatchStartRejected,
)
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
from app.services.game.settlement import (
    ExpAward,
    GoldAward,
    LootDrop,
    SeasonChange,
    StreakChange,
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
                    avatar_url=resolve_avatar_url(user, app_settings),
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
                your_hp=player.hp,
                opponent_hp=opponent.hp,
                your_mana=player.mana,
                your_combo=player.combo,
                you_are_stunned=player.is_stunned_for(match.round_index),
                your_max_hp=player.build.max_hp,
                active_effects=player.effects.codes(match.round_index),
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
            avatar_url=resolve_avatar_url(user, app_settings),
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
                avatar_url=resolve_avatar_url(user, app_settings),
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
                avatar_url=resolve_avatar_url(user, app_settings),
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
        player = match.players[user_id]
        if player.is_stunned_for(round_index):
            await _send_error(websocket, ErrorCode.STUNNED, "You are stunned this round")
            return

        question = match.questions[round_index]
        if option_id not in {option.id for option in question.options}:
            await _send_error(
                websocket, ErrorCode.INVALID_OPTION, "That option is not on this question"
            )
            return

        elapsed_ms = match.elapsed_ms()
        # The round runs on one clock and one timer, but each player can have
        # their own deadline inside it, so a late answer is rejected here
        # rather than by shortening the round.
        if elapsed_ms > player.effective_limit_ms:
            await _send_error(websocket, ErrorCode.ROUND_CLOSED, "Your time is up")
            return
        is_correct = option_id in match.answer_key.get(question.id, [])
        points = award_points(is_correct, elapsed_ms, match.settings.time_per_question)

        match.round_answers[user_id] = SubmittedAnswer(
            option_id=option_id,
            elapsed_ms=elapsed_ms,
            is_correct=is_correct,
            points=points,
        )
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

    async def use_skill(
        self, user_id: str, websocket: WebSocket, round_index: int, skill_code: str
    ) -> None:
        """Cast one equipped skill in the open round.

        Everything is checked against the loadout resolved at match start, so
        this never touches the database: a live round must not wait on a query,
        and a player must not be able to re-equip mid-fight.
        """
        match = self.registry.match_of_user(user_id)
        if match is None or match.status is not DuoMatchStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No match in progress")
            return
        if match.round_index != round_index or match.round_started_at is None:
            await _send_error(websocket, ErrorCode.ROUND_NOT_OPEN, "This round is closed")
            return

        player = match.players[user_id]
        opponent = match.opponent_of(user_id)
        if opponent is None:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No opponent")
            return
        if player.is_stunned_for(round_index):
            await _send_error(websocket, ErrorCode.STUNNED, "You are stunned this round")
            return

        skill = player.build.skill(skill_code)
        if skill is None:
            await _send_error(
                websocket, ErrorCode.SKILL_NOT_EQUIPPED, "That skill is not equipped"
            )
            return
        if skill_code in player.skills_used_this_round:
            await _send_error(
                websocket,
                ErrorCode.SKILL_ALREADY_USED_THIS_ROUND,
                "That skill has already been used this round",
            )
            return
        if player.mana < skill.mana_cost:
            await _send_error(websocket, ErrorCode.NOT_ENOUGH_MANA, "Not enough mana")
            return
        # Revealing options after answering would be pointless, and letting it
        # through would still cost the player their mana.
        if skill.effect is SkillEffect.REMOVE_OPTIONS and user_id in match.round_answers:
            await _send_error(
                websocket, ErrorCode.ROUND_NOT_OPEN, "You have already answered"
            )
            return

        player.mana -= skill.mana_cost
        player.skills_used_this_round.add(skill_code)
        match.skill_log.append(
            SkillUseRecord(
                round_index=round_index,
                user_id=user_id,
                skill_id=skill.skill_id,
                skill_code=skill.code,
                mana_spent=skill.mana_cost,
            )
        )
        private = self._apply_skill(match, player, opponent, skill, round_index)
        await self._broadcast_skill_used(match, user_id, skill, private)

    def _apply_skill(
        self,
        match: LiveMatch,
        player: PlayerConn,
        opponent: PlayerConn,
        skill: EquippedSkill,
        round_index: int,
    ) -> dict[str, object] | None:
        """Do what the skill does, and return anything only the caster may see."""
        effect = skill.effect

        if effect is SkillEffect.HEAL:
            player.hp = min(player.build.max_hp, player.hp + skill.magnitude)
            return None
        if effect is SkillEffect.MANA_BURN:
            opponent.mana = max(0, opponent.mana - skill.magnitude)
            return None
        if effect is SkillEffect.REMOVE_OPTIONS:
            return {"removed_option_ids": self._removed_options(match, skill.magnitude)}

        # The rest stand until they are spent. A duration of zero means this
        # round; one means the round after it.
        target = opponent if effect is SkillEffect.TIME_PENALTY else player
        target.add_effect(
            ActiveEffect(
                effect=effect,
                magnitude=skill.magnitude,
                expires_after_round=round_index + skill.duration_rounds,
                source_code=skill.code,
            )
        )
        return None

    def _removed_options(self, match: LiveMatch, count: int) -> list[str]:
        """Wrong options to hide from the caster, never broadcast."""
        question = match.questions[match.round_index]
        correct = set(match.answer_key.get(question.id, []))
        wrong = [option.id for option in question.options if option.id not in correct]
        # Always leave at least one wrong answer standing, so a reveal narrows
        # the choice rather than handing it over.
        return wrong[: min(count, max(0, len(wrong) - 1))]

    async def _broadcast_skill_used(
        self,
        match: LiveMatch,
        caster_id: str,
        skill: EquippedSkill,
        private: dict[str, object] | None,
    ) -> None:
        """Both sides see what was cast; only the caster sees the private part."""
        for user_id, player in match.players.items():
            other = match.opponent_of(user_id)
            await _send(
                player.websocket,
                ServerEvent.SKILL_USED,
                SkillUsedData(
                    round_index=match.round_index,
                    user_id=caster_id,
                    skill_code=skill.code,
                    skill_name=skill.name,
                    effect=skill.effect,
                    magnitude=skill.magnitude,
                    mana_spent=skill.mana_cost,
                    your_hp=player.hp,
                    opponent_hp=other.hp if other is not None else 0,
                    your_mana=player.mana,
                    private=private if user_id == caster_id else None,
                ),
            )

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
                # A knockout stops the match, but only after the round that
                # landed it has broadcast its result.
                if match.ko_pending:
                    break
            if match.status is DuoMatchStatus.IN_PROGRESS:
                await self._finish(
                    match,
                    DuoMatchEndReason.KNOCKOUT
                    if match.ko_pending
                    else DuoMatchEndReason.COMPLETED,
                )
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

        # Resolved here because `_prepare` is the one point both entry paths --
        # random queue and friend room -- pass through, and because a friend
        # room can sit waiting for ten minutes: a build read at room-create
        # time would be stale by the time the fight started.
        loadouts = await self.persistence.start_players(match)
        if isinstance(loadouts, MatchStartRejected):
            await self._broadcast_error(match, loadouts.reason, loadouts.message)
            await self._abort(match)
            return False
        # Whatever the start charged is now owed back if the match never runs.
        match.energy_spent = True
        for user_id, player in match.players.items():
            player.loadout = loadouts.get(user_id)
            player.hp = player.build.max_hp
            player.mana = player.build.starting_mana

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
        for player in match.players.values():
            player.skills_used_this_round = set()
            player.effects.prune(index)
            # A time penalty shortens this player's own deadline without
            # touching the round's single timer.
            penalty = player.effects.consume(SkillEffect.TIME_PENALTY, index)
            seconds = limit - (penalty.magnitude if penalty is not None else 0)
            player.effective_limit_ms = max(1, seconds) * 1000
        match.round_started_at = time.monotonic()

        await self._broadcast_round_start(match, index, question, limit)

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

        blows, hp_after = self._settle_round_damage(match)
        match.rounds_log.append(
            RoundRecord(
                round_index=index,
                challenge_id=question.id,
                answers=dict(match.round_answers),
                blows=blows,
                hp_after=hp_after,
            )
        )
        await self._broadcast_round_result(match, index, question, blows)
        await asyncio.sleep(BETWEEN_ROUNDS_SECONDS)

    def _settle_round_damage(
        self, match: LiveMatch
    ) -> tuple[dict[str, Blow], dict[str, int]]:
        """Resolve both attacks for the round that just closed.

        Every blow is computed from the health both players started the round
        with, and only then applied. Applying damage the moment an answer
        arrives would let whoever pressed first knock the other out before they
        had a chance to answer the same question.
        """
        limit = match.settings.time_per_question
        blows: dict[str, Blow] = {}

        for user_id, player in match.players.items():
            answer = match.round_answers.get(user_id)
            is_correct = answer is not None and answer.is_correct
            opponent = match.opponent_of(user_id)

            if is_correct or not self._combo_survives(player, match.round_index):
                player.combo = combo_after(player.combo, is_correct)
            player.best_combo = max(player.best_combo, player.combo)

            if answer is not None:
                # No answer at all earns nothing. Paying mana for sitting out
                # would make waiting a viable way to charge a skill.
                player.mana = gain_mana(
                    player.mana, award_mana(is_correct, answer.elapsed_ms, limit)
                )

            blows[user_id] = resolve_blow(
                is_correct=is_correct,
                elapsed_ms=answer.elapsed_ms if answer is not None else 0,
                time_limit_seconds=limit,
                combo_count=player.combo,
                attacker_damage_permille=self._attack_permille(
                    player, opponent, match.round_index
                ),
                defender_reduction_permille=self._defence_permille(
                    opponent, match.round_index
                ),
            )

        for user_id, blow in blows.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            opponent.hp = apply_damage(opponent.hp, blow.final_damage)
            if blow.stuns_opponent:
                # The round after this one, never the one being settled.
                opponent.stunned_round_index = match.round_index + 1

        if any(player.hp <= 0 for player in match.players.values()):
            match.ko_pending = True

        return blows, {user_id: p.hp for user_id, p in match.players.items()}

    def _combo_survives(self, player: PlayerConn, round_index: int) -> bool:
        """Whether a miss is forgiven this round by a standing COMBO_KEEP."""
        return player.effects.consume(SkillEffect.COMBO_KEEP, round_index) is not None

    def _attack_permille(
        self, player: PlayerConn, opponent: PlayerConn | None, round_index: int
    ) -> int:
        """The attacker's damage scaling: class, then whatever they cast.

        Skills change this number; they never add a branch to `resolve_blow`.
        """
        permille = player.build.damage_permille
        boost = player.effects.consume(SkillEffect.DOUBLE_DAMAGE, round_index)
        if boost is not None:
            permille = permille * boost.magnitude // 100
        execute = player.effects.consume(SkillEffect.EXECUTE, round_index)
        # An execute only pays off against an opponent already low enough,
        # which is checked now rather than when the skill was cast.
        if execute is not None and opponent is not None and 0 < opponent.hp <= execute.magnitude:
            permille *= 2
        return permille

    def _defence_permille(self, player: PlayerConn | None, round_index: int) -> int:
        if player is None:
            return 0
        shield = player.effects.consume(SkillEffect.DAMAGE_REDUCTION, round_index)
        return shield.magnitude if shield is not None else 0

    def _close_round_if_everyone_answered(self, match: LiveMatch) -> None:
        """Advance as soon as nobody is still expected to answer.

        Only connected players count, so a player dropping mid-round does not
        leave the other one waiting out the full timer. A stunned player is not
        expected either -- they cannot answer, so counting them would hold the
        round open until the timer expired every single time.

        When the stunned player is the only one left connected, `expected` is
        empty and the round correctly runs its full course: there is nobody
        able to close it early.
        """
        expected = {
            player.user_id
            for player in match.connected_players()
            if not player.is_stunned_for(match.round_index)
        }
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

        rewards = MatchRewards.empty()
        if match.persisted:
            # Shielded because a reconnect can cancel the grace timer that is
            # running this very call; being cancelled midway through settling
            # would leave the payout half applied.
            rewards = await asyncio.shield(
                self.persistence.save_result(
                    match,
                    MatchResult(
                        outcome_by_user=outcomes,
                        winner_id=winner_id,
                        end_reason=end_reason,
                        duration_seconds=duration,
                        forfeit_user_id=forfeit_user_id,
                        knockout=end_reason is DuoMatchEndReason.KNOCKOUT,
                    ),
                )
            )

        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            change = rewards.rating.get(user_id)
            exp = rewards.exp.get(user_id)
            gold = rewards.gold.get(user_id)
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
                    exp=_exp_change(exp),
                    gold=_gold_change(gold),
                    your_hp_left=player.hp,
                    opponent_hp_left=opponent.hp,
                    loot=_loot_read(rewards.loot.get(user_id)),
                    season=_season_read(rewards.season.get(user_id)),
                    streak=_streak_read(rewards.streak.get(user_id)),
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
        # A settled match is never un-settled. `persisted` is never reset, so
        # anything raising after `_finish` returned would otherwise land here
        # and cancel a match that has already paid out its rewards.
        if match.status is DuoMatchStatus.FINISHED:
            await self._teardown(match)
            return
        match.status = DuoMatchStatus.CANCELLED
        # `create_match` can raise after the start has already charged energy;
        # `persisted` is still False at that point, so this is the only place
        # that window is covered.
        if match.energy_spent:
            match.energy_spent = False
            await self.persistence.refund_start(match)
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

    async def _broadcast_round_start(
        self, match: LiveMatch, index: int, question: ChallengePublicRead, limit: int
    ) -> None:
        """Sent per player rather than broadcast: health, mana and the stun
        flag differ between the two sides."""
        deadline = datetime.now(UTC) + timedelta(seconds=limit)
        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            await _send(
                player.websocket,
                ServerEvent.ROUND_START,
                RoundStartData(
                    round_index=index,
                    total_rounds=len(match.questions),
                    question=question,
                    time_limit_seconds=limit,
                    deadline_at=deadline,
                    your_hp=player.hp,
                    opponent_hp=opponent.hp,
                    your_mana=player.mana,
                    your_combo=player.combo,
                    your_time_limit_seconds=player.effective_limit_ms // 1000,
                    you_are_stunned=player.is_stunned_for(index),
                ),
            )

    async def _broadcast_round_result(
        self,
        match: LiveMatch,
        index: int,
        question: ChallengePublicRead,
        blows: dict[str, Blow],
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
                    your_blow=_blow_read(blows.get(user_id)),
                    opponent_blow=_blow_read(blows.get(opponent.user_id)),
                    your_hp=player.hp,
                    opponent_hp=opponent.hp,
                    your_mana=player.mana,
                    your_combo=player.combo,
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
        hp_left=player.hp,
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


def _blow_read(blow: Blow | None) -> BlowRead:
    """A player with no blow this round reports an empty one."""
    resolved = blow if blow is not None else Blow.none()
    return BlowRead(
        damage=resolved.final_damage,
        strike=resolved.strike,
        combo_count=resolved.combo_count,
        combo_multiplier=resolved.combo_multiplier,
        is_critical=resolved.is_critical,
        stuns_opponent=resolved.stuns_opponent,
        element_multiplier=resolved.element_multiplier,
    )


def _loot_read(drop: LootDrop | None) -> LootDropRead | None:
    if drop is None:
        return None
    return LootDropRead(code=drop.code, name=drop.name, rarity=drop.rarity)


def _season_read(change: SeasonChange | None) -> SeasonChangeRead | None:
    if change is None:
        return None
    return SeasonChangeRead(
        season_code=change.season_code,
        rating_before=change.rating_before,
        rating_after=change.rating_after,
        tier_before=change.tier_before,
        tier_after=change.tier_after,
        promoted=change.promoted,
    )


def _streak_read(change: StreakChange | None) -> StreakChangeRead | None:
    if change is None:
        return None
    return StreakChangeRead(
        day_streak=change.day_streak,
        best_day_streak=change.best_day_streak,
        extended=change.extended,
    )


def _exp_change(award: ExpAward | None) -> ExpChange:
    """An unpaid match (never persisted, or already settled) reports no change."""
    if award is None:
        return ExpChange(
            before=0, after=0, delta=0, level_before=1, level_after=1, leveled_up=False
        )
    return ExpChange(
        before=award.before,
        after=award.after,
        delta=award.delta,
        level_before=award.level_before,
        level_after=award.level_after,
        leveled_up=award.leveled_up,
    )


def _gold_change(award: GoldAward | None) -> GoldChange:
    if award is None:
        return GoldChange(before=0, after=0, delta=0)
    return GoldChange(before=award.before, after=award.after, delta=award.delta)


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
