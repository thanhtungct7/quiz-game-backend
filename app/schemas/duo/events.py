"""WebSocket message payloads for duo matches.

Every message on the wire is an envelope: `{"type": "<name>", "data": {...}}`.
Client payloads are parsed through these models so malformed input is rejected
before it reaches the match runtime.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode
from app.models.game.game_item import ItemRarity
from app.models.game.skill import SkillEffect
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.duo import DuoPlayerRead, DuoSettingsRead, DuoSettingsRequest
from app.services.duo.combat import StrikeKind
from app.services.duo.scoring import MatchOutcome
from app.services.game.season import RankTier

MAX_CHAT_LENGTH = 200


class ClientEvent(StrEnum):
    QUEUE_JOIN = "queue.join"
    QUEUE_LEAVE = "queue.leave"
    ROOM_CREATE = "room.create"
    ROOM_JOIN = "room.join"
    MATCH_START = "match.start"
    ANSWER_SUBMIT = "answer.submit"
    MATCH_LEAVE = "match.leave"
    CHAT_SEND = "chat.send"
    SKILL_USE = "skill.use"
    PING = "ping"


class ServerEvent(StrEnum):
    CONNECTED = "connected"
    QUEUE_WAITING = "queue.waiting"
    QUEUE_LEFT = "queue.left"
    QUEUE_TIMEOUT = "queue.timeout"
    ROOM_CREATED = "room.created"
    MATCH_FOUND = "match.found"
    MATCH_STARTED = "match.started"
    ROUND_START = "round.start"
    ROUND_OPPONENT_ANSWERED = "round.opponent_answered"
    ROUND_RESULT = "round.result"
    MATCH_RESUME = "match.resume"
    OPPONENT_DISCONNECTED = "opponent.disconnected"
    OPPONENT_RECONNECTED = "opponent.reconnected"
    MATCH_FINISHED = "match.finished"
    CHAT_MESSAGE = "chat.message"
    SKILL_USED = "skill.used"
    PONG = "pong"
    ERROR = "error"


class ErrorCode(StrEnum):
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    ALREADY_IN_MATCH = "ALREADY_IN_MATCH"
    ALREADY_IN_QUEUE = "ALREADY_IN_QUEUE"
    NOT_IN_QUEUE = "NOT_IN_QUEUE"
    ROOM_NOT_FOUND = "ROOM_NOT_FOUND"
    ROOM_FULL = "ROOM_FULL"
    NOT_HOST = "NOT_HOST"
    NOT_ENOUGH_PLAYERS = "NOT_ENOUGH_PLAYERS"
    MATCH_ALREADY_STARTED = "MATCH_ALREADY_STARTED"
    NOT_IN_MATCH = "NOT_IN_MATCH"
    ROUND_CLOSED = "ROUND_CLOSED"
    ALREADY_ANSWERED = "ALREADY_ANSWERED"
    INVALID_OPTION = "INVALID_OPTION"
    NO_QUESTIONS_AVAILABLE = "NO_QUESTIONS_AVAILABLE"
    STUNNED = "STUNNED"
    SKILL_NOT_EQUIPPED = "SKILL_NOT_EQUIPPED"
    SKILL_ALREADY_USED_THIS_ROUND = "SKILL_ALREADY_USED_THIS_ROUND"
    NOT_ENOUGH_MANA = "NOT_ENOUGH_MANA"
    ROUND_NOT_OPEN = "ROUND_NOT_OPEN"
    NOT_ENOUGH_ENERGY = "NOT_ENOUGH_ENERGY"


# --- Client -> server ------------------------------------------------------


class ClientEnvelope(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class QueueJoinPayload(DuoSettingsRequest):
    pass


class RoomCreatePayload(DuoSettingsRequest):
    pass


class RoomJoinPayload(BaseModel):
    room_code: str = Field(min_length=4, max_length=8)


class AnswerSubmitPayload(BaseModel):
    round_index: int = Field(ge=0)
    option_id: str = Field(min_length=1, max_length=36)


class SkillUsePayload(BaseModel):
    skill_code: str = Field(min_length=1, max_length=32)
    round_index: int = Field(ge=0)


class ChatSendPayload(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CHAT_LENGTH)


# --- Server -> client ------------------------------------------------------


class ConnectedData(BaseModel):
    user: DuoPlayerRead
    active_match_id: str | None


class QueueWaitingData(BaseModel):
    position: int
    waited_seconds: int


class RoomCreatedData(BaseModel):
    match_id: str
    room_code: str
    settings: DuoSettingsRead


class MatchFoundData(BaseModel):
    match_id: str
    room_code: str | None
    mode: DuoMatchMode
    opponent: DuoPlayerRead
    settings: DuoSettingsRead
    host_id: str
    auto_start: bool


class MatchStartedData(BaseModel):
    match_id: str
    total_rounds: int


class RoundStartData(BaseModel):
    """Sent per player: the combat fields differ between the two."""

    round_index: int
    total_rounds: int
    question: ChallengePublicRead
    time_limit_seconds: int
    deadline_at: datetime
    your_hp: int
    opponent_hp: int
    your_mana: int
    your_combo: int
    # This player's own deadline, which can be shorter than the round's.
    your_time_limit_seconds: int
    you_are_stunned: bool


class OpponentAnsweredData(BaseModel):
    round_index: int


class AnswerOutcome(BaseModel):
    option_id: str | None
    correct: bool
    elapsed_ms: int | None
    points: int


class BlowRead(BaseModel):
    """One player's attack in one round, as reported to the client."""

    damage: int
    strike: StrikeKind
    combo_count: int
    combo_multiplier: float
    is_critical: bool
    stuns_opponent: bool
    element_multiplier: float


