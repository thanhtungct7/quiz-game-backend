"""Granting the neutral starter skills.

Shared by the two paths that can be a player's first contact with the game
layer, because either of them may happen first and the promise has to hold
whichever one does:

* a `/game` call, through `GameService` -- the client opening the skill screen;
* the start of a match, through `LoadoutBuilder` -- the client just queueing.

The promise itself is `README.md`: two neutral starters come with the first
profile "so nobody ever walks into a match with an empty bar". Leaving this in
`GameService` alone meant it held only for players who happened to open a game
screen before their first match.
"""

from app.models.game.skill import SkillUnlockKind
from app.models.game.user_skill import LOADOUT_SLOTS
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.user_skill_repository import UserSkillRepository


async def ensure_starters(
    catalog: CatalogRepository,
    skills: UserSkillRepository,
    user_id: str,
) -> bool:
    """Grant the starters this player is missing, and equip them if the bar is
    empty. Returns whether anything was granted.

    Idempotent, which is what makes it safe to call from a hot path: a player
    who already owns the starters costs one SELECT and nothing else.

    Equipping is tied to the grant rather than merely to an empty bar. Clearing
    the bar is something `PUT /game/loadout` explicitly allows, and re-filling
    it on every read would make that choice impossible to keep.
    """
    starters = [
        skill
        for skill in await catalog.list_skills()
        if skill.unlock_kind is SkillUnlockKind.STARTER
    ]
    if not starters:
        return False

    owned = await skills.owned_skill_ids(user_id)
    missing = [skill.id for skill in starters if skill.id not in owned]
    if not missing:
        return False

    await skills.grant_many(user_id, missing)
    if not await skills.equipped_slot_by_skill_id(user_id):
        await skills.replace_loadout(
            user_id, [skill.id for skill in starters[:LOADOUT_SLOTS]]
        )
    return True
