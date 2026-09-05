"""Turning catalog rows into the monster that guards a given lesson.

Kept apart from both the engine and the REST service because the two ask the
same question in different shapes: the engine wants one lesson's monster, the
course map wants nine hundred at once, and neither should re-derive the rule.
"""

from app.models.game.monster import Monster
from app.services.pve.monster import (
    MonsterProfile,
    is_boss_lesson,
    select_monster,
    tier_for_unit,
)


def to_profile(row: Monster) -> MonsterProfile:
    """Freeze a catalog row into what a battle reads."""
    return MonsterProfile(
        code=row.code,
        name=row.name,
        tier=row.tier,
        max_hp=row.max_hp,
        attack_damage=row.attack_damage,
        damage_reduction_permille=row.damage_reduction_permille,
        enrage_after_rounds=row.enrage_after_rounds,
        enrage_multiplier_permille=row.enrage_multiplier_permille,
        is_boss=row.is_boss,
        art_code=row.art_code,
        weak_topic_id=row.weak_topic_id,
    )


def resolve_for_lesson(
    catalog: list[Monster],
    *,
    unit_order_index: int,
    lesson_order_index: int,
    last_order_index: int,
) -> MonsterProfile | None:
    """Which monster stands on this lesson.

    Derived rather than stored: the tier follows the unit's place on the path
    and the boss stands on the unit's last lesson, so hundreds of gates need no
    rows of their own and an imported batch is guarded the moment it lands.
    """
    return select_monster(
        [to_profile(row) for row in catalog],
        tier=tier_for_unit(unit_order_index),
        is_boss=is_boss_lesson(lesson_order_index, last_order_index),
    )
