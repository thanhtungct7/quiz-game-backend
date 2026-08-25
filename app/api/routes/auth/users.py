from fastapi import APIRouter

from app.api.dependencies import CurrentUser
from app.schemas.auth.user import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> CurrentUser:
    return current_user
