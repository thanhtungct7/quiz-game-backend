from pydantic import BaseModel, Field

from app.models.challenge import ChallengeDifficulty
from app.schemas.course_content import ChallengePublicRead


class QuizGenerateRequest(BaseModel):
    topic_ids: list[str] | None = None
    difficulties: list[ChallengeDifficulty] | None = None
    count: int = Field(default=10, gt=0, le=100)
    exclude_ids: list[str] | None = None
    seed: int | None = None


class QuizSet(BaseModel):
    lesson_id: str
    requested_count: int
    returned_count: int
    questions: list[ChallengePublicRead]


class StageQuizSet(QuizSet):
    """One QuizSet per Stage (Lesson) within a Unit."""

    lesson_title: str
