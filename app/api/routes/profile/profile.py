from fastapi import APIRouter

from app.api.dependencies import CurrentUser, ProfileServiceDependency
from app.api.routes.profile._profile_errors import raise_profile_http_error
from app.core.exceptions import ApplicationError
from app.schemas.profile.profile import PublicProfileRead, SelfProfileRead

router = APIRouter()


# Declared before `/{user_id}`, and it has to stay that way: routes match in
# declaration order, so the other way round "me" would be read as a user id and
# this endpoint would never be reached.
@router.get("/me", response_model=SelfProfileRead)
async def read_my_profile(
    current_user: CurrentUser, service: ProfileServiceDependency
) -> SelfProfileRead:
    return await service.get_self(current_user.id)


@router.get("/{user_id}", response_model=PublicProfileRead)
async def read_public_profile(
    user_id: str, current_user: CurrentUser, service: ProfileServiceDependency
) -> PublicProfileRead:
    """Another player's card, as opened from a leaderboard row or a lobby.

    Authenticated, but not restricted: any signed-in player may read any other
    player's public half. `current_user` is required so the endpoint cannot be
    scraped anonymously.
    """
    try:
        return await service.get_public(user_id)
    except ApplicationError as exc:
        raise_profile_http_error(exc)
