"""The duo match engine: matchmaking, the realtime loop, grading and teardown.

One `asyncio.Task` per match drives the whole game (`_run_match`). There are no
rounds and no lock step: each player holds their own deck of questions and
works through it at their own pace, and a correct answer lands its blow on the
tick the tap arrives rather than at the end of a shared round. Whoever answers
first strikes first, and a wrong answer sends its question to the back of that
player's own deck to be met again.

Because the loop owns its own timing there are no detached callbacks to leak
when a room is torn down; cancelling the task cancels the match.

Answer timing is measured here, from the instant the question was pushed to
that player, and never taken from the client.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel

from app.core.config import settings as app_settings
from app.models.auth.user import User
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.models.game.skill import SkillEffect
from app.schemas.duo.events import (
    ActiveEffectRead,
    AnswerResultData,
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
    PongData,
    QuestionPushData,
    QueueWaitingData,
    RatingChange,
    RoomCreatedData,
    SeasonChangeRead,
    ServerEvent,
    SkillUsedData,
    StateTickData,
    StreakChangeRead,
    envelope,
)
from app.services.auth.avatar_url import resolve_avatar_url
from app.services.duo import clock
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
    SubmittedAnswer,
)
from app.services.game.combat import (
    Blow,
    apply_damage,
    award_mana,
    combo_after,
    gain_mana,
    resolve_blow,
)
from app.services.game.loadout import ActiveEffect, EquippedSkill, SkillUseRecord
from app.services.game.settlement import (
    ExpAward,
    GoldAward,
    LootDrop,
    SeasonChange,
    StreakChange,
)

logger = logging.getLogger(__name__)

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
        standing = await self.persistence.load_standing(user.id)
        await _send(
            websocket,
            ServerEvent.CONNECTED,
            ConnectedData(
                user=PlayerConn(
                    user_id=user.id,
                    username=user.username,
                    avatar_url=resolve_avatar_url(user, app_settings),
                    standing=standing,
                ).to_read(),
                active_match_id=match.match_id if match is not None else None,
                server_time_ms=clock.server_ms(),
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

        now = clock.seconds()
        question = (
            match.questions[player.current_index]
            if player.current_index is not None
            else None
        )
        await _send(
            websocket,
            ServerEvent.MATCH_RESUME,
            MatchResumeData(
                match_id=match.match_id,
                opponent=opponent.to_read(),
                settings=match.settings.to_read(),
                deck_size=match.deck_size,
                server_time_ms=clock.to_server_ms(now),
                deadline_at=clock.to_server_ms(match.deadline_at),
                # The question the player was on is handed back with its own
                # token, so an answer given before the drop and one given
                # after can still be told apart.
                token=player.question_token,
                question=question,
                pushed_at=(
                    clock.to_server_ms(player.question_pushed_at)
                    if player.question_pushed_at is not None
                    else 0
                ),
                your_deck_remaining=player.deck_remaining,
                opponent_deck_remaining=opponent.deck_remaining,
                your_score=player.score,
                opponent_score=opponent.score,
                your_hp=player.hp,
                your_max_hp=player.build.max_hp,
                opponent_hp=opponent.hp,
                opponent_max_hp=opponent.build.max_hp,
                your_mana=player.mana,
                your_combo=player.combo,
                opponent_combo=opponent.combo,
                lockout_ends_at=_stamp(player.lockout_until),
                stunned_until=_stamp(player.stunned_until),
                effects=_effects_read(player, now),
            ),
        )
        await self._notify_opponent(match, user.id, ServerEvent.OPPONENT_RECONNECTED, None)

    async def pong(self, websocket: WebSocket, client_time_ms: int) -> None:
        """Echo the client's stamp back beside the server's own, so the client
        can work out its offset and half the round trip."""
        await _send(
            websocket,
            ServerEvent.PONG,
            PongData(client_time_ms=client_time_ms, server_time_ms=clock.server_ms()),
        )

    # --- matchmaking --------------------------------------------------------

    async def join_queue(
        self, user: User, websocket: WebSocket, settings: MatchSettings
    ) -> None:
        """Match against a waiting opponent immediately, or start waiting.

        The lookup and match creation happen under the registry lock so two
        players joining at the same instant can never be paired twice.
        """
        standing = await self.persistence.load_standing(user.id)
        entry = QueueEntry(
            user_id=user.id,
            standing=standing,
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
        standing = await self.persistence.load_standing(user.id)
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
                standing=standing,
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
        standing = await self.persistence.load_standing(user.id)
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
                standing=standing,
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
        self, user_id: str, websocket: WebSocket, token: str, option_id: str
    ) -> None:
        """Resolve one answer, here and now.

        Graded against the key loaded when the match started, so the blow lands
        on the tick the tap arrived rather than at the end of a round the
        opponent also has to finish. `elapsed_ms` is measured from this
        player's own question clock, so a client cannot inflate its speed bonus
        by lying about timing.
        """
        match = self.registry.match_of_user(user_id)
        if match is None or match.status is not DuoMatchStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No match in progress")
            return

        player = match.players[user_id]
        index = player.current_index
        # An open question is this player's own token with a live clock;
        # anything else is a late or replayed submission.
        if index is None or player.question_token != token:
            await _send_error(
                websocket, ErrorCode.QUESTION_CLOSED, "That question is no longer open"
            )
            return
        now = clock.seconds()
        # The question stays on screen through a stun, and its clock keeps
        # running: what a five-answer combo takes from the opponent is exactly
        # those seconds.
        if player.is_stunned(now):
            await _send_error(websocket, ErrorCode.STUNNED, "You are stunned")
            return

        question = match.questions[index]
        if option_id not in {option.id for option in question.options}:
            await _send_error(
                websocket, ErrorCode.INVALID_OPTION, "That option is not on this question"
            )
            return

        elapsed_ms = player.elapsed_ms(now)
        # Shut the question before anything is resolved, so a double tap in the
        # same millisecond cannot land two blows.
        player.question_token = None
        player.question_pushed_at = None
        player.current_index = None

        correct_ids = match.answer_key.get(question.id, [])
        is_correct = option_id in correct_ids
        answer = SubmittedAnswer(
            question_index=index,
            question_id=question.id,
            option_id=option_id,
            elapsed_ms=elapsed_ms,
            is_correct=is_correct,
            points=award_points(is_correct, elapsed_ms, match.settings.time_per_question),
            correct_option_ids=list(correct_ids),
            explanation=match.explanations.get(question.id),
        )

        opponent = match.opponent_of(user_id)
        blow = self._settle_answer(match, player, opponent, answer, now)
        if not is_correct:
            # To the back of the deck, to be met again later. This is the whole
            # point of the deck: a question is not survived, it is learned.
            player.deck.append(index)
        lockout_ms = (
            clock.CORRECT_LOCKOUT_MS if is_correct else clock.WRONG_LOCKOUT_MS
        )
        player.lockout_until = now + lockout_ms / 1000.0

        await _send(
            player.websocket,
            ServerEvent.ANSWER_RESULT,
            AnswerResultData(
                token=token,
                correct=is_correct,
                option_id=option_id,
                elapsed_ms=elapsed_ms,
                correct_option_ids=answer.correct_option_ids,
                explanation=answer.explanation,
                points=answer.points,
                blow=_blow_read(blow),
                your_score=player.score,
                your_mana=player.mana,
                your_combo=player.combo,
                your_deck_remaining=player.deck_remaining,
                opponent_hp=max(0, opponent.hp) if opponent is not None else 0,
                lockout_ends_at=_stamp(player.lockout_until),
            ),
        )
        if opponent is None:
            return
        await _send(
            opponent.websocket,
            ServerEvent.OPPONENT_ANSWERED,
            OpponentAnsweredData(
                correct=is_correct,
                damage=blow.final_damage if blow is not None else 0,
                is_critical=blow.is_critical if blow is not None else False,
                your_hp=max(0, opponent.hp),
                opponent_score=player.score,
                opponent_combo=player.combo,
                opponent_deck_remaining=player.deck_remaining,
                your_stunned_until=_stamp(opponent.stunned_until),
            ),
        )

    def _settle_answer(
        self,
        match: LiveMatch,
        player: PlayerConn,
        opponent: PlayerConn | None,
        answer: SubmittedAnswer,
        now: float,
    ) -> Blow | None:
        """Turn one answer into points, mana, a combo and possibly a blow.

        The blow is applied to the opponent immediately. That is the whole
        change from the lock-step engine: there is no round to wait for, so
        whoever answers first is the one whose blow lands first.
        """
        limit = match.settings.time_per_question

        player.answers_given += 1
        player.score += answer.points
        player.total_elapsed_ms += answer.elapsed_ms
        if answer.is_correct or not self._combo_survives(player, now):
            player.combo = combo_after(player.combo, answer.is_correct)
        player.best_combo = max(player.best_combo, player.combo)
        player.mana = gain_mana(
            player.mana, award_mana(answer.is_correct, answer.elapsed_ms, limit)
        )
        if not answer.is_correct:
            return None

        player.correct_count += 1
        blow = resolve_blow(
            is_correct=True,
            elapsed_ms=answer.elapsed_ms,
            time_limit_seconds=limit,
            combo_count=player.combo,
            attacker_damage_permille=self._attack_permille(player, opponent, now),
            defender_reduction_permille=self._defence_permille(opponent, now),
        )
        if opponent is not None:
            opponent.hp = apply_damage(opponent.hp, blow.final_damage)
            if blow.stuns_opponent:
                # Seconds rather than a round to sit out: there are no rounds
                # left to skip, so a combo takes the opponent's clock instead.
                opponent.stunned_until = max(
                    opponent.stunned_until, now + clock.STUN_SECONDS
                )
        return blow

    async def use_skill(self, user_id: str, websocket: WebSocket, skill_code: str) -> None:
        """Cast one equipped skill.

        Everything is checked against the loadout resolved at match start, so
        this never touches the database: a live match must not wait on a query,
        and a player must not be able to re-equip mid-fight. Nothing here is
        tied to a question -- raising a shield while reading the last
        explanation is exactly when it is worth the mana.
        """
        match = self.registry.match_of_user(user_id)
        if match is None or match.status is not DuoMatchStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No match in progress")
            return

        player = match.players[user_id]
        opponent = match.opponent_of(user_id)
        if opponent is None:
            await _send_error(websocket, ErrorCode.NOT_IN_MATCH, "No opponent")
            return

        now = clock.seconds()
        if player.is_stunned(now):
            await _send_error(websocket, ErrorCode.STUNNED, "You are stunned")
            return

        skill = player.build.skill(skill_code)
        if skill is None:
            await _send_error(
                websocket, ErrorCode.SKILL_NOT_EQUIPPED, "That skill is not equipped"
            )
            return
        if now < player.skill_ready_at.get(skill_code, 0.0):
            await _send_error(
                websocket, ErrorCode.SKILL_ON_COOLDOWN, "That skill is still recharging"
            )
            return
        if player.mana < skill.mana_cost:
            await _send_error(websocket, ErrorCode.NOT_ENOUGH_MANA, "Not enough mana")
            return
        # Revealing options with no question on screen would be pointless, and
        # letting it through would still cost the player their mana.
        if skill.effect is SkillEffect.REMOVE_OPTIONS and player.current_index is None:
            await _send_error(
                websocket, ErrorCode.QUESTION_CLOSED, "There is no question to narrow"
            )
            return

        player.mana -= skill.mana_cost
        ready_again = now + _cooldown_seconds(skill)
        player.skill_ready_at[skill_code] = ready_again
        match.skill_log.append(
            SkillUseRecord(
                # The column still means "how far into the match", which with
                # no rounds left to count is the caster's answer count.
                round_index=player.answers_given,
                user_id=user_id,
                skill_id=skill.skill_id,
                skill_code=skill.code,
                mana_spent=skill.mana_cost,
            )
        )
        private = self._apply_skill(match, player, opponent, skill, now)
        await self._broadcast_skill_used(match, user_id, skill, private, ready_again)

    def _apply_skill(
        self,
        match: LiveMatch,
        player: PlayerConn,
        opponent: PlayerConn,
        skill: EquippedSkill,
        now: float,
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
            return {"removed_option_ids": self._removed_options(match, player, skill.magnitude)}
        if effect is SkillEffect.TIME_PENALTY:
            # Authored as "shorten the opponent's clock by N seconds". No
            # question has a deadline any more, so it takes those N seconds off
            # them the other way round: they wait that much longer before their
            # next question arrives.
            opponent.lockout_until = max(opponent.lockout_until, now) + skill.magnitude
            return None

        # The rest stand until they are spent or expire. A skill authored for
        # one round stands for `SECONDS_PER_ROUND` seconds.
        player.add_effect(
            ActiveEffect(
                effect=effect,
                magnitude=skill.magnitude,
                expires_at=now + max(1, skill.duration_rounds) * clock.SECONDS_PER_ROUND,
                source_code=skill.code,
            )
        )
        return None

    def _removed_options(
        self, match: LiveMatch, player: PlayerConn, count: int
    ) -> list[str]:
        """Wrong options to hide from the caster, never broadcast."""
        if player.current_index is None:
            return []
        question = match.questions[player.current_index]
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
        ready_again: float,
    ) -> None:
        """Both sides see what was cast; only the caster sees the private part."""
        for user_id, player in match.players.items():
            other = match.opponent_of(user_id)
            mine = user_id == caster_id
            await _send(
                player.websocket,
                ServerEvent.SKILL_USED,
                SkillUsedData(
                    user_id=caster_id,
                    skill_code=skill.code,
                    skill_name=skill.name,
                    effect=skill.effect,
                    magnitude=skill.magnitude,
                    mana_spent=skill.mana_cost,
                    your_hp=max(0, player.hp),
                    opponent_hp=max(0, other.hp) if other is not None else 0,
                    your_mana=player.mana,
                    ready_again_at=_stamp(ready_again) if mine else 0,
                    lockout_ends_at=_stamp(player.lockout_until),
                    private=private if mine else None,
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
        """The task body: countdown, the first question for each player, then
        advance the match until one of them falls or clears their deck."""
        try:
            await asyncio.sleep(clock.COUNTDOWN_SECONDS)
            if not await self._prepare(match):
                return
            now = clock.seconds()
            for player in match.players.values():
                await self._push_question(match, player, now)
            while match.status is DuoMatchStatus.IN_PROGRESS:
                await asyncio.sleep(clock.TICK_SECONDS)
                if match.status is not DuoMatchStatus.IN_PROGRESS:
                    return
                await self._tick(match, clock.seconds())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Duo match %s crashed", match.match_id)
            await self._abort(match)

    async def _prepare(self, match: LiveMatch) -> bool:
        """Draw the deck and persist the match row before the first question.

        Returns False (and aborts the match) when there aren't enough
        questions to play, so the caller knows not to start the loop.
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

        match.questions = question_set.questions
        match.answer_key = question_set.answer_key
        match.explanations = question_set.explanations

        now = clock.seconds()
        for user_id, player in match.players.items():
            player.loadout = loadouts.get(user_id)
            player.hp = player.build.max_hp
            player.mana = player.build.starting_mana
            # Both decks hold the same questions in the same order: the two
            # players are running the same race, and only their own mistakes
            # make the decks diverge.
            player.deck = deque(range(len(match.questions)))

        match.status = DuoMatchStatus.IN_PROGRESS
        match.started_at = now
        match.deadline_at = now + clock.match_time_cap(match.settings)
        match.started_wall = datetime.now(UTC)

        await self.persistence.create_match(match)
        match.persisted = True

        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            await _send(
                player.websocket,
                ServerEvent.MATCH_STARTED,
                MatchStartedData(
                    match_id=match.match_id,
                    deck_size=match.deck_size,
                    speed_reference_seconds=match.settings.time_per_question,
                    tick_hz=clock.TICK_HZ,
                    snapshot_hz=clock.SNAPSHOT_HZ,
                    server_time_ms=clock.to_server_ms(now),
                    deadline_at=clock.to_server_ms(match.deadline_at),
                    your_hp=player.hp,
                    your_max_hp=player.build.max_hp,
                    your_mana=player.mana,
                    opponent_hp=opponent.hp if opponent is not None else 0,
                    opponent_max_hp=opponent.build.max_hp if opponent is not None else 0,
                ),
            )
        return True

    async def _tick(self, match: LiveMatch, now: float) -> None:
        """One step of the simulation.

        The order matters. Effects are aged and empty decks latched first, so
        the end check below sees the same reading for both players; a match
        that is already over neither pushes a question nobody will answer nor
        broadcasts a snapshot of a fight that has stopped.
        """
        for player in match.players.values():
            player.effects.prune(now)
            # Latched only once the lockout is up, so the player who just
            # cleared their deck still gets the beat that shows their last
            # blow landing before the result screen takes over.
            if (
                player.question_token is None
                and not player.deck
                and now >= player.ready_at()
            ):
                player.deck_cleared = True

        end_reason = _end_reason(match, now)
        if end_reason is not None:
            await self._finish(match, end_reason)
            return

        for player in match.players.values():
            if player.question_token is None and player.deck and now >= player.ready_at():
                await self._push_question(match, player, now)

        if now - match.last_snapshot_at >= clock.SNAPSHOT_SECONDS:
            match.last_snapshot_at = now
            await self._broadcast_snapshot(match, now)

    async def _push_question(
        self, match: LiveMatch, player: PlayerConn, now: float
    ) -> None:
        """Put this player's next question on their screen.

        Only their own: the two players share a deck's contents, never a
        position in it.
        """
        index = player.deck.popleft()
        question = match.questions[index]
        retry = index in player.seen
        player.seen.add(index)
        player.current_index = index
        player.question_token = uuid4().hex
        player.question_pushed_at = now
        await _send(
            player.websocket,
            ServerEvent.QUESTION_PUSH,
            QuestionPushData(
                token=player.question_token,
                question=question,
                pushed_at=clock.to_server_ms(now),
                deck_remaining=player.deck_remaining,
                retry=retry,
            ),
        )

    async def _broadcast_snapshot(self, match: LiveMatch, now: float) -> None:
        """The whole match, per player: every "your"/"opponent" pair is
        inverted between the two sides, so this is not a broadcast."""
        stamp = clock.to_server_ms(now)
        for user_id, player in match.players.items():
            opponent = match.opponent_of(user_id)
            if opponent is None:
                continue
            await _send(
                player.websocket,
                ServerEvent.STATE_TICK,
                StateTickData(
                    t=stamp,
                    your_hp=max(0, player.hp),
                    your_max_hp=player.build.max_hp,
                    your_mana=player.mana,
                    your_combo=player.combo,
                    your_score=player.score,
                    your_deck_remaining=player.deck_remaining,
                    opponent_hp=max(0, opponent.hp),
                    opponent_max_hp=opponent.build.max_hp,
                    opponent_combo=opponent.combo,
                    opponent_score=opponent.score,
                    opponent_deck_remaining=opponent.deck_remaining,
                    deadline_at=clock.to_server_ms(match.deadline_at),
                    lockout_ends_at=_stamp(player.lockout_until),
                    stunned_until=_stamp(player.stunned_until),
                    effects=_effects_read(player, now),
                ),
            )

    # --- modifiers ----------------------------------------------------------

    def _combo_survives(self, player: PlayerConn, now: float) -> bool:
        """Whether a miss is forgiven by a standing COMBO_KEEP."""
        return player.effects.consume(SkillEffect.COMBO_KEEP, now) is not None

    def _attack_permille(
        self, player: PlayerConn, opponent: PlayerConn | None, now: float
    ) -> int:
        """The attacker's damage scaling: class, then whatever they cast.

        Skills change this number; they never add a branch to `resolve_blow`.
        """
        permille = player.build.damage_permille
        boost = player.effects.consume(SkillEffect.DOUBLE_DAMAGE, now)
        if boost is not None:
            permille = permille * boost.magnitude // 100
        execute = player.effects.consume(SkillEffect.EXECUTE, now)
        # An execute only pays off against an opponent already low enough,
        # which is checked now rather than when the skill was cast.
        if execute is not None and opponent is not None and 0 < opponent.hp <= execute.magnitude:
            permille *= 2
        return permille

    def _defence_permille(self, player: PlayerConn | None, now: float) -> int:
        if player is None:
            return 0
        shield = player.effects.consume(SkillEffect.DAMAGE_REDUCTION, now)
        return shield.magnitude if shield is not None else 0

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
        a forfeit and a knockout can only ever run this once.
        """
        if match.status is DuoMatchStatus.FINISHED:
            return
        match.status = DuoMatchStatus.FINISHED
        match.end_reason = end_reason
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
                    deck_size=match.deck_size,
                    your_deck_cleared=player.deck_cleared,
                    opponent_deck_cleared=opponent.deck_cleared,
                    duration_seconds=duration,
                    rating=RatingChange(
                        before=change.before if change else player.rating,
                        after=change.after if change else player.rating,
                        delta=change.delta if change else 0,
                    ),
                    exp=_exp_change(exp),
                    gold=_gold_change(gold),
                    your_hp_left=max(0, player.hp),
                    opponent_hp_left=max(0, opponent.hp),
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
            standing=entry.standing,
            websocket=entry.websocket,
        )
    return match


def _end_reason(match: LiveMatch, now: float) -> DuoMatchEndReason | None:
    """Why the match should stop, or None to keep playing.

    A knockout outranks a cleared deck: if the answer that emptied someone's
    deck also dropped the other player to zero, the fight is what ended it.
    """
    if any(player.is_down for player in match.players.values()):
        return DuoMatchEndReason.KNOCKOUT
    if any(player.deck_cleared for player in match.players.values()):
        return DuoMatchEndReason.DECK_CLEARED
    if match.deadline_at > 0 and now >= match.deadline_at:
        return DuoMatchEndReason.TIME_UP
    return None


def _decide_outcomes(
    match: LiveMatch,
    one_id: str,
    two_id: str,
    forfeit_user_id: str | None,
) -> dict[str, MatchOutcome]:
    """A forfeit always loses regardless of score; otherwise fall back to
    the normal deck/health/score tiebreak in scoring.decide_outcome."""
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
        hp_left=max(0, player.hp),
        deck_cleared=player.deck_cleared,
    )


def _duration_seconds(match: LiveMatch) -> int:
    if match.started_wall is None:
        return 0
    return max(0, int((datetime.now(UTC) - match.started_wall).total_seconds()))


def _stamp(at: float) -> int:
    """A monotonic instant on the published scale, or zero when it is unset.

    Zero rather than a stamp because an unset deadline is a monotonic zero,
    which converts to a large negative instant that means nothing to a client.
    """
    return clock.to_server_ms(at) if at > 0 else 0


def _effects_read(player: PlayerConn, now: float) -> list[ActiveEffectRead]:
    return [
        ActiveEffectRead(
            code=effect.source_code,
            effect=effect.effect,
            magnitude=effect.magnitude,
            expires_at=clock.to_server_ms(effect.expires_at),
        )
        for effect in player.effects.effects
        if effect.expires_at >= now
    ]


def _cooldown_seconds(skill: EquippedSkill) -> float:
    """How long before this skill may be cast again.

    Its own duration, so an effect can never be stacked on top of itself, and
    never less than the floor -- a zero-duration skill would otherwise be
    limited by mana alone, which is not a fast enough gate when questions
    arrive back to back.
    """
    return max(
        clock.SKILL_MIN_COOLDOWN_SECONDS, skill.duration_rounds * clock.SECONDS_PER_ROUND
    )


def _blow_read(blow: Blow | None) -> BlowRead | None:
    if blow is None:
        return None
    return BlowRead(
        damage=blow.final_damage,
        strike=blow.strike,
        combo_count=blow.combo_count,
        combo_multiplier=blow.combo_multiplier,
        is_critical=blow.is_critical,
        stuns_opponent=blow.stuns_opponent,
        element_multiplier=blow.element_multiplier,
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
