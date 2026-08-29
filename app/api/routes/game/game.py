from fastapi import APIRouter

from app.api.dependencies import CurrentUser, GameServiceDependency
from app.api.routes.game._game_errors import raise_game_http_error
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
    SkillNodeRead,
    SkillTreeRead,
)

router = APIRouter()


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


@router.post("/class", response_model=GameProfileRead)
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


@router.post("/skills/{skill_id}/unlock", response_model=SkillNodeRead)
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


@router.put("/loadout", response_model=LoadoutRead)
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


@router.put("/equipment", response_model=InventoryRead)
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


@router.get("/season/current", response_model=SeasonRead)
async def get_current_season(
    current_user: CurrentUser,
    service: GameServiceDependency,
) -> SeasonRead:
    return await service.get_current_season(current_user.id)
