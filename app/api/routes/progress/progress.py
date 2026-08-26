from fastapi import APIRouter

from app.api.dependencies import CurrentUser, ProgressServiceDependency
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.progress.progress import (
    CourseProgressRead,
    LessonProgressRead,
    UnitProgressRead,
)

router = APIRouter()


@router.get("/lessons/{lesson_id}", response_model=LessonProgressRead)
async def get_lesson_progress(
    lesson_id: str, current_user: CurrentUser, service: ProgressServiceDependency
) -> LessonProgressRead:
    try:
        return await service.get_lesson_progress(current_user.id, lesson_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)


@router.get("/units/{unit_id}", response_model=UnitProgressRead)
async def get_unit_progress(
    unit_id: str, current_user: CurrentUser, service: ProgressServiceDependency
) -> UnitProgressRead:
    try:
        return await service.get_unit_progress(current_user.id, unit_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)


@router.get("/courses/{course_id}", response_model=CourseProgressRead)
async def get_course_progress(
    course_id: str, current_user: CurrentUser, service: ProgressServiceDependency
) -> CourseProgressRead:
    """Whole-course progress in one call, so the learn screen no longer issues
    one request per unit on every launch."""
    try:
        return await service.get_course_progress(current_user.id, course_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
