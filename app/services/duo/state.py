"""In-memory state of duo matches that are currently being played.

Only finished results reach PostgreSQL; everything here lives in the process
and is deliberately cheap to mutate. See `registry` for the lock that guards
the collections these objects are stored in.

There are no rounds. Each player holds a deck of question indices and works
through it at their own pace: a question is pushed, answered, and the next one
follows after a short lockout, while the opponent is doing the same thing on a
clock of their own. Everything here is timed in monotonic seconds from
`clock.seconds()`.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import WebSocket

from app.models.content.challenge import ChallengeDifficulty
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.duo import DuoSettingsRead
from app.schemas.game.player_card import PlayerCardRead
from app.services.game.combat import MAX_HP
from app.services.game.loadout import (
    ActiveEffect,
    EffectState,
    PlayerLoadout,
    SkillUseRecord,
    default_loadout,
)
from app.services.game.player_card import PlayerStanding, build_player_card


@dataclass(frozen=True)
class MatchSettings:
    """Frozen so queue entries can be compared for compatibility by value."""

    question_count: int
    # No longer a deadline. Nothing cuts a player off mid-question any more:
    # this is the speed reference a correct answer is scored against, and it
    # is what `award_points` and `resolve_blow` are handed.
    time_per_question: int
    topic_ids: tuple[str, ...] = ()
    difficulty: ChallengeDifficulty | None = None

    def to_read(self) -> DuoSettingsRead:
        return DuoSettingsRead(
            question_count=self.question_count,
            time_per_question=self.time_per_question,
            topic_ids=list(self.topic_ids) or None,
            difficulty=self.difficulty,
        )

    @property
    def topic_id_list(self) -> list[str] | None:
        return list(self.topic_ids) or None

    @property
    def difficulty_list(self) -> list[ChallengeDifficulty] | None:
        return [self.difficulty] if self.difficulty is not None else None


@dataclass
class PlayerConn:
    user_id: str
    username: str | None
    avatar_url: str | None
    # Rating, level, class and streak together: read once when the socket
    # joins and shown as this player's card for the rest of the match. Not
    # refreshed mid-match on purpose -- the card an opponent saw at the coin
    # toss should not change under them while they play.
    standing: PlayerStanding
    websocket: WebSocket | None = None
    connected: bool = True
    score: int = 0
    correct_count: int = 0
    answers_given: int = 0
    total_elapsed_ms: int = 0
    hp: int = MAX_HP
    mana: int = 0
    combo: int = 0
    best_combo: int = 0

    # --- this player's own deck --------------------------------------------
    # Indices into `LiveMatch.questions`, seeded in order. A question leaves
    # the deck only when it is answered correctly; a wrong answer sends it to
    # the back, which is what makes the deck something to be worked through
    # rather than a list to be survived.
    deck: deque[int] = field(default_factory=deque)
    # A token rather than an index: a wrong answer puts the same question back
    # in the deck, so the same index can legitimately come round twice and an
    # index would no longer identify which showing an answer belongs to.
    question_token: str | None = None
    question_pushed_at: float | None = None
    current_index: int | None = None
    # Indices this player has already been shown once. Only so a repeat can be
    # announced as one -- a question coming round again with no explanation
    # reads as a bug.
    seen: set[int] = field(default_factory=set)
    # While either of these is in the future no question is on screen: the
    # player is reading the last result, or sitting out a stun. The opponent's
    # clock does not care.
    lockout_until: float = 0.0
    stunned_until: float = 0.0
    # Latched by the match loop the moment an empty deck has nothing left to
    # push. Ends the match.
    deck_cleared: bool = False

    # Resolved once when the match starts and read-only from then on.
    loadout: PlayerLoadout | None = None
    effects: EffectState = field(default_factory=EffectState)
    # When each cast skill may be cast again, on the engine's monotonic clock.
    skill_ready_at: dict[str, float] = field(default_factory=dict)
    grace_task: asyncio.Task[None] | None = None

    @property
    def build(self) -> PlayerLoadout:
        """The loadout, or baseline stats for a player who never picked a class."""
        if self.loadout is None:
            return default_loadout(self.user_id)
        return self.loadout

    def add_effect(self, effect: ActiveEffect) -> None:
        self.effects.add(effect)

    @property
    def rating(self) -> int:
        return self.standing.rating

    @property
    def is_down(self) -> bool:
        return self.hp <= 0

    @property
    def deck_remaining(self) -> int:
        """How many questions this player still has to get right, the one on
        screen included -- which is the number worth showing an opponent."""
        return len(self.deck) + (1 if self.question_token is not None else 0)

    def is_stunned(self, now: float) -> bool:
        return now < self.stunned_until

    def ready_at(self) -> float:
        """The earliest this player may be handed another question."""
        return max(self.lockout_until, self.stunned_until)

    def elapsed_ms(self, now: float) -> int:
        """Milliseconds since the current question was pushed."""
        if self.question_pushed_at is None:
            return 0
        return int((now - self.question_pushed_at) * 1000)

    def to_read(self) -> PlayerCardRead:
        return build_player_card(
            user_id=self.user_id,
            username=self.username,
            avatar_url=self.avatar_url,
            standing=self.standing,
        )


@dataclass
class SubmittedAnswer:
    """One answer, as it was resolved.

    `is_correct` is decided by the engine from the key loaded when the match
    started -- a tick cannot wait on a query -- and the reveal fields ride
    along so the result frame needs no second lookup.
    """

    question_index: int
    question_id: str
    option_id: str
    elapsed_ms: int
    is_correct: bool
    points: int
    correct_option_ids: list[str] = field(default_factory=list)
    explanation: str | None = None


@dataclass
class LiveMatch:
    match_id: str
    mode: DuoMatchMode
    host_id: str
    settings: MatchSettings
    room_code: str | None = None
    # Insertion order fixes who is player_one / player_two in the database.
    players: dict[str, PlayerConn] = field(default_factory=dict)
    questions: list[ChallengePublicRead] = field(default_factory=list)
    answer_key: dict[str, list[str]] = field(default_factory=dict)
    explanations: dict[str, str | None] = field(default_factory=dict)
    status: DuoMatchStatus = DuoMatchStatus.WAITING
    task: asyncio.Task[None] | None = None
    created_at: float = field(default_factory=time.monotonic)
    # Monotonic instants, set when the first question goes out.
    started_at: float = 0.0
    deadline_at: float = 0.0
    last_snapshot_at: float = 0.0
    finished_at: float | None = None
    started_wall: datetime | None = None
    persisted: bool = False
    # Set by the tick that noticed the match is over, so `_finish` reports the
    # same reason the loop stopped for.
    end_reason: DuoMatchEndReason | None = None
    # True once the start charged the players. Cleared when it is handed back.
    energy_spent: bool = False
    # Buffered in memory and flushed with the match, so a live answer never
    # waits on a database round trip.
    skill_log: list[SkillUseRecord] = field(default_factory=list)

    @property
    def player_ids(self) -> list[str]:
        return list(self.players)

    @property
    def is_full(self) -> bool:
        return len(self.players) >= 2

    @property
    def deck_size(self) -> int:
        return len(self.questions)

    def opponent_of(self, user_id: str) -> PlayerConn | None:
        for player_id, player in self.players.items():
            if player_id != user_id:
                return player
        return None

    def connected_players(self) -> list[PlayerConn]:
        return [player for player in self.players.values() if player.connected]

    def duration_seconds(self, now: float) -> int:
        if self.started_at <= 0:
            return 0
        return int(now - self.started_at)


@dataclass
class QueueEntry:
    user_id: str
    standing: PlayerStanding
    settings: MatchSettings
    websocket: WebSocket
    username: str | None = None
    avatar_url: str | None = None
    joined_at: float = field(default_factory=time.monotonic)

    @property
    def rating(self) -> int:
        """Matchmaking bands are drawn on rating alone; the rest of the
        standing only rides along so the card is ready when a match forms."""
        return self.standing.rating

    def waited_seconds(self) -> int:
        return int(time.monotonic() - self.joined_at)
