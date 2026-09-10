"""WebSocket message payloads for lesson battles.

Every message on the wire is an envelope: `{"type": "<name>", "data": {...}}`,
the same shape duo uses. The enums are PvE's own rather than shared: a few
values read identically to duo's, and that is the price of the two features
never being able to break each other.

Client payloads are parsed through these models before the engine sees them.

There are no rounds here. A `question.push` opens a question and an
`answer.result` closes it, while `state.tick` reports the fight ten times a
second and `monster.swing` announces a blow that no answer caused. Every
instant on the wire is an absolute millisecond stamp on the server's own clock
-- `cast_ends_at`, `lockout_ends_at`, `server_time_ms` -- so a client drawing at
60 fps can interpolate between two snapshots instead of asking for more of them.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.game.game_item import ItemRarity
from app.models.game.skill import SkillEffect
from app.models.progress.user_lesson_progress import LessonProgressStatus
from app.models.pve.lesson_battle import BattleEndReason, BattleStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.services.game.combat import StrikeKind
from app.services.pve.monster import MonsterIntentKind


class ClientEvent(StrEnum):
    BATTLE_START = "battle.start"
    ANSWER_SUBMIT = "answer.submit"
    SKILL_USE = "skill.use"
    BATTLE_LEAVE = "battle.leave"
    PING = "ping"


class ServerEvent(StrEnum):
    CONNECTED = "connected"
    BATTLE_STARTED = "battle.started"
    STATE_TICK = "state.tick"
    QUESTION_PUSH = "question.push"
    ANSWER_RESULT = "answer.result"
    MONSTER_SWING = "monster.swing"
    SKILL_USED = "skill.used"
    BATTLE_FINISHED = "battle.finished"
    PONG = "pong"
    ERROR = "error"


class ErrorCode(StrEnum):
    INVALID_PAYLOAD = "INVALID_PAYLOAD"
    UNKNOWN_EVENT = "UNKNOWN_EVENT"
    BATTLE_ALREADY_ACTIVE = "BATTLE_ALREADY_ACTIVE"
    LESSON_NOT_FOUND = "LESSON_NOT_FOUND"
    NO_QUESTIONS_AVAILABLE = "NO_QUESTIONS_AVAILABLE"
    # The catalog has never been seeded. A configuration fault rather than
    # anything the player did, and worth saying so plainly.
    MONSTER_UNAVAILABLE = "MONSTER_UNAVAILABLE"
    NOT_IN_BATTLE = "NOT_IN_BATTLE"
    # Answered a question that is no longer the one on screen, or answered
    # while none is: a late frame, a replay, or a tap during the pause after
    # the previous answer.
    QUESTION_CLOSED = "QUESTION_CLOSED"
    INVALID_OPTION = "INVALID_OPTION"
    SKILL_NOT_EQUIPPED = "SKILL_NOT_EQUIPPED"
    NOT_ENOUGH_MANA = "NOT_ENOUGH_MANA"
    SKILL_ON_COOLDOWN = "SKILL_ON_COOLDOWN"
    # Cast at something a monster does not have: no mana to burn. Refused
    # before the cost is taken.
    SKILL_NO_TARGET = "SKILL_NO_TARGET"


# --- Client -> server ------------------------------------------------------


class ClientEnvelope(BaseModel):
    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class BattleStartPayload(BaseModel):
    """No per-question time limit any more: the monster's cast bar is the only
    clock, and it belongs to the monster."""

    lesson_id: str = Field(min_length=1, max_length=36)


class AnswerSubmitPayload(BaseModel):
    """One answer, in whichever of the two shapes the question takes.

    A single-choice question is answered with `option_id`; an ORDER one with
    `option_ids`, the word tiles in the order they were laid down. Exactly one
    of the two is expected, and `answer_ids` normalises them into the sequence
    the engine grades -- a single choice being a sequence of one.
    """

    token: str = Field(min_length=1, max_length=36)
    option_id: str | None = Field(default=None, min_length=1, max_length=36)
    option_ids: list[str] | None = Field(default=None, min_length=1, max_length=32)

    @property
    def answer_ids(self) -> list[str]:
        if self.option_ids is not None:
            return self.option_ids
        return [self.option_id] if self.option_id is not None else []

    @model_validator(mode="after")
    def _exactly_one_shape(self) -> "AnswerSubmitPayload":
        if (self.option_id is None) == (self.option_ids is None):
            raise ValueError("send either option_id or option_ids, not both")
        return self


class SkillUsePayload(BaseModel):
    skill_code: str = Field(min_length=1, max_length=32)


class PingPayload(BaseModel):
    """Echoed back untouched beside the server's own clock, so the client can
    work out its offset and half the round trip."""

    client_time_ms: int = 0


# --- Server -> client ------------------------------------------------------


class MonsterRead(BaseModel):
    """The monster as the fight needs it. The catalog view carries the prose."""

    code: str
    name: str
    tier: int
    max_hp: int
    attack_damage: int
    is_boss: bool
    art_code: str
    cast_interval_ms: int


class ActiveEffectRead(BaseModel):
    """One effect standing on the player, as the HUD needs to draw it."""

    code: str
    effect: SkillEffect
    magnitude: int
    expires_at: int


class ConnectedData(BaseModel):
    user_id: str
    active_battle_id: str | None
    server_time_ms: int


class BattleStartedData(BaseModel):
    battle_id: str
    lesson_id: str
    lesson_title: str
    questions_in_pool: int
    monster: MonsterRead
    monster_hp: int
    your_hp: int
    your_max_hp: int
    your_mana: int
    tick_hz: int
    snapshot_hz: int
    server_time_ms: int


class StateTickData(BaseModel):
    """The whole fight, ten times a second.

    Small on purpose: everything here is a scalar the client already knows how
    to draw, and the two deadlines let it animate the seconds in between on its
    own.
    """

    t: int
    your_hp: int
    your_max_hp: int
    your_mana: int
    combo: int
    monster_hp: int
    monster_max_hp: int
    cast_ends_at: int
    next_swing: MonsterIntentKind
    next_swing_damage: int
    lockout_ends_at: int
    effects: list[ActiveEffectRead] = Field(default_factory=list)


class QuestionPushData(BaseModel):
    token: str
    question: ChallengePublicRead
    pushed_at: int
    # Which time round the pool this is. Above zero the player has already seen
    # every question once; their answers stop moving lesson progress, and the
    # client says so rather than letting the repeat look like a bug.
    pool_pass: int


class BlowRead(BaseModel):
    """The player's attack, as reported to the client."""

    final_damage: int
    strike: StrikeKind
    combo_count: int
    combo_multiplier: float
    is_critical: bool
    element_multiplier: float


