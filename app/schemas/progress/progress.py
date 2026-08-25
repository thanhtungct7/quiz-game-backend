from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.progress.user_lesson_progress import LessonProgressStatus


class LessonProgressRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    lesson_id: str
    status: LessonProgressStatus
    correct_challenge_count: int
    total_challenge_count: int
    completed_at: datetime | None


class UnitProgressRead(BaseModel):
    unit_id: str
    lessons: list[LessonProgressRead]
