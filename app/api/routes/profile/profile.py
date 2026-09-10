from fastapi import APIRouter

from app.api.dependencies import CurrentUser, ProfileServiceDependency
from app.api.routes.profile._profile_errors import raise_profile_http_error
from app.core.exceptions import ApplicationError
from app.schemas.profile.profile import (
    AchievementListRead,
    CombatBreakdownRead,
    PublicProfileRead,
    SelfProfileRead,
)

router = APIRouter()


# Declared before `/{user_id}`, and it has to stay that way: routes match in
# declaration order, so the other way round "me" would be read as a user id and
# this endpoint would never be reached.
@router.get("/me", response_model=SelfProfileRead)
async def read_my_profile(
    current_user: CurrentUser, service: ProfileServiceDependency
) -> SelfProfileRead:
    return await service.get_self(current_user.id)


@router.get("/me/combat", response_model=CombatBreakdownRead)
async def read_my_combat_breakdown(
    current_user: CurrentUser, service: ProfileServiceDependency
) -> CombatBreakdownRead:
    """Where each of the four stats on the card came from.

    Two segments, so it cannot be mistaken for a user id by the route below no
    matter what order these are declared in -- but it is kept beside `/me`
    anyway, because the next person to add a route here will be reading for the
    ordering rule rather than for the segment count.

    Self only. The totals are public and ride along on every card; the itemised
    version is a player looking at their own build.
    """
    return await service.get_combat_breakdown(current_user.id)


@router.get("/me/achievements", response_model=AchievementListRead)
async def read_my_achievements(
    current_user: CurrentUser, service: ProfileServiceDependency
) -> AchievementListRead:
    """Everything on offer, with what has been earned marked and the rest
    carrying how far off it is.

    Self only, and the one read path that syncs: opening this tab is what lets
    an account older than an achievement pick it up. A card shows the three most
    recent and never syncs.
    """
    return await service.get_achievements(current_user.id)


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
