"""What a player brings into a match, resolved once when the match starts.

A loadout is read-only for the whole match. The engine runs entirely on this
and on `PlayerConn`, and never touches the database between rounds: a live
round must not wait on a query, and re-reading equipment mid-match would let a
player swap to a stronger build in a second tab and reconnect buffed.
"""

from dataclasses import dataclass, field

from app.models.game.skill import SkillEffect
from app.services.game.combat import MAX_HP, PERMILLE_ONE


@dataclass(frozen=True)
class EquippedSkill:
    """One of the three skills a player can fire during a match."""

    skill_id: str
    code: str
    name: str
    slot: int
    effect: SkillEffect
    mana_cost: int
    magnitude: int
    duration_rounds: int


@dataclass(frozen=True)
class PlayerLoadout:
    user_id: str
    level: int
    class_code: str | None
    max_hp: int
    starting_mana: int
    damage_permille: int
    # Flat damage off every blow this player takes, from their class and armour.
    # Defaults to zero, which is what keeps a player with no class -- the
    # reference player `test_pve_balance` measures the whole catalog against --
    # exactly as fragile as they were before defence existed.
    defence: int = 0
    # Already folded into max_hp and starting_mana; carried so the client can
    # explain where the head start came from.
    day_streak: int = 0

    skills: tuple[EquippedSkill, ...] = ()

    def skill(self, code: str) -> EquippedSkill | None:
        """The equipped skill with this code, or None if it is not equipped."""
        return next((skill for skill in self.skills if skill.code == code), None)


def default_loadout(user_id: str) -> PlayerLoadout:
    """Stats for a player who has not picked a class.

    They still fight, on the baseline numbers and with no skills, so the
    feature never blocks an existing player out of a match.
    """
    return PlayerLoadout(
        user_id=user_id,
        level=1,
        class_code=None,
        max_hp=MAX_HP,
        starting_mana=0,
        damage_permille=PERMILLE_ONE,
    )


@dataclass
class ActiveEffect:
    """A skill's effect while it is still standing.

    `expires_at` is inclusive: the effect applies while the caller's current
    reading is less than or equal to it. The reading is deliberately unitless,
    because the two engines count time differently and neither should have to
    own a second copy of this logic -- duo passes a round index, so a skill
    fired in round N that affects this round expires at N and one that affects
    the next expires at N + 1; a lesson battle passes monotonic seconds, so the
    same skill expires that many seconds out. Whatever the caller passes to
    `add` it must also pass to `active`, `consume`, `prune` and `codes`.
    """

    effect: SkillEffect
    magnitude: int
    expires_at: float
    source_code: str = ""


@dataclass
class SkillUseRecord:
    """One firing, buffered until the match is written."""

    round_index: int
    user_id: str
    skill_id: str
    skill_code: str
    mana_spent: int


@dataclass
class EffectState:
    """The effects standing on one player, on the caller's own time scale."""

    effects: list[ActiveEffect] = field(default_factory=list)

    def add(self, effect: ActiveEffect) -> None:
        self.effects.append(effect)

    def active(self, effect: SkillEffect, at: float) -> ActiveEffect | None:
        return next(
            (
                candidate
                for candidate in self.effects
                if candidate.effect is effect and candidate.expires_at >= at
            ),
            None,
        )

    def consume(self, effect: SkillEffect, at: float) -> ActiveEffect | None:
        """Take an effect off the stack as it is spent."""
        found = self.active(effect, at)
        if found is not None:
            self.effects.remove(found)
        return found

    def prune(self, at: float) -> None:
        self.effects = [
            candidate for candidate in self.effects if candidate.expires_at >= at
        ]

    def codes(self, at: float) -> list[str]:
        return [
            candidate.source_code
            for candidate in self.effects
            if candidate.expires_at >= at
        ]
