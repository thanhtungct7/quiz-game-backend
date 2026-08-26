from pydantic import BaseModel, ConfigDict, Field

from app.models.content.challenge import ChallengeDifficulty, ChallengeType

# --- Challenge options -------------------------------------------------


class ChallengeOptionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    correct: bool = False
    order_index: int = Field(gt=0)
    image_src: str | None = None
    audio_src: str | None = None


class ChallengeOptionUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    correct: bool | None = None
    order_index: int | None = Field(default=None, gt=0)
    image_src: str | None = None
    audio_src: str | None = None


class ChallengeOptionRead(BaseModel):
    """Admin-facing representation. Includes the correct answer."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    challenge_id: str
    text: str
    correct: bool
    order_index: int
    image_src: str | None
    audio_src: str | None


class ChallengeOptionPublicRead(BaseModel):
    """Learner-facing representation. Never exposes the correct answer."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    text: str
    order_index: int
    image_src: str | None
    audio_src: str | None


# --- Passages --------------------------------------------------------------


class PassageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    content: str
    level_grade: str | None


# --- Challenges ----------------------------------------------------------


class ChallengeCreate(BaseModel):
    lesson_id: str
    type: ChallengeType
    question: str = Field(min_length=1, max_length=1000)
    explanation: str | None = Field(default=None, max_length=2000)
    difficulty: ChallengeDifficulty = ChallengeDifficulty.EASY
    topic_id: str | None = None
    order_index: int = Field(gt=0)
    options: list[ChallengeOptionCreate] = Field(min_length=2)
    correct_text: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    cefr_level: str | None = Field(default=None, max_length=20)
    toeic_band: str | None = Field(default=None, max_length=20)
    toeic_min_score: int | None = None


class ChallengeUpdate(BaseModel):
    type: ChallengeType | None = None
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    explanation: str | None = Field(default=None, max_length=2000)
    difficulty: ChallengeDifficulty | None = None
    topic_id: str | None = None
    order_index: int | None = Field(default=None, gt=0)
    correct_text: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    cefr_level: str | None = Field(default=None, max_length=20)
    toeic_band: str | None = Field(default=None, max_length=20)
    toeic_min_score: int | None = None


class ChallengeRead(BaseModel):
    """Admin-facing representation. Options include the correct answer, the
    explanation is visible, and so is the full source metadata (tags/CEFR/
    TOEIC band) -- all hidden from ChallengePublicRead."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    lesson_id: str
    type: ChallengeType
    question: str
    explanation: str | None
    difficulty: ChallengeDifficulty
    topic_id: str | None
    order_index: int
    passage: PassageRead | None
    options: list[ChallengeOptionRead]
    correct_text: str | None
    tags: list[str] | None
    cefr_level: str | None
    toeic_band: str | None
    toeic_min_score: int | None


class ChallengePublicRead(BaseModel):
    """Learner-facing representation. Never exposes the correct answer (but
    difficulty and topic are safe to show — neither reveals anything about
    the answer)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    lesson_id: str
    type: ChallengeType
    question: str
    difficulty: ChallengeDifficulty
    topic_id: str | None
    order_index: int
    passage: PassageRead | None
    options: list[ChallengeOptionPublicRead]


# --- Lessons ---------------------------------------------------------------


class LessonCreate(BaseModel):
    unit_id: str
    title: str = Field(min_length=1, max_length=100)
    order_index: int = Field(gt=0)


class LessonUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    order_index: int | None = Field(default=None, gt=0)


class LessonRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    unit_id: str
    title: str
    order_index: int
    is_bank: bool = False


# --- Units -------------------------------------------------------------


class UnitCreate(BaseModel):
    course_id: str
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    order_index: int = Field(gt=0)


class UnitUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    order_index: int | None = Field(default=None, gt=0)


class UnitRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    course_id: str
    title: str
    description: str
    order_index: int


# --- Courses -------------------------------------------------------------


class CourseCreate(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    image_src: str = Field(min_length=1, max_length=255)


class CourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    image_src: str | None = Field(default=None, min_length=1, max_length=255)


class CourseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    image_src: str


# --- Course tree -----------------------------------------------------------


class LessonTreeRead(BaseModel):
    """A path lesson as it appears on the learn screen. `challenge_count` lets
    the client show lesson length without fetching the challenges."""

    id: str
    title: str
    order_index: int
    challenge_count: int


class UnitTreeRead(BaseModel):
    id: str
    title: str
    description: str
    order_index: int
    lessons: list[LessonTreeRead]


class CourseTreeRead(BaseModel):
    """The whole learn path -- course, units and path lessons -- in one payload.

    Replaces the client's 1 + 1 + N walk over /courses, /courses/{id}/units and
    /units/{id}/lessons. Bank lessons are excluded.
    """

    id: str
    title: str
    image_src: str
    units: list[UnitTreeRead]


# --- Topics ----------------------------------------------------------------


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class TopicUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)


class TopicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str


class TopicStats(BaseModel):
    """Question count for one topic, broken down by difficulty.

    `topic_id` is None for challenges that have not been assigned a topic yet.
    """

    topic_id: str | None
    topic_name: str
    total: int
    by_difficulty: dict[str, int]
