from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency, QuizServiceDependency
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.content.course_content import LessonRead
from app.schemas.content.quiz import QuizGenerateRequest, StageQuizSet

router = APIRouter()


@router.get("/{unit_id}/lessons", response_model=list[LessonRead])
async def list_lessons(unit_id: str, service: CourseContentServiceDependency) -> list[LessonRead]:
    lessons = await service.list_lessons(unit_id)
    return [LessonRead.model_validate(lesson) for lesson in lessons]


@router.post("/{unit_id}/quiz", response_model=list[StageQuizSet])
async def generate_stage_quizzes(
    unit_id: str, data: QuizGenerateRequest, service: QuizServiceDependency
) -> list[StageQuizSet]:
    try:
        return await service.generate_for_unit(
            unit_id,
            topic_ids=data.topic_ids,
            difficulties=data.difficulties,
            count=data.count,
            exclude_ids=data.exclude_ids,
            seed=data.seed,
        )
    except ApplicationError as exc:
        raise_content_http_error(exc)
