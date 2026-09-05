"""In-memory state of lesson battles that are currently being played.

Only the started row and the finished result reach PostgreSQL; everything here
lives in the process and is cheap to mutate. `registry` holds the lock that
guards the collections these objects live in.

A battle has one player, so there is no opponent bookkeeping at all: what duo
spends on symmetry, this spends on the monster.

Everything here is timed in monotonic seconds from `clock.seconds()`. There are
no rounds: a question is pushed, answered or abandoned, and the next one follows
immediately, while the monster's cast bar runs on a clock of its own that no
answer can pause.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import WebSocket

from app.models.pve.lesson_battle import BattleStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.services.game.combat import MAX_HP
from app.services.game.loadout import (
    ActiveEffect,
    EffectState,
    PlayerLoadout,
    SkillUseRecord,
    default_loadout,
)
from app.services.pve.monster import MonsterProfile, cast_interval_seconds


@dataclass
class MonsterState:
    """The monster as it stands right now: a frozen profile, its health, and
    the instant its next blow lands.

    `cast_ready_at` is the whole difference between this engine and the one it
    replaced. It runs whether or not a question is on screen, which is what
    makes the monster an opponent rather than a deadline.
    """

    profile: MonsterProfile
    hp: int
    cast_ready_at: float = 0.0
    swings: int = 0

    @classmethod
    def of(cls, profile: MonsterProfile, *, now: float) -> "MonsterState":
        return cls(
            profile=profile,
            hp=profile.max_hp,
            # The player gets one full interval before the first blow, so a
            # fight never opens with damage already in flight.
            cast_ready_at=now + cast_interval_seconds(profile),
        )

    @property
    def max_hp(self) -> int:
        return self.profile.max_hp

    @property
    def is_down(self) -> bool:
        return self.hp <= 0

    def rearm(self, now: float, *, delay: float = 0.0) -> None:
        """Start the next cast. `delay` pushes it further out, which is what a
        clock-stealing skill spends its mana on."""
        self.cast_ready_at = now + cast_interval_seconds(self.profile) + delay


@dataclass
class SubmittedAnswer:
    """One answer, as it was resolved.

    Unlike the lock-step engine, `is_correct` is decided here and now, from the
    answer key loaded when the battle started -- a tick cannot wait on a query.
    The study path is still told, on a background task, and is still what moves
    lesson progress; it just no longer gates the sword.
    """

    question_id: str
    option_id: str
    elapsed_ms: int
    is_correct: bool
    correct_option_ids: list[str] = field(default_factory=list)
    explanation: str | None = None


@dataclass
class LiveBattle:
    battle_id: str
    user_id: str
    lesson_id: str
    lesson_title: str
    monster: MonsterState
    websocket: WebSocket | None = None
    questions: list[ChallengePublicRead] = field(default_factory=list)
    # Now the engine's own grading table, not only a reveal aid. See
    # `SubmittedAnswer`.
    answer_key: dict[str, list[str]] = field(default_factory=dict)
    explanations: dict[str, str | None] = field(default_factory=dict)
    status: BattleStatus = BattleStatus.IN_PROGRESS
    hp: int = MAX_HP
    mana: int = 0
    combo: int = 0
    best_combo: int = 0
    correct_count: int = 0
    answers_given: int = 0
    # A battle where the monster never landed a blow is worth a bonus, so it
    # has to be remembered even after a heal puts the health bar back up. In a
    # realtime fight this means never letting a cast finish.
    took_damage: bool = False

    # --- the question on screen --------------------------------------------
    # A token rather than an index: the pool is recycled when it runs out, so
    # the same question can legitimately come round twice and an index would no
    # longer identify which showing an answer belongs to.
    question_token: str | None = None
    question_pushed_at: float | None = None
    question_cursor: int = 0
    pool_pass: int = 0
    # While this is in the future no question is on screen: the player is
    # reading the last result. The monster's clock does not care.
    lockout_until: float = 0.0

    task: asyncio.Task[None] | None = None
    # Answers handed to the study path, still in flight. Awaited once, when
    # the battle settles, so the progress it reports is the progress it wrote.
    grading: set[asyncio.Task[None]] = field(default_factory=set)
    last_snapshot_at: float = 0.0
    loadout: PlayerLoadout | None = None
    effects: EffectState = field(default_factory=EffectState)
    skill_ready_at: dict[str, float] = field(default_factory=dict)
    skill_log: list[SkillUseRecord] = field(default_factory=list)
    started_at: float = 0.0
    created_at: float = field(default_factory=time.monotonic)
    started_wall: datetime | None = None
    persisted: bool = False

    @property
    def build(self) -> PlayerLoadout:
        """The loadout, or baseline stats for a player who never picked a class."""
        if self.loadout is None:
            return default_loadout(self.user_id)
        return self.loadout

    @property
    def rounds_played(self) -> int:
        """What the battles table calls a round: one answer given.

        The column predates the realtime loop and still means "how much of the
        lesson did this fight cover", which is exactly the answer count.
        """
        return self.answers_given

    @property
    def questions_in_pool(self) -> int:
        return len(self.questions)

    @property
    def is_down(self) -> bool:
        return self.hp <= 0

    @property
    def current_question(self) -> ChallengePublicRead | None:
        """The question on screen, or None while the player is between two."""
        if self.question_token is None:
            return None
        index = (self.question_cursor - 1) % len(self.questions)
        return self.questions[index]

    def add_effect(self, effect: ActiveEffect) -> None:
        self.effects.add(effect)

    def elapsed_ms(self, now: float) -> int:
        """Milliseconds since the current question was pushed."""
        if self.question_pushed_at is None:
            return 0
        return int((now - self.question_pushed_at) * 1000)

    def duration_ms(self, now: float) -> int:
        return int((now - self.started_at) * 1000)
