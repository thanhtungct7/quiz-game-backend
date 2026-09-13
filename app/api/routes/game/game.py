from fastapi import APIRouter

from app.api.dependencies import CurrentUser, GameServiceDependency
from app.api.rate_limit import limit_by_user
from app.api.routes.game._game_errors import raise_game_http_error
from app.core import rate_limit_policies as limits
from app.core.exceptions import ApplicationError
from app.models.game.game_item import EquipmentSlot
from app.schemas.game.game import (
    ChooseClassRequest,
    EquipmentRequest,
    GameClassRead,
    GameProfileRead,
    InventoryRead,
    LoadoutRead,
    LoadoutRequest,
    SeasonRead,
    ShopRead,
    SkillNodeRead,
    SkillTreeRead,
    WearSkinRequest,
)

router = APIRouter()

# Gold, class, skills and equipment share one allowance: none of them is worth
# doing more than a few times a minute.
_GAME_WRITE = [limit_by_user("game-write", limits.GAME_WRITE_PER_USER)]


@router.get("/profile", response_model=GameProfileRead)
async def get_my_game_profile(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> GameProfileRead:
    return await service.get_profile(current_user.id)


@router.get("/classes", response_model=list[GameClassRead])
async def list_classes(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> list[GameClassRead]:
    return await service.list_classes(current_user.id)


@router.post("/class", response_model=GameProfileRead, dependencies=_GAME_WRITE)
async def choose_class(
    payload: ChooseClassRequest,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> GameProfileRead:
    try:
        return await service.choose_class(current_user.id, payload.class_code)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/skills", response_model=SkillTreeRead)
async def get_skill_tree(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> SkillTreeRead:
    return await service.get_skill_tree(current_user.id)


@router.post("/skills/{skill_id}/unlock", response_model=SkillNodeRead, dependencies=_GAME_WRITE)
async def unlock_skill(
    skill_id: str,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> SkillNodeRead:
    try:
        return await service.unlock_skill(current_user.id, skill_id)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/loadout", response_model=LoadoutRead)
async def get_loadout(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> LoadoutRead:
    return await service.get_loadout(current_user.id)


@router.put("/loadout", response_model=LoadoutRead, dependencies=_GAME_WRITE)
async def set_loadout(
    payload: LoadoutRequest,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> LoadoutRead:
    try:
        return await service.set_loadout(current_user.id, payload.skill_ids)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/items", response_model=InventoryRead)
async def get_inventory(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> InventoryRead:
    return await service.get_inventory(current_user.id)


@router.put("/equipment", response_model=InventoryRead, dependencies=_GAME_WRITE)
async def set_equipment(
    payload: EquipmentRequest,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> InventoryRead:
    try:
        return await service.set_equipment(
            current_user.id,
            {
                EquipmentSlot.WEAPON: payload.weapon_id,
                EquipmentSlot.ARMOR: payload.armor_id,
                EquipmentSlot.TRINKET: payload.trinket_id,
            },
        )
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.put("/skin", response_model=InventoryRead, dependencies=_GAME_WRITE)
async def wear_skin(
    payload: WearSkinRequest,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> InventoryRead:
    """Put an owned skin on show. A null code takes the current one off."""
    try:
        return await service.wear_skin(current_user.id, payload.skin_code)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/shop", response_model=ShopRead)
async def get_shop(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> ShopRead:
    """The cosmetics on sale and what the player can afford them with."""
    return await service.get_shop(current_user.id)


@router.post("/shop/{item_id}/purchase", response_model=ShopRead, dependencies=_GAME_WRITE)
async def purchase_item(
    item_id: str,
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> ShopRead:
    """Buy one cosmetic. Returns the refreshed shelf, new balance included."""
    try:
        return await service.purchase_item(current_user.id, item_id)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/season/current", response_model=SeasonRead)
async def get_current_season(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> SeasonRead:
    return await service.get_current_season(current_user.id)
