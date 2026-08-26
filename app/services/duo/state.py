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
from app.schemas.duo.duo import DuoPlayerRead, DuoSettingsRead


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
    rating: int
    websocket: WebSocket | None = None
    connected: bool = True
    score: int = 0
    correct_count: int = 0
    total_elapsed_ms: int = 0
    grace_task: asyncio.Task[None] | None = None

    def to_read(self) -> DuoPlayerRead:
        return DuoPlayerRead(
            id=self.user_id,
            username=self.username,
            avatar_url=self.avatar_url,
            rating=self.rating,
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
    rating: int
    settings: MatchSettings
    websocket: WebSocket
    username: str | None = None
    avatar_url: str | None = None
    joined_at: float = field(default_factory=time.monotonic)

    def waited_seconds(self) -> int:
        return int(time.monotonic() - self.joined_at)
