from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency
from app.schemas.course_content import LessonRead

router = APIRouter()


@router.get("/{unit_id}/lessons", response_model=list[LessonRead])
async def list_lessons(unit_id: str, service: CourseContentServiceDependency) -> list[LessonRead]:
    lessons = await service.list_lessons(unit_id)
    return [LessonRead.model_validate(lesson) for lesson in lessons]
