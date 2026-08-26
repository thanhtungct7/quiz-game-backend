from fastapi import APIRouter, Request, Response, status

from app.api.dependencies import CourseContentServiceDependency
from app.api.routes.content._content_errors import raise_content_http_error
from app.api.routes.content._etag import conditional_json_response
from app.core.exceptions import ApplicationError
from app.schemas.content.course_content import CourseRead, CourseTreeRead, UnitRead

router = APIRouter()


@router.get("", response_model=list[CourseRead])
async def list_courses(service: CourseContentServiceDependency) -> list[CourseRead]:
    courses = await service.list_courses()
    return [CourseRead.model_validate(course) for course in courses]


@router.get("/{course_id}", response_model=CourseRead)
async def get_course(course_id: str, service: CourseContentServiceDependency) -> CourseRead:
    try:
        course = await service.get_course(course_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return CourseRead.model_validate(course)


@router.get(
    "/{course_id}/tree",
    response_model=CourseTreeRead,
    responses={status.HTTP_304_NOT_MODIFIED: {"description": "Course content unchanged"}},
)
async def get_course_tree(
    course_id: str, request: Request, service: CourseContentServiceDependency
) -> Response:
    """The whole learn path in one request, with an ETag so a client that
    already has it revalidates for free instead of re-downloading it."""
    try:
        tree = await service.get_course_tree(course_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return conditional_json_response(request, tree)


@router.get("/{course_id}/units", response_model=list[UnitRead])
async def list_units(course_id: str, service: CourseContentServiceDependency) -> list[UnitRead]:
    units = await service.list_units(course_id)
    return [UnitRead.model_validate(unit) for unit in units]
