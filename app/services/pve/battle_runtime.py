"""The lesson-battle engine: starting a fight, the tick loop, and payout.

One `asyncio.Task` drives one battle (`_run_battle`). It advances the fight
fifteen times a second: it expires effects, lets the monster swing when its cast
finishes, pushes the next question once the player has had their beat to read
the last result, and broadcasts a snapshot every tenth of a second.

Player input does not wait for a tick. `submit_answer` and `use_skill` resolve
on the socket's own coroutine and answer immediately, which is what makes a hit
feel like a hit; asyncio gives them mutual exclusion against the loop for free,
since neither mutates state across an await. The loop stays the authority on
everything the player does not cause: the monster's clock, and the two deaths
that end the fight.

Correctness is decided here, from the answer key loaded when the battle started
-- a tick cannot wait on a query. `ProgressService.check_answer` is still told,
on a background task, and is still the only thing that moves the learn path; it
simply no longer stands between the answer and the sword. `_finish` waits for
those tasks before it reports lesson progress, so the number the player sees is
the number that was written.

Deliberately separate from `services/duo/match_runtime.py`. The two share the
combat maths (`services/game/combat.py`) and nothing else, so a change to
matchmaking, forfeits or the ladder cannot reach this loop.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import WebSocket
from pydantic import BaseModel

from app.models.auth.user import User
from app.models.content.challenge import ChallengeType
from app.models.game.skill import SkillEffect
from app.models.pve.lesson_battle import BattleEndReason, BattleStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.pve.events import (
    ActiveEffectRead,
    AnswerResultData,
    BattleFinishedData,
    BattleStartedData,
    BlowRead,
    ConnectedData,
    ErrorCode,
    ErrorData,
    ExpChange,
    GoldChange,
    LootDropRead,
    MonsterRead,
    MonsterSwingData,
    PongData,
    QuestionPushData,
    ServerEvent,
    SkillUsedData,
    StateTickData,
    StreakChangeRead,
    envelope,
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
from app.services.game.settlement import ExpAward, GoldAward, LootDrop, StreakChange
from app.services.pve import clock
from app.services.pve.clock import (
    BATTLE_TTL_SECONDS,
    CORRECT_LOCKOUT_MS,
    SECONDS_PER_ROUND,
    SKILL_MIN_COOLDOWN_SECONDS,
    SNAPSHOT_HZ,
    SNAPSHOT_SECONDS,
    SPEED_REFERENCE_SECONDS,
    TICK_HZ,
    TICK_SECONDS,
    WRONG_LOCKOUT_MS,
)
from app.services.pve.monster import (
    cast_interval_seconds,
    element_multiplier,
    monster_attack,
    next_swing,
)
from app.services.pve.persistence import (
    BattlePersistence,
    BattleResult,
    BattleRewards,
    BattleStartRejected,
    DatabaseBattlePersistence,
)
from app.services.pve.registry import BattleRegistry
from app.services.pve.registry import registry as default_registry
from app.services.pve.state import LiveBattle, MonsterState, SubmittedAnswer

logger = logging.getLogger(__name__)

__all__ = ["BATTLE_TTL_SECONDS", "BattleEngine", "engine"]

# The one effect with nothing to bite on: a monster spends no mana. Refused at
# cast time so the player keeps theirs. TIME_PENALTY, which had no target under
# the old rules either, now steals from the monster's cast bar instead.
UNTARGETABLE_EFFECTS = (SkillEffect.MANA_BURN,)


class BattleEngine:
    def __init__(
        self,
        persistence: BattlePersistence | None = None,
        registry: BattleRegistry | None = None,
    ) -> None:
        self.persistence: BattlePersistence = persistence or DatabaseBattlePersistence()
        self.registry = registry or default_registry

    # --- connection ---------------------------------------------------------

    async def on_connect(self, user: User, websocket: WebSocket) -> None:
        battle = self.registry.battle_of_user(user.id)
        await _send(
            websocket,
            ServerEvent.CONNECTED,
            ConnectedData(
                user_id=user.id,
                active_battle_id=battle.battle_id if battle is not None else None,
                server_time_ms=clock.server_ms(),
            ),
        )

    async def on_disconnect(self, user_id: str, websocket: WebSocket) -> None:
        """A dropped socket ends the fight.

        Deliberately unlike duo, which holds a 30-second grace window: losing
        costs nothing here, and every answer already given was written to
        progress as it happened. There is nothing left to rescue, so there is
        no reason to keep a room open in the hope of a reconnect.
        """
        battle = self.registry.battle_of_user(user_id)
        if battle is None:
            return
        # A stale socket from a connection the user already replaced.
        if battle.websocket is not websocket:
            return
        battle.websocket = None
        await self._finish(battle, BattleStatus.ABANDONED, BattleEndReason.LEFT)

    async def pong(self, websocket: WebSocket, client_time_ms: int) -> None:
        """Echo the client's stamp beside the server's, so it can work out the
        offset every published deadline is measured against."""
        await _send(
            websocket,
            ServerEvent.PONG,
            PongData(client_time_ms=client_time_ms, server_time_ms=clock.server_ms()),
        )

    # --- starting -----------------------------------------------------------

    async def start_battle(self, user: User, websocket: WebSocket, lesson_id: str) -> None:
        """Open one fight against the monster guarding this lesson."""
        async with self.registry.lock:
            if self.registry.is_busy(user.id):
                await _send_error(
                    websocket,
                    ErrorCode.BATTLE_ALREADY_ACTIVE,
                    "You are already in a battle",
                )
                return

        setup = await self.persistence.load_setup(user.id, lesson_id)
        if isinstance(setup, BattleStartRejected):
            await _send_error(websocket, setup.reason, setup.message)
            return

        now = clock.seconds()
        battle = LiveBattle(
            battle_id=str(uuid4()),
            user_id=user.id,
            lesson_id=lesson_id,
            lesson_title=setup.lesson_title,
            monster=MonsterState.of(setup.monster, now=now),
            websocket=websocket,
            questions=setup.questions.questions,
            answer_key=setup.questions.answer_key,
            explanations=setup.questions.explanations,
            loadout=setup.loadout,
            started_at=now,
            started_wall=datetime.now(UTC),
        )
        battle.hp = battle.build.max_hp
        battle.mana = battle.build.starting_mana

        async with self.registry.lock:
            # Re-checked inside the lock: loading the setup above is a database
            # round trip, and a second `battle.start` can arrive during it.
            if self.registry.is_busy(user.id):
                await _send_error(
                    websocket,
                    ErrorCode.BATTLE_ALREADY_ACTIVE,
                    "You are already in a battle",
                )
                return
            self.registry.register(battle)

        await self.persistence.create_battle(battle)
        battle.persisted = True

        await _send(
            websocket,
            ServerEvent.BATTLE_STARTED,
            BattleStartedData(
                battle_id=battle.battle_id,
                lesson_id=lesson_id,
                lesson_title=battle.lesson_title,
                questions_in_pool=battle.questions_in_pool,
                monster=_monster_read(battle),
                monster_hp=battle.monster.hp,
                your_hp=battle.hp,
                your_max_hp=battle.build.max_hp,
                your_mana=battle.mana,
                tick_hz=TICK_HZ,
                snapshot_hz=SNAPSHOT_HZ,
                server_time_ms=clock.server_ms(),
            ),
        )
        battle.task = asyncio.create_task(self._run_battle(battle))

    # --- player input -------------------------------------------------------

    async def submit_answer(
        self, user_id: str, websocket: WebSocket, token: str, option_ids: list[str]
    ) -> None:
        """Resolve one answer, here and now.

        `option_ids` is the answer whatever shape the question takes: one
        option for a single-choice round, every word tile in the order it was
        laid down for an ORDER one.

        Graded against the key loaded at setup rather than through the study
        path, so the blow lands on the frame the tap arrived rather than a
        database round trip later. The study path is told immediately after,
        on a task of its own.
        """
        battle = self.registry.battle_of_user(user_id)
        if battle is None or battle.status is not BattleStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_BATTLE, "No battle in progress")
            return

        question = battle.current_question
        if question is None or battle.question_token != token:
            await _send_error(
                websocket, ErrorCode.QUESTION_CLOSED, "That question is no longer open"
            )
            return
        if not _answer_fits(question, option_ids):
            await _send_error(
                websocket, ErrorCode.INVALID_OPTION, "That is not an answer to this question"
            )
            return

        now = clock.seconds()
        elapsed_ms = battle.elapsed_ms(now)
        # Shut the question before anything is resolved, so a double tap in the
        # same millisecond cannot land two blows.
        battle.question_token = None
        battle.question_pushed_at = None

        correct_ids = battle.answer_key.get(question.id, [])
        answer = SubmittedAnswer(
            question_id=question.id,
            option_ids=list(option_ids),
            elapsed_ms=elapsed_ms,
            is_correct=_is_correct(question, correct_ids, option_ids),
            correct_option_ids=list(correct_ids),
            explanation=battle.explanations.get(question.id),
        )
        blow = self._settle_answer(battle, question, answer, now)

        lockout_ms = CORRECT_LOCKOUT_MS if answer.is_correct else WRONG_LOCKOUT_MS
        battle.lockout_until = now + lockout_ms / 1000.0

        await _send(
            battle.websocket,
            ServerEvent.ANSWER_RESULT,
            AnswerResultData(
                token=token,
                correct=answer.is_correct,
                option_id=answer.option_id,
                option_ids=answer.option_ids,
                elapsed_ms=answer.elapsed_ms,
                correct_option_ids=answer.correct_option_ids,
                explanation=answer.explanation,
                blow=_blow_read(blow),
                your_mana=battle.mana,
                combo=battle.combo,
                monster_hp=max(0, battle.monster.hp),
                lockout_ends_at=clock.to_server_ms(battle.lockout_until),
            ),
        )
        self._record_for_progress(battle, answer)

    def _settle_answer(
        self,
        battle: LiveBattle,
        question: ChallengePublicRead,
        answer: SubmittedAnswer,
        now: float,
    ) -> Blow | None:
        """Turn one answer into mana, a combo and possibly a blow.

        Nothing comes back at the player: the monster's damage is on its own
        clock now, so being wrong costs tempo and the combo, never health.
        """
        battle.answers_given += 1
        if answer.is_correct or not self._combo_survives(battle, now):
            battle.combo = combo_after(battle.combo, answer.is_correct)
        battle.best_combo = max(battle.best_combo, battle.combo)
        battle.mana = gain_mana(
            battle.mana,
            award_mana(answer.is_correct, answer.elapsed_ms, SPEED_REFERENCE_SECONDS),
        )
        if not answer.is_correct:
            return None

        battle.correct_count += 1
        blow = resolve_blow(
            is_correct=True,
            elapsed_ms=answer.elapsed_ms,
            time_limit_seconds=SPEED_REFERENCE_SECONDS,
            combo_count=battle.combo,
            attacker_damage_permille=self._attack_permille(battle, now),
            defender_reduction_permille=battle.monster.profile.damage_reduction_permille,
            element_multiplier=element_multiplier(
                battle.monster.profile.weak_topic_id, question.topic_id
            ),
        )
        battle.monster.hp = apply_damage(battle.monster.hp, blow.final_damage)
        return blow

    def _record_for_progress(self, battle: LiveBattle, answer: SubmittedAnswer) -> None:
        """Hand the answer to the study path without waiting for it.

        Skipped once the pool has been round at least once: a recycled question
        is there to keep the fight going, not to mark the lesson twice.
        """
        if battle.pool_pass > 0:
            return

        async def record() -> None:
            with contextlib.suppress(Exception):
                await self.persistence.check_answer(
                    battle.user_id, answer.question_id, answer.option_ids
                )

        task = asyncio.create_task(record())
        battle.grading.add(task)
        task.add_done_callback(battle.grading.discard)

    async def use_skill(self, user_id: str, websocket: WebSocket, skill_code: str) -> None:
        """Cast one equipped skill.

        Everything is checked against the loadout resolved when the battle
        started, so this never touches the database and a player cannot
        re-equip mid-fight.
        """
        battle = self.registry.battle_of_user(user_id)
        if battle is None or battle.status is not BattleStatus.IN_PROGRESS:
            await _send_error(websocket, ErrorCode.NOT_IN_BATTLE, "No battle in progress")
            return

        skill = battle.build.skill(skill_code)
        if skill is None:
            await _send_error(websocket, ErrorCode.SKILL_NOT_EQUIPPED, "That skill is not equipped")
            return
        if skill.effect in UNTARGETABLE_EFFECTS:
            # Refused before the cost is taken: a monster has no mana to burn,
            # and paying for nothing is worse than a no-op.
            await _send_error(
                websocket,
                ErrorCode.SKILL_NO_TARGET,
                "That skill has no target in a monster battle",
            )
            return

        now = clock.seconds()
        if now < battle.skill_ready_at.get(skill_code, 0.0):
            await _send_error(
                websocket, ErrorCode.SKILL_ON_COOLDOWN, "That skill is still recharging"
            )
            return
        if battle.mana < skill.mana_cost:
            await _send_error(websocket, ErrorCode.NOT_ENOUGH_MANA, "Not enough mana")
            return
        # Revealing options with no question on screen would be pointless, and
        # letting it through would still cost the player their mana.
        if skill.effect is SkillEffect.REMOVE_OPTIONS and battle.current_question is None:
            await _send_error(
                websocket, ErrorCode.QUESTION_CLOSED, "There is no question to narrow"
            )
            return
        # An ORDER question has no wrong tile to hide -- every one of them
        # belongs in the sentence. Refused before the mana is taken, for the
        # same reason the cast above is.
        if (
            skill.effect is SkillEffect.REMOVE_OPTIONS
            and battle.current_question is not None
            and battle.current_question.type is ChallengeType.ORDER
        ):
            await _send_error(
                websocket, ErrorCode.SKILL_NO_TARGET, "There is nothing to narrow on this question"
            )
            return

        battle.mana -= skill.mana_cost
        ready_again = now + _cooldown_seconds(skill)
        battle.skill_ready_at[skill_code] = ready_again
        battle.skill_log.append(
            SkillUseRecord(
                round_index=battle.answers_given,
                user_id=user_id,
                skill_id=skill.skill_id,
                skill_code=skill.code,
                mana_spent=skill.mana_cost,
            )
        )
        private = self._apply_skill(battle, skill, now)
        await _send(
            battle.websocket,
            ServerEvent.SKILL_USED,
            SkillUsedData(
                skill_code=skill.code,
                skill_name=skill.name,
                effect=skill.effect,
                magnitude=skill.magnitude,
                mana_spent=skill.mana_cost,
                your_hp=battle.hp,
                your_mana=battle.mana,
                monster_hp=max(0, battle.monster.hp),
                cast_ends_at=clock.to_server_ms(battle.monster.cast_ready_at),
                ready_again_at=clock.to_server_ms(ready_again),
                private=private,
            ),
        )

    def _apply_skill(
        self, battle: LiveBattle, skill: EquippedSkill, now: float
    ) -> dict[str, object] | None:
        """Do what the skill does, and return anything only the caster may see."""
        if skill.effect is SkillEffect.HEAL:
            battle.hp = min(battle.build.max_hp, battle.hp + skill.magnitude)
            return None
        if skill.effect is SkillEffect.REMOVE_OPTIONS:
            return {"removed_option_ids": self._removed_options(battle, skill.magnitude)}
        if skill.effect is SkillEffect.TIME_PENALTY:
            # Authored as "shorten the opponent's clock by N seconds". There is
            # no opponent clock to shorten here, so it buys the player the same
            # N seconds by pushing the monster's cast back instead.
            battle.monster.cast_ready_at += skill.magnitude
            return None

        # The rest stand until they are spent or expire. A skill authored for
        # one round stands for `SECONDS_PER_ROUND` seconds.
        battle.add_effect(
            ActiveEffect(
                effect=skill.effect,
                magnitude=skill.magnitude,
                expires_at=now + max(1, skill.duration_rounds) * SECONDS_PER_ROUND,
                source_code=skill.code,
            )
        )
        return None

    def _removed_options(self, battle: LiveBattle, count: int) -> list[str]:
        """Wrong options to hide from the player."""
        question = battle.current_question
        if question is None:
            return []
        correct = set(battle.answer_key.get(question.id, []))
        wrong = [option.id for option in question.options if option.id not in correct]
        # Always leave one wrong answer standing, so a reveal narrows the
        # choice rather than handing it over.
        return wrong[: min(count, max(0, len(wrong) - 1))]

    async def leave_battle(self, user_id: str) -> None:
        """Voluntary exit. The answers already given keep their progress."""
        battle = self.registry.battle_of_user(user_id)
        if battle is None:
            return
        await self._finish(battle, BattleStatus.ABANDONED, BattleEndReason.LEFT)

    # --- battle loop --------------------------------------------------------

    async def _run_battle(self, battle: LiveBattle) -> None:
        """The task body: advance the fight until one of the two falls."""
        try:
            await self._push_question(battle, clock.seconds())
            while battle.status is BattleStatus.IN_PROGRESS:
                await asyncio.sleep(TICK_SECONDS)
                if battle.status is not BattleStatus.IN_PROGRESS:
                    return
                await self._tick(battle, clock.seconds())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lesson battle %s crashed", battle.battle_id)
            await self._abort(battle)

    async def _tick(self, battle: LiveBattle, now: float) -> None:
        """One step of the simulation.

        The order matters. Deaths are settled first so a fight that is already
        over neither swings again nor pushes a question nobody will answer; the
        monster then swings if its cast finished; the next question follows once
        the player has had their beat; and the snapshot goes out last, carrying
        whatever the first three did.
        """
        battle.effects.prune(now)

        if battle.monster.is_down:
            await self._finish(battle, BattleStatus.WON, BattleEndReason.MONSTER_DOWN)
            return
        if battle.is_down:
            await self._finish(battle, BattleStatus.LOST, BattleEndReason.PLAYER_DOWN)
            return

        if now >= battle.monster.cast_ready_at:
            await self._monster_swing(battle, now)
            if battle.is_down:
                await self._finish(battle, BattleStatus.LOST, BattleEndReason.PLAYER_DOWN)
                return

        if battle.question_token is None and now >= battle.lockout_until:
            await self._push_question(battle, now)

        if now - battle.last_snapshot_at >= SNAPSHOT_SECONDS:
            battle.last_snapshot_at = now
            await self._broadcast_snapshot(battle, now)

    async def _push_question(self, battle: LiveBattle, now: float) -> None:
        """Put the next question on screen.

        The pool is a ring rather than a list with an end: a lesson holds about
        ten questions and a slow fight outlasts them, so running out means going
        round again rather than losing. Only the health bars end a battle.
        """
        index = battle.question_cursor % battle.questions_in_pool
        if battle.question_cursor > 0 and index == 0:
            battle.pool_pass += 1
        question = battle.questions[index]
        battle.question_cursor += 1
        battle.question_token = uuid4().hex
        battle.question_pushed_at = now
        await _send(
            battle.websocket,
            ServerEvent.QUESTION_PUSH,
            QuestionPushData(
                token=battle.question_token,
                question=question,
                pushed_at=clock.to_server_ms(now),
                pool_pass=battle.pool_pass,
            ),
        )

    async def _monster_swing(self, battle: LiveBattle, now: float) -> None:
        """The cast finished. Nothing the player did caused this."""
        attack = monster_attack(
            monster=battle.monster.profile,
            elapsed_seconds=now - battle.started_at,
            defender_reduction_permille=self._defence_permille(battle, now),
            defender_flat_reduction=battle.build.defence,
        )
        battle.hp = apply_damage(battle.hp, attack.final_damage)
        if attack.final_damage > 0:
            battle.took_damage = True
        battle.monster.swings += 1
        battle.monster.rearm(now)
        await _send(
            battle.websocket,
            ServerEvent.MONSTER_SWING,
            MonsterSwingData(
                damage=attack.final_damage,
                enraged=attack.enraged,
                your_hp=max(0, battle.hp),
                swing_index=battle.monster.swings,
                cast_ends_at=clock.to_server_ms(battle.monster.cast_ready_at),
            ),
        )

    async def _broadcast_snapshot(self, battle: LiveBattle, now: float) -> None:
        kind, damage = next_swing(
            monster=battle.monster.profile,
            elapsed_seconds=now - battle.started_at,
            defender_reduction_permille=self._standing_defence_permille(battle, now),
            defender_flat_reduction=battle.build.defence,
        )

        await _send(
            battle.websocket,
            ServerEvent.STATE_TICK,
            StateTickData(
                t=clock.to_server_ms(now),
                your_hp=max(0, battle.hp),
                your_max_hp=battle.build.max_hp,
                your_mana=battle.mana,
                combo=battle.combo,
                monster_hp=max(0, battle.monster.hp),
                monster_max_hp=battle.monster.max_hp,
                cast_ends_at=clock.to_server_ms(battle.monster.cast_ready_at),
                next_swing=kind,
                next_swing_damage=damage,
                # Zero rather than a stamp when nothing is standing: the default
                # `lockout_until` is a monotonic zero, which converts to a large
                # negative instant that means nothing to a client.
                lockout_ends_at=(
                    clock.to_server_ms(battle.lockout_until)
                    if battle.lockout_until > 0
                    else 0
                ),
                effects=[
                    ActiveEffectRead(
                        code=effect.source_code,
                        effect=effect.effect,
                        magnitude=effect.magnitude,
                        expires_at=clock.to_server_ms(effect.expires_at),
                    )
                    for effect in battle.effects.effects
                ],
            ),
        )

    # --- modifiers ----------------------------------------------------------

    def _combo_survives(self, battle: LiveBattle, now: float) -> bool:
        """Whether a miss is forgiven by a standing COMBO_KEEP."""
        return battle.effects.consume(SkillEffect.COMBO_KEEP, now) is not None

    def _attack_permille(self, battle: LiveBattle, now: float) -> int:
        """The player's damage scaling: class and gear, then whatever they cast.

        Skills move this number; they never add a branch to `resolve_blow`.
        """
        permille = battle.build.damage_permille
        boost = battle.effects.consume(SkillEffect.DOUBLE_DAMAGE, now)
        if boost is not None:
            permille = permille * boost.magnitude // 100
        execute = battle.effects.consume(SkillEffect.EXECUTE, now)
        # An execute only pays off against a monster already low enough, which
        # is checked now rather than when the skill was cast.
        if execute is not None and 0 < battle.monster.hp <= execute.magnitude:
            permille *= 2
        return permille

    def _defence_permille(self, battle: LiveBattle, now: float) -> int:
        """The shield that soaks the monster's blow, spent as it is used."""
        shield = battle.effects.consume(SkillEffect.DAMAGE_REDUCTION, now)
        return shield.magnitude if shield is not None else 0

    def _standing_defence_permille(self, battle: LiveBattle, now: float) -> int:
        """The same shield, read without spending it -- for the snapshot only."""
        shield = battle.effects.active(SkillEffect.DAMAGE_REDUCTION, now)
        return shield.magnitude if shield is not None else 0

    # --- ending -------------------------------------------------------------

    async def _finish(
        self, battle: LiveBattle, status: BattleStatus, end_reason: BattleEndReason
    ) -> None:
        """Settle the battle once, then tell the player what it was worth.

        Guarded by the status check so a race between the loop ending, a
        `battle.leave` and a dropped socket can only ever run this once.
        """
        if battle.status is not BattleStatus.IN_PROGRESS:
            return
        battle.status = status
        self._cancel_task(battle)
        await self._drain_grading(battle)

        rewards = BattleRewards.empty()
        if battle.persisted:
            # Shielded: the socket that is being torn down is often what runs
            # this call, and being cancelled midway would leave the payout half
            # applied.
            rewards = await asyncio.shield(
                self.persistence.save_result(
                    battle, BattleResult(status=status, end_reason=end_reason)
                )
            )
        # Winning the gate is what finishes the lesson on the path. Shielded for
        # the same reason the payout is: the socket tearing down is often what
        # runs this, and a half-applied completion is a lesson the player cleared
        # and cannot leave.
        if battle.persisted and status is BattleStatus.WON:
            await asyncio.shield(
                self.persistence.complete_lesson(battle.user_id, battle.lesson_id)
            )
        progress = await self.persistence.lesson_progress(battle.user_id, battle.lesson_id)

        await _send(
            battle.websocket,
            ServerEvent.BATTLE_FINISHED,
            BattleFinishedData(
                battle_id=battle.battle_id,
                outcome=status,
                end_reason=end_reason,
                your_hp_left=max(0, battle.hp),
                monster_hp_left=max(0, battle.monster.hp),
                answers_given=battle.answers_given,
                correct_count=battle.correct_count,
                best_combo=battle.best_combo,
                duration_ms=battle.duration_ms(clock.seconds()),
                monster_swings=battle.monster.swings,
                first_clear=rewards.first_clear,
                exp=_exp_change(rewards.exp),
                gold=_gold_change(rewards.gold),
                lesson_progress=progress,
                loot=_loot_read(rewards.loot),
                streak=_streak_read(rewards.streak),
            ),
        )
        await self._teardown(battle)

    async def _drain_grading(self, battle: LiveBattle) -> None:
        """Let the study path finish writing before progress is read back."""
        pending = list(battle.grading)
        if not pending:
            return
        with contextlib.suppress(Exception):
            await asyncio.shield(asyncio.gather(*pending, return_exceptions=True))

    async def _abort(self, battle: LiveBattle) -> None:
        """A crashed battle is closed without paying, and without charging."""
        if battle.status is not BattleStatus.IN_PROGRESS:
            await self._teardown(battle)
            return
        battle.status = BattleStatus.ABANDONED
        if battle.persisted:
            await self.persistence.save_result(
                battle,
                BattleResult(status=BattleStatus.ABANDONED, end_reason=BattleEndReason.CANCELLED),
            )
        await self._teardown(battle)

    async def close_stale_battle(self, battle: LiveBattle) -> None:
        """Drop a battle whose player is long gone (housekeeping)."""
        await self._finish(battle, BattleStatus.ABANDONED, BattleEndReason.CANCELLED)

    async def shutdown(self) -> None:
        """Cancel every running battle loop so the process can exit promptly."""
        for battle in self.registry.all_battles():
            if battle.task is not None:
                battle.task.cancel()
                battle.task = None

    async def _teardown(self, battle: LiveBattle) -> None:
        async with self.registry.lock:
            self.registry.unregister(battle.battle_id)

    def _cancel_task(self, battle: LiveBattle) -> None:
        task = battle.task
        # Never cancel the task we are currently running inside.
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            battle.task = None


