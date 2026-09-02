from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.models.content.challenge import ChallengeDifficulty
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.schemas.game.player_card import PlayerCardRead
from app.services.duo.scoring import MatchOutcome
from app.services.game.season import RankTier

MIN_QUESTION_COUNT = 3
MAX_QUESTION_COUNT = 20
DEFAULT_QUESTION_COUNT = 10
MIN_TIME_PER_QUESTION = 5
MAX_TIME_PER_QUESTION = 60
DEFAULT_TIME_PER_QUESTION = 15


class DuoSettingsRequest(BaseModel):
    """Match settings a client may ask for. Bounds are enforced here, not by the
    client, so a crafted socket cannot request a 99999-second round."""

    question_count: int = Field(
        default=DEFAULT_QUESTION_COUNT, ge=MIN_QUESTION_COUNT, le=MAX_QUESTION_COUNT
    )
    time_per_question: int = Field(
        default=DEFAULT_TIME_PER_QUESTION, ge=MIN_TIME_PER_QUESTION, le=MAX_TIME_PER_QUESTION
    )
    topic_ids: list[str] | None = None
    difficulty: ChallengeDifficulty | None = None


class DuoSettingsRead(BaseModel):
    question_count: int
    time_per_question: int
    topic_ids: list[str] | None
    difficulty: ChallengeDifficulty | None


class DuoMatchSummary(BaseModel):
    """One row of a player's match history, told from that player's side."""

    match_id: str
    mode: DuoMatchMode
    status: DuoMatchStatus
    end_reason: DuoMatchEndReason | None
    outcome: MatchOutcome | None
    opponent: PlayerCardRead | None
    my_score: int
    opponent_score: int
    my_correct: int
    opponent_correct: int
    question_count: int
    duration_seconds: int | None
    finished_at: datetime | None
    created_at: datetime


class DuoRoundRead(BaseModel):
    round_index: int
    challenge_id: str | None
    question: str | None
    my_option_id: str | None
    opponent_option_id: str | None
    my_correct: bool
    opponent_correct: bool
    my_elapsed_ms: int | None
    opponent_elapsed_ms: int | None
    my_points: int
    opponent_points: int
    my_damage: int
    opponent_damage: int
    my_hp_after: int
    opponent_hp_after: int
    my_combo: int
    opponent_combo: int


class DuoSkillUseRead(BaseModel):
    round_index: int
    skill_code: str
    mana_spent: int
    # True when this was the reader's own skill, not the opponent's.
    mine: bool


class DuoMatchDetail(DuoMatchSummary):
    rounds: list[DuoRoundRead]
    my_hp_left: int
    opponent_hp_left: int
    skill_uses: list[DuoSkillUseRead]


class DuoStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rating: int
    matches_played: int
    wins: int
    losses: int
    draws: int
    win_rate: float
    current_streak: int
    best_streak: int


class LeaderboardScope(StrEnum):
    CURRENT = "current"
    ALL_TIME = "all_time"


class DuoLeaderboardEntry(BaseModel):
    rank: int
    user_id: str
    username: str | None
    avatar_url: str | None
    rating: int
    matches_played: int
    wins: int
    tier: RankTier


class DuoLeaderboardRead(BaseModel):
    entries: list[DuoLeaderboardEntry]
    my_rank: int | None
    scope: LeaderboardScope = LeaderboardScope.ALL_TIME
    season_code: str | None = None


class DuoRoomPreview(BaseModel):
    """What a player sees before committing to join a friend room."""

    room_code: str
    host: PlayerCardRead
    settings: DuoSettingsRead
    player_count: int
