from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency
from app.schemas.course_content import TopicRead

router = APIRouter()


@router.get("", response_model=list[TopicRead])
async def list_topics(service: CourseContentServiceDependency) -> list[TopicRead]:
    topics = await service.list_topics()
    return [TopicRead.model_validate(topic) for topic in topics]