# --- module helpers --------------------------------------------------------


def _answer_fits(question: ChallengePublicRead, option_ids: list[str]) -> bool:
    """Whether this submission is even a possible answer to this question.

    An ORDER question takes every one of its tiles, each used once: a partial
    sentence is not the sentence, and a tile placed twice is not something the
    word-tile surface can produce. Every other type takes exactly one option.
    """
    available = {option.id for option in question.options}
    if not option_ids or not available.issuperset(option_ids):
        return False
    if question.type is ChallengeType.ORDER:
        return len(option_ids) == len(available) == len(set(option_ids))
    return len(option_ids) == 1


def _is_correct(
    question: ChallengePublicRead, answer_key: list[str], option_ids: list[str]
) -> bool:
    """Grade one answer against the key drawn when the battle started.

    For an ORDER question the key is the solution in order, and the comparison
    is on the *words* rather than the ids: a sentence can repeat a word, and
    two tiles carrying the same one are interchangeable -- swapping them still
    spells the right sentence. That is the rule the study path grades by, and
    the two must never disagree about the same answer.
    """
    if question.type is not ChallengeType.ORDER:
        return option_ids[0] in answer_key
    text_of = {option.id: option.text for option in question.options}
    return [text_of.get(o) for o in option_ids] == [text_of.get(o) for o in answer_key]


