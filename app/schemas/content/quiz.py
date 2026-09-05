from pydantic import BaseModel, Field

from app.models.content.challenge import ChallengeDifficulty
from app.schemas.content.course_content import ChallengePublicRead


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


class QuizSetWithAnswers(BaseModel):
    """A drawn question set plus its answer key.

    The key is kept server-side (duo matches grade answers in memory without a
    round trip to the database), so this must never be returned from a route.
    """

    questions: list[ChallengePublicRead]
    answer_key: dict[str, list[str]]
    explanations: dict[str, str | None]


class AnswerCheckRequest(BaseModel):
    """One submitted answer, in whichever of the two shapes the challenge takes.

    Single-choice challenges (SELECT/ASSIST) send `selected_option_id`. An
    ORDER challenge sends `selected_option_ids` -- every word tile, in the
    order the learner laid them down. Exactly one of the two must be present;
    `ProgressService.check_answer` rejects a shape the challenge type does not
    take rather than guessing.
    """

    selected_option_id: str | None = None
    selected_option_ids: list[str] | None = None


class AnswerCheckResult(BaseModel):
    """The graded answer.

    `correct_option_ids` is a set for single-choice challenges but a *sequence*
    for ORDER ones -- the tiles in the order that spells the right sentence, so
    a wrong answer can be shown its solution.
    """

    challenge_id: str
    selected_option_id: str | None
    selected_option_ids: list[str]
    correct: bool
    correct_option_ids: list[str]
    explanation: str | None
