from datetime import datetime

from pydantic import BaseModel, Field

from app.models.game.benchmark_exam_attempt import BenchmarkAttemptStatus
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.game.game import GameProfileRead


class BenchmarkAttemptStartRequest(BaseModel):
    """Sit the Benchmark Exam bound to one chốt chặn năng lực.

    `cap_level` must be one of `cefr.LEVEL_CAPS`, one the player's raw level
    has already reached, and one not yet cleared -- the service checks all
    three, this is only the shape of the request.
    """

    cap_level: int = Field(gt=0)


class BenchmarkAttemptRead(BaseModel):
    """A freshly drawn paper. Carries the questions and never their answers."""

    attempt_id: str
    cap_level: int
    questions: list[ChallengePublicRead]
    total: int
    pass_percent: int
    started_at: datetime
    expires_at: datetime


class BenchmarkAnswerRequest(BaseModel):
    """One answer, in whichever of the two shapes the challenge takes -- the
    same contract as `AnswerCheckRequest`."""

    challenge_id: str = Field(min_length=1, max_length=36)
    selected_option_id: str | None = None
    selected_option_ids: list[str] | None = None


class BenchmarkAnswerAck(BaseModel):
    """That an answer was taken. Deliberately says nothing about whether it was
    right: an exam gives no feedback until the paper is handed in."""

    attempt_id: str
    answered_count: int
    total: int


class BenchmarkResultRead(BaseModel):
    attempt_id: str
    cap_level: int
    status: BenchmarkAttemptStatus
    correct_count: int
    total: int
    percent: int
    pass_percent: int
    passed: bool
    submitted_at: datetime | None
    # The profile after grading, so a pass shows up as a lifted level at once.
    profile: GameProfileRead


class BenchmarkAttemptSummary(BaseModel):
    attempt_id: str
    cap_level: int
    status: BenchmarkAttemptStatus
    correct_count: int | None
    total: int
    percent: int | None
    started_at: datetime
    submitted_at: datetime | None