class AnswerResultData(BaseModel):
    """What one answer did.

    `option_ids` is what the player submitted, a sequence for an ORDER question
    and a single choice for the rest; `option_id` repeats the single choice on
    its own and is null for ORDER, where no one tile is "the" answer.
    """

    token: str
    correct: bool
    option_id: str | None
    option_ids: list[str]
    elapsed_ms: int
    correct_option_ids: list[str]
    explanation: str | None
    blow: BlowRead | None
    your_mana: int
    combo: int
    monster_hp: int
    lockout_ends_at: int


class MonsterSwingData(BaseModel):
    """A blow the player did nothing to earn -- the cast simply finished."""

    damage: int
    enraged: bool
    your_hp: int
    swing_index: int
    cast_ends_at: int


class SkillUsedData(BaseModel):
    """`private` carries what only the caster may see, such as hidden options."""

    skill_code: str
    skill_name: str
    effect: SkillEffect
    magnitude: int
    mana_spent: int
    your_hp: int
    your_mana: int
    monster_hp: int
    cast_ends_at: int
    ready_again_at: int
    private: dict[str, Any] | None = None


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


class StreakChangeRead(BaseModel):
    day_streak: int
    best_day_streak: int
    extended: bool


class LessonProgressChange(BaseModel):
    """Where the lesson stands after the battle wrote its answers.

    Proof that the two layers are actually joined: the same fight that moved
    the health bars also moved the learn path.
    """

    status: LessonProgressStatus
    correct: int
    total: int


class BattleFinishedData(BaseModel):
    battle_id: str
    outcome: BattleStatus
    end_reason: BattleEndReason
    your_hp_left: int
    monster_hp_left: int
    answers_given: int
    correct_count: int
    best_combo: int
    duration_ms: int
    monster_swings: int
    first_clear: bool
    exp: ExpChange
    gold: GoldChange
    lesson_progress: LessonProgressChange
    loot: LootDropRead | None = None
    streak: StreakChangeRead | None = None


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
