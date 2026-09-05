"""WebSocket message payloads for duo matches.

Every message on the wire is an envelope: `{"type": "<name>", "data": {...}}`.
Client payloads are parsed through these models so malformed input is rejected
before it reaches the match runtime.

There are no rounds here any more. A `question.push` opens a question for one
player and an `answer.result` closes it, while `opponent.answered` tells the
other side what just landed on them and `state.tick` reports the whole fight
ten times a second. Every instant on the wire is an absolute millisecond stamp
on the server's own clock -- `deadline_at`, `lockout_ends_at`, `server_time_ms`
-- so a client drawing at 60 fps can interpolate between two snapshots instead
of asking for more of them.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode
from app.models.game.game_item import ItemRarity
from app.models.game.skill import SkillEffect
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.duo.duo import DuoSettingsRead, DuoSettingsRequest
from app.schemas.game.player_card import PlayerCardRead
from app.services.duo.scoring import MatchOutcome
from app.services.game.combat import StrikeKind
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
    QUESTION_PUSH = "question.push"
    ANSWER_RESULT = "answer.result"
    OPPONENT_ANSWERED = "opponent.answered"
    STATE_TICK = "state.tick"
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
    # Answered a question that is no longer the one on screen, or answered
    # while none is: a late frame, a replay, or a tap during the pause after
    # the previous answer. Replaces the old ROUND_CLOSED / ALREADY_ANSWERED
    # pair, neither of which means anything without rounds.
    QUESTION_CLOSED = "QUESTION_CLOSED"
    INVALID_OPTION = "INVALID_OPTION"
    NO_QUESTIONS_AVAILABLE = "NO_QUESTIONS_AVAILABLE"
    STUNNED = "STUNNED"
    SKILL_NOT_EQUIPPED = "SKILL_NOT_EQUIPPED"
    SKILL_ON_COOLDOWN = "SKILL_ON_COOLDOWN"
    NOT_ENOUGH_MANA = "NOT_ENOUGH_MANA"
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
    """The token identifies which showing of the question this answers -- a
    wrong answer sends the same question back into the deck, so an index
    could no longer tell two showings apart."""

    token: str = Field(min_length=1, max_length=36)
    option_id: str = Field(min_length=1, max_length=36)


class SkillUsePayload(BaseModel):
    skill_code: str = Field(min_length=1, max_length=32)


class ChatSendPayload(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_CHAT_LENGTH)


class PingPayload(BaseModel):
    """Echoed back untouched beside the server's own clock, so the client can
    work out its offset and half the round trip."""

    client_time_ms: int = 0


# --- Server -> client ------------------------------------------------------


class ConnectedData(BaseModel):
    user: PlayerCardRead
    active_match_id: str | None
    server_time_ms: int


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
    opponent: PlayerCardRead
    settings: DuoSettingsRead
    host_id: str
    auto_start: bool


class MatchStartedData(BaseModel):
    """Sent per player: the health and mana differ between the two."""

    match_id: str
    deck_size: int
    # The speed reference a correct answer is scored against, not a deadline.
    speed_reference_seconds: int
    tick_hz: int
    snapshot_hz: int
    server_time_ms: int
    deadline_at: int
    your_hp: int
    your_max_hp: int
    your_mana: int
    opponent_hp: int
    opponent_max_hp: int


class QuestionPushData(BaseModel):
    token: str
    question: ChallengePublicRead
    pushed_at: int
    # How many questions this player still has to get right, this one
    # included. Zero is impossible: a push means there was one to give.
    deck_remaining: int
    # True when this question is coming round again because it was answered
    # wrongly. The client says so rather than letting the repeat look like a
    # bug.
    retry: bool


class ActiveEffectRead(BaseModel):
    """One effect standing on the player, as the HUD needs to draw it."""

    code: str
    effect: SkillEffect
    magnitude: int
    expires_at: int


class BlowRead(BaseModel):
    """One player's attack, as reported to the client."""

    damage: int
    strike: StrikeKind
    combo_count: int
    combo_multiplier: float
    is_critical: bool
    stuns_opponent: bool
    element_multiplier: float


class AnswerResultData(BaseModel):
    """What one answer did, sent only to the player who gave it."""

    token: str
    correct: bool
    option_id: str
    elapsed_ms: int
    correct_option_ids: list[str]
    explanation: str | None
    points: int
    blow: BlowRead | None
    your_score: int
    your_mana: int
    your_combo: int
    your_deck_remaining: int
    opponent_hp: int
    lockout_ends_at: int


class OpponentAnsweredData(BaseModel):
    """The other side answered, and whatever it cost has already been applied.

    This is how a player sees a blow the instant it lands rather than at the
    end of a round: nothing here is a prediction.
    """

    correct: bool
    damage: int
    is_critical: bool
    your_hp: int
    opponent_score: int
    opponent_combo: int
    opponent_deck_remaining: int
    # Set when that answer's combo stunned this player, so the screen can say
    # why it went inert.
    your_stunned_until: int


class StateTickData(BaseModel):
    """The whole match, ten times a second.

    Small on purpose: everything here is a scalar the client already knows how
    to draw, and the deadlines let it animate the seconds in between on its
    own.
    """

    t: int
    your_hp: int
    your_max_hp: int
    your_mana: int
    your_combo: int
    your_score: int
    your_deck_remaining: int
    opponent_hp: int
    opponent_max_hp: int
    opponent_combo: int
    opponent_score: int
    opponent_deck_remaining: int
    deadline_at: int
    lockout_ends_at: int
    stunned_until: int
    effects: list[ActiveEffectRead] = Field(default_factory=list)


class MatchResumeData(BaseModel):
    """Everything a reconnecting client needs to rebuild its screen."""

    match_id: str
    opponent: PlayerCardRead
    settings: DuoSettingsRead
    deck_size: int
    server_time_ms: int
    deadline_at: int
    # The question this player was on, if any. Null while they are between two.
    token: str | None
    question: ChallengePublicRead | None
    pushed_at: int
    your_deck_remaining: int
    opponent_deck_remaining: int
    your_score: int
    opponent_score: int
    your_hp: int
    your_max_hp: int
    opponent_hp: int
    opponent_max_hp: int
    your_mana: int
    your_combo: int
    opponent_combo: int
    lockout_ends_at: int
    stunned_until: int
    effects: list[ActiveEffectRead] = Field(default_factory=list)


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
    deck_size: int
    your_deck_cleared: bool
    opponent_deck_cleared: bool
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

    user_id: str
    skill_code: str
    skill_name: str
    effect: SkillEffect
    magnitude: int
    mana_spent: int
    your_hp: int
    opponent_hp: int
    your_mana: int
    # When the caster may fire this skill again. Zero for the other side,
    # which has no business knowing.
    ready_again_at: int
    # This player's own lockout, which a TIME_PENALTY landing on them moves.
    lockout_ends_at: int
    private: dict[str, Any] | None = None


class ChatMessageData(BaseModel):
    user_id: str
    message: str
    sent_at: datetime


class PongData(BaseModel):
    client_time_ms: int
    server_time_ms: int


class ErrorData(BaseModel):
    code: ErrorCode
    message: str


def envelope(event: ServerEvent, data: BaseModel | None = None) -> dict[str, Any]:
    """Wrap a payload for `WebSocket.send_json`."""
    payload = data.model_dump(mode="json") if data is not None else {}
    return {"type": event.value, "data": payload}
