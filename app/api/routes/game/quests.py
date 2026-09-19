from datetime import UTC, datetime

from fastapi import APIRouter

from app.api.dependencies import CurrentUser, DailyQuestServiceDependency
from app.api.rate_limit import limit_by_user
from app.api.routes.game._game_errors import raise_game_http_error
from app.core import rate_limit_policies as limits
from app.core.exceptions import ApplicationError
from app.schemas.game.quest import DailyQuestsRead, QuestClaimRead

router = APIRouter()

_CLAIM = [limit_by_user("quest-claim", limits.QUEST_CLAIM_PER_USER)]


@router.get("/daily", response_model=DailyQuestsRead)
async def get_daily_quests(
    current_user: CurrentUser,
    service: DailyQuestServiceDependency,
) -> DailyQuestsRead:
    """Today's four quests and three chests, drawn on the day's first look."""
    return await service.get_today(current_user.id, datetime.now(UTC))


@router.post("/daily/{quest_id}/claim", response_model=QuestClaimRead, dependencies=_CLAIM)
async def claim_daily_quest(
    quest_id: str,
    current_user: CurrentUser,
    service: DailyQuestServiceDependency,
) -> QuestClaimRead:
    """Collect a finished quest's gold and experience. 409 once collected."""
    try:
        return await service.claim_quest(current_user.id, quest_id, datetime.now(UTC))
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.post("/daily/chests/{milestone}/claim", response_model=QuestClaimRead, dependencies=_CLAIM)
async def claim_activity_chest(
    milestone: int,
    current_user: CurrentUser,
    service: DailyQuestServiceDependency,
) -> QuestClaimRead:
    """Open the chest at 30, 60 or 100 activity points. 400 before it is reached."""
    try:
        return await service.claim_chest(current_user.id, milestone, datetime.now(UTC))
    except ApplicationError as exc:
        raise_game_http_error(exc)
