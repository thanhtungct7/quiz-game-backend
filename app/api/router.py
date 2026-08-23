from fastapi import APIRouter

from app.api.routes import (
    admin_content,
    auth,
    challenges,
    courses,
    health,
    lessons,
    topics,
    units,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(courses.router, prefix="/courses", tags=["course-content"])
api_router.include_router(units.router, prefix="/units", tags=["course-content"])
api_router.include_router(lessons.router, prefix="/lessons", tags=["course-content"])
api_router.include_router(challenges.router, prefix="/challenges", tags=["course-content"])
api_router.include_router(topics.router, prefix="/topics", tags=["course-content"])

api_router.include_router(
    admin_content.courses_router, prefix="/admin/courses", tags=["admin-question-bank"]
)
api_router.include_router(
    admin_content.units_router, prefix="/admin/units", tags=["admin-question-bank"]
)
api_router.include_router(
    admin_content.lessons_router, prefix="/admin/lessons", tags=["admin-question-bank"]
)
api_router.include_router(
    admin_content.challenges_router, prefix="/admin/challenges", tags=["admin-question-bank"]
)
api_router.include_router(
    admin_content.challenge_options_router, prefix="/admin", tags=["admin-question-bank"]
)
api_router.include_router(
    admin_content.topics_router, prefix="/admin/topics", tags=["admin-question-bank"]
)