class RoundResultData(BaseModel):
    round_index: int
    correct_option_ids: list[str]
    explanation: str | None
    you: AnswerOutcome
    opponent: AnswerOutcome
    your_score: int
    opponent_score: int
    your_blow: BlowRead
    opponent_blow: BlowRead
    your_hp: int
    opponent_hp: int
    your_mana: int
    your_combo: int


class MatchResumeData(BaseModel):
    """Everything a reconnecting client needs to rebuild its screen."""

    match_id: str
    opponent: DuoPlayerRead
    settings: DuoSettingsRead
    round_index: int
    total_rounds: int
    your_score: int
    opponent_score: int
    question: ChallengePublicRead | None
    seconds_remaining: int | None
    already_answered: bool
    your_hp: int
    opponent_hp: int
    your_mana: int
    your_combo: int
    you_are_stunned: bool
    your_max_hp: int
    # Codes of the skills still standing on you, so a reconnect redraws the
    # buff row instead of silently dropping it.
    active_effects: list[str]


class OpponentDisconnectedData(BaseModel):
    grace_seconds: int


class RatingChange(BaseModel):
    before: int
    after: int
    delta: int


class ExpChange(BaseModel):
    before: int
    after: int
    delta: int
    level_before: int
    level_after: int
    leveled_up: bool


class GoldChange(BaseModel):
    before: int
    after: int
    delta: int


class LootDropRead(BaseModel):
    code: str
    name: str
    rarity: ItemRarity


class SeasonChangeRead(BaseModel):
    season_code: str
    rating_before: int
    rating_after: int
    tier_before: RankTier
    tier_after: RankTier
    promoted: bool


class StreakChangeRead(BaseModel):
    day_streak: int
    best_day_streak: int
    extended: bool


class MatchFinishedData(BaseModel):
    match_id: str
    result: MatchOutcome
    end_reason: DuoMatchEndReason
    your_score: int
    opponent_score: int
    your_correct: int
    opponent_correct: int
    total_rounds: int
    duration_seconds: int
    rating: RatingChange
    exp: ExpChange
    gold: GoldChange
    your_hp_left: int
    opponent_hp_left: int
    loot: LootDropRead | None = None
    season: SeasonChangeRead | None = None
    streak: StreakChangeRead | None = None
    energy_left: int | None = None


class SkillUsedData(BaseModel):
    """Sent to both players. `private` is filled in only for the caster."""

    round_index: int
    user_id: str
    skill_code: str
    skill_name: str
    effect: SkillEffect
    magnitude: int
    mana_spent: int
    your_hp: int
    opponent_hp: int
    your_mana: int
    private: dict[str, Any] | None = None


class ChatMessageData(BaseModel):
    user_id: str
    message: str
    sent_at: datetime


class ErrorData(BaseModel):
    code: ErrorCode
    message: str


def envelope(event: ServerEvent, data: BaseModel | None = None) -> dict[str, Any]:
    """Wrap a payload for `WebSocket.send_json`."""
    payload = data.model_dump(mode="json") if data is not None else {}
    return {"type": event.value, "data": payload}
