"""In-memory state of duo matches that are currently being played.

Only finished results reach PostgreSQL; everything here lives in the process
and is deliberately cheap to mutate. See `registry` for the lock that guards
the collections these objects are stored in.
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import WebSocket

from app.models.content.challenge import ChallengeDifficulty
from app.models.duo.duo_match import DuoMatchMode, DuoMatchStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.duo import DuoSettingsRead
from app.schemas.game.player_card import PlayerCardRead
from app.services.duo.combat import MAX_HP, Blow
from app.services.duo.loadout import (
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
    total_elapsed_ms: int = 0
    hp: int = MAX_HP
    mana: int = 0
    combo: int = 0
    best_combo: int = 0
    # The exact round index this player has to sit out, or None. An exact index
    # rather than a countdown because the stun is granted while settling round
    # N and applies to round N+1, and a countdown would be ambiguous about
    # which of the two it meant.
    stunned_round_index: int | None = None
    # This player's own deadline for the current round, which can be shorter
    # than the round's once skills can shorten it.
    effective_limit_ms: int = 0
    # Resolved once when the match starts and read-only from then on.
    loadout: PlayerLoadout | None = None
    effects: EffectState = field(default_factory=EffectState)
    skills_used_this_round: set[str] = field(default_factory=set)
    grace_task: asyncio.Task[None] | None = None

    def is_stunned_for(self, round_index: int) -> bool:
        return self.stunned_round_index == round_index

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

    def to_read(self) -> PlayerCardRead:
        return build_player_card(
            user_id=self.user_id,
            username=self.username,
            avatar_url=self.avatar_url,
            standing=self.standing,
        )


@dataclass
class SubmittedAnswer:
    option_id: str
    elapsed_ms: int
    is_correct: bool
    points: int


@dataclass
class RoundRecord:
    round_index: int
    challenge_id: str
    answers: dict[str, SubmittedAnswer]
    blows: dict[str, Blow] = field(default_factory=dict)
    hp_after: dict[str, int] = field(default_factory=dict)


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
    round_index: int = -1
    round_started_at: float | None = None
    round_answers: dict[str, SubmittedAnswer] = field(default_factory=dict)
    round_closed: asyncio.Event = field(default_factory=asyncio.Event)
    rounds_log: list[RoundRecord] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    started_wall: datetime | None = None
    persisted: bool = False
    # Set once a blow drops someone to zero. The round it happens in still
    # plays out and still reports its result; the match loop stops afterwards.
    ko_pending: bool = False
    # True once the start charged the players. Cleared when it is handed back.
    energy_spent: bool = False
    # Buffered in memory and flushed with the match, so a live round never
    # waits on a database round trip.
    skill_log: list[SkillUseRecord] = field(default_factory=list)

    @property
    def player_ids(self) -> list[str]:
        return list(self.players)

    @property
    def is_full(self) -> bool:
        return len(self.players) >= 2

    def opponent_of(self, user_id: str) -> PlayerConn | None:
        for player_id, player in self.players.items():
            if player_id != user_id:
                return player
        return None

    def connected_players(self) -> list[PlayerConn]:
        return [player for player in self.players.values() if player.connected]

    def elapsed_ms(self) -> int:
        """Milliseconds since the current round was broadcast."""
        if self.round_started_at is None:
            return 0
        return int((time.monotonic() - self.round_started_at) * 1000)


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
