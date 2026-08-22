from pydantic import BaseModel, ConfigDict, Field

from app.models.challenge import ChallengeDifficulty, ChallengeType

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


# --- Challenges ----------------------------------------------------------


class ChallengeCreate(BaseModel):
    lesson_id: str
    type: ChallengeType
    question: str = Field(min_length=1, max_length=1000)
    explanation: str | None = Field(default=None, max_length=2000)
    difficulty: ChallengeDifficulty = ChallengeDifficulty.MEDIUM
    order_index: int = Field(gt=0)
    options: list[ChallengeOptionCreate] = Field(min_length=2)


class ChallengeUpdate(BaseModel):
    type: ChallengeType | None = None
    question: str | None = Field(default=None, min_length=1, max_length=1000)
    explanation: str | None = Field(default=None, max_length=2000)
    difficulty: ChallengeDifficulty | None = None
    order_index: int | None = Field(default=None, gt=0)


class ChallengeRead(BaseModel):
    """Admin-facing representation. Options include the correct answer and the
    explanation is visible (both are hidden from ChallengePublicRead)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    lesson_id: str
    type: ChallengeType
    question: str
    explanation: str | None
    difficulty: ChallengeDifficulty
    order_index: int
    options: list[ChallengeOptionRead]


class ChallengePublicRead(BaseModel):
    """Learner-facing representation. Never exposes the correct answer (but
    difficulty is safe to show — it doesn't reveal anything about the answer)."""

    model_config = ConfigDict(from_attributes=True)
    id: str
    lesson_id: str
    type: ChallengeType
    question: str
    difficulty: ChallengeDifficulty
    order_index: int
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