def _cooldown_seconds(skill: EquippedSkill) -> float:
    """How long before this skill may be cast again.

    Its own duration, so an effect can never be stacked on top of itself, and
    never less than the floor -- a zero-duration skill would otherwise be
    limited by mana alone.
    """
    return max(SKILL_MIN_COOLDOWN_SECONDS, skill.duration_rounds * SECONDS_PER_ROUND)


def _monster_read(battle: LiveBattle) -> MonsterRead:
    profile = battle.monster.profile
    return MonsterRead(
        code=profile.code,
        name=profile.name,
        tier=profile.tier,
        max_hp=profile.max_hp,
        attack_damage=profile.attack_damage,
        is_boss=profile.is_boss,
        art_code=profile.art_code,
        cast_interval_ms=int(cast_interval_seconds(profile) * 1000),
    )


def _blow_read(blow: Blow | None) -> BlowRead | None:
    if blow is None:
        return None
    return BlowRead(
        final_damage=blow.final_damage,
        strike=blow.strike,
        combo_count=blow.combo_count,
        combo_multiplier=blow.combo_multiplier,
        is_critical=blow.is_critical,
        element_multiplier=blow.element_multiplier,
    )


def _exp_change(award: ExpAward | None) -> ExpChange:
    """A battle that paid nothing -- a loss, or one already settled -- reports
    no change rather than a missing field the client has to guard."""
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


def _loot_read(drop: LootDrop | None) -> LootDropRead | None:
    if drop is None:
        return None
    return LootDropRead(code=drop.code, name=drop.name, rarity=drop.rarity)


def _streak_read(change: StreakChange | None) -> StreakChangeRead | None:
    if change is None:
        return None
    return StreakChangeRead(
        day_streak=change.day_streak,
        best_day_streak=change.best_day_streak,
        extended=change.extended,
    )


async def _send(websocket: WebSocket | None, event: ServerEvent, data: BaseModel | None) -> None:
    """Best-effort delivery. A dead socket is handled by the disconnect path."""
    if websocket is None:
        return
    with contextlib.suppress(Exception):
        await websocket.send_json(envelope(event, data))


async def _send_error(websocket: WebSocket | None, code: ErrorCode, message: str) -> None:
    await _send(websocket, ServerEvent.ERROR, ErrorData(code=code, message=message))


engine = BattleEngine()
