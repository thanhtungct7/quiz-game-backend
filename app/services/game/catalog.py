"""The class and skill catalog, and the seeder that upserts it.

Every number here is balance data. Changing one is a data change: the engine
reads magnitudes and feeds them to `combat.resolve_blow`, so no value in this
file can introduce a new code path.

Seeding is an upsert keyed on `code`, so it runs on every startup and is
harmless to repeat. It never deletes: a skill somebody already unlocked stays
unlockable-from, and retiring one is done with `is_active = false`.
"""

from dataclasses import dataclass

from app.models.game.achievement import AchievementCategory, AchievementMetric
from app.models.game.game_item import EquipmentSlot, ItemKind, ItemRarity
from app.models.game.skill import SkillEffect, SkillUnlockKind
from app.repository.game.achievement_repository import AchievementRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.monster_repository import MonsterRepository
from app.services.game.achievements import AchievementSpec

# A monster that is kept waiting starts hitting harder, so a long lesson
# cannot be farmed as a safe place to sit. Nine authored rounds is 54 seconds
# of fight -- the last third of an ordinary gate and the second half of a boss,
# so rage is what closes a fight rather than what decides it.
ENRAGE_AFTER_ROUNDS = 9
ENRAGE_MULTIPLIER_PERMILLE = 1400

WARRIOR = "WARRIOR"
MAGE = "MAGE"
ASSASSIN = "ASSASSIN"


@dataclass(frozen=True)
class ClassSpec:
    code: str
    name: str
    description: str
    max_hp: int
    damage_permille: int
    starting_mana: int
    # Flat damage off every incoming blow. Small numbers on purpose: the whole
    # scale is one-digit, because a monster hits for 8 to 14 and a duo blow
    # lands around 14, so a single point is already worth something.
    defence: int
    sort_order: int


# Health and damage trade against each other; starting mana decides how early
# a class can act. A warrior outlasts, a mage races, an assassin builds.
CLASSES = (
    ClassSpec(
        code=WARRIOR,
        name="Chiến binh",
        description="Giáp dày, đòn nhẹ hơn. Chịu đòn giỏi để thắng bằng độ bền.",
        # Health came down from 130 when DEF arrived. The two are the same
        # resource, and stacking both would have made this the only class worth
        # picking; defence is what makes a warrior a warrior now, not bulk.
        max_hp=115,
        damage_permille=900,
        starting_mana=10,
        defence=3,
        sort_order=1,
    ),
    ClassSpec(
        code=MAGE,
        name="Pháp sư",
        description="Sát thương cao nhưng mỏng manh. Kết thúc trận trước khi bị bắt kịp.",
        max_hp=80,
        damage_permille=1250,
        starting_mana=30,
        defence=0,
        sort_order=2,
    ),
    ClassSpec(
        code=ASSASSIN,
        name="Sát thủ",
        description="Cân bằng, mạnh lên theo chuỗi trả lời đúng liên tiếp.",
        max_hp=100,
        damage_permille=1050,
        starting_mana=20,
        defence=1,
        sort_order=3,
    ),
)


@dataclass(frozen=True)
class SkillSpec:
    code: str
    name: str
    description: str
    effect: SkillEffect
    mana_cost: int
    magnitude: int
    unlock_kind: SkillUnlockKind
    class_code: str | None = None
    tier: int = 0
    parent_code: str | None = None
    duration_rounds: int = 0
    unlock_level: int = 1
    gold_price: int = 0
    sort_order: int = 0


# `magnitude` is read against `effect`: percent for DOUBLE_DAMAGE, thousandths
# for DAMAGE_REDUCTION, health for HEAL, seconds for TIME_PENALTY, options for
# REMOVE_OPTIONS, mana for MANA_BURN, health threshold for EXECUTE.
SKILLS = (
    # Neutral starters, granted free with the first profile so nobody ever
    # walks into a match with an empty bar.
    SkillSpec(
        code="STRIKE_X2",
        name="Song kích",
        description="Đòn của hiệp này nhân đôi sát thương.",
        effect=SkillEffect.DOUBLE_DAMAGE,
        mana_cost=50,
        magnitude=200,
        unlock_kind=SkillUnlockKind.STARTER,
        sort_order=1,
    ),
    SkillSpec(
        code="SHIELD",
        name="Khiên chắn",
        description="Giảm một nửa sát thương phải nhận trong hiệp này.",
        effect=SkillEffect.DAMAGE_REDUCTION,
        mana_cost=40,
        magnitude=500,
        unlock_kind=SkillUnlockKind.STARTER,
        sort_order=2,
    ),
    # Warrior: outlast.
    SkillSpec(
        code="WAR_GUARD",
        name="Trấn thủ",
        description="Giảm 70% sát thương phải nhận trong hiệp này.",
        effect=SkillEffect.DAMAGE_REDUCTION,
        mana_cost=45,
        magnitude=700,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=WARRIOR,
        tier=1,
        unlock_level=2,
        gold_price=150,
        sort_order=10,
    ),
    SkillSpec(
        code="WAR_MEND",
        name="Hồi phục",
        description="Hồi 25 máu ngay lập tức.",
        effect=SkillEffect.HEAL,
        mana_cost=50,
        magnitude=25,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=WARRIOR,
        tier=2,
        parent_code="WAR_GUARD",
        unlock_level=5,
        gold_price=300,
        sort_order=11,
    ),
    SkillSpec(
        code="WAR_LAST_STAND",
        name="Tử thủ",
        description="Hồi 40 máu ngay lập tức.",
        effect=SkillEffect.HEAL,
        mana_cost=70,
        magnitude=40,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=WARRIOR,
        tier=3,
        parent_code="WAR_MEND",
        unlock_level=9,
        gold_price=600,
        sort_order=12,
    ),
    # Mage: race, and slow the other side down.
    SkillSpec(
        code="MAGE_BOLT",
        name="Lôi kích",
        description="Đòn của hiệp này gây 250% sát thương.",
        effect=SkillEffect.DOUBLE_DAMAGE,
        mana_cost=55,
        magnitude=250,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=MAGE,
        tier=1,
        unlock_level=2,
        gold_price=150,
        sort_order=20,
    ),
    SkillSpec(
        code="MAGE_FREEZE",
        name="Băng giá",
        description="Rút ngắn 5 giây thời gian trả lời của đối thủ ở hiệp kế tiếp.",
        effect=SkillEffect.TIME_PENALTY,
        mana_cost=55,
        magnitude=5,
        duration_rounds=1,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=MAGE,
        tier=2,
        parent_code="MAGE_BOLT",
        unlock_level=5,
        gold_price=300,
        sort_order=21,
    ),
    SkillSpec(
        code="MAGE_DRAIN",
        name="Hút mana",
        description="Đốt 40 mana của đối thủ.",
        effect=SkillEffect.MANA_BURN,
        mana_cost=35,
        magnitude=40,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=MAGE,
        tier=3,
        parent_code="MAGE_FREEZE",
        unlock_level=9,
        gold_price=600,
        sort_order=22,
    ),
    # Assassin: protect the combo, then cash it in.
    SkillSpec(
        code="ASSA_FOCUS",
        name="Tập trung",
        description="Trả lời sai ở hiệp kế tiếp không làm mất chuỗi combo.",
        effect=SkillEffect.COMBO_KEEP,
        mana_cost=40,
        magnitude=1,
        duration_rounds=1,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=ASSASSIN,
        tier=1,
        unlock_level=2,
        gold_price=150,
        sort_order=30,
    ),
    SkillSpec(
        code="ASSA_REVEAL",
        name="Nhìn thấu",
        description="Loại bỏ 2 đáp án sai, chỉ mình bạn thấy.",
        effect=SkillEffect.REMOVE_OPTIONS,
        mana_cost=35,
        magnitude=2,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=ASSASSIN,
        tier=2,
        parent_code="ASSA_FOCUS",
        unlock_level=5,
        gold_price=300,
        sort_order=31,
    ),
    SkillSpec(
        code="ASSA_EXECUTE",
        name="Kết liễu",
        description="Sát thương nhân đôi khi đối thủ còn dưới 30 máu.",
        effect=SkillEffect.EXECUTE,
        mana_cost=60,
        magnitude=30,
        unlock_kind=SkillUnlockKind.LEVEL_GOLD,
        class_code=ASSASSIN,
        tier=3,
        parent_code="ASSA_REVEAL",
        unlock_level=9,
        gold_price=600,
        sort_order=32,
    ),
)

# Ultimates cost no gold and answer to no level. They are bought with study:
# finish every lesson of the unit they are bound to and they are yours. Neutral
# on purpose, so the reward for learning is not gated behind a class choice.
ULTIMATES = (
    SkillSpec(
        code="ULT_OVERDRIVE",
        name="Bùng nổ",
        description="Đòn của hiệp này gây 300% sát thương.",
        effect=SkillEffect.DOUBLE_DAMAGE,
        mana_cost=80,
        magnitude=300,
        unlock_kind=SkillUnlockKind.UNIT_COMPLETION,
        tier=3,
        sort_order=40,
    ),
    SkillSpec(
        code="ULT_AEGIS",
        name="Thánh khiên",
        description="Giảm 80% sát thương phải nhận trong hiệp này.",
        effect=SkillEffect.DAMAGE_REDUCTION,
        mana_cost=70,
        magnitude=800,
        unlock_kind=SkillUnlockKind.UNIT_COMPLETION,
        tier=3,
        sort_order=41,
    ),
    SkillSpec(
        code="ULT_SIPHON",
        name="Hồi sinh",
        description="Hồi 50 máu ngay lập tức.",
        effect=SkillEffect.HEAL,
        mana_cost=90,
        magnitude=50,
        unlock_kind=SkillUnlockKind.UNIT_COMPLETION,
        tier=3,
        sort_order=42,
    ),
)

STARTER_CODES = tuple(
    spec.code for spec in SKILLS if spec.unlock_kind is SkillUnlockKind.STARTER
)


def _class_row(spec: ClassSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "max_hp": spec.max_hp,
        "damage_permille": spec.damage_permille,
        "starting_mana": spec.starting_mana,
        "defence": spec.defence,
        "sort_order": spec.sort_order,
        "is_active": True,
    }


def _skill_row(spec: SkillSpec, unit_id: str | None) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "effect": spec.effect,
        "class_code": spec.class_code,
        "tier": spec.tier,
        "parent_code": spec.parent_code,
        "mana_cost": spec.mana_cost,
        "magnitude": spec.magnitude,
        "duration_rounds": spec.duration_rounds,
        "unlock_kind": spec.unlock_kind,
        "unlock_level": spec.unlock_level,
        "gold_price": spec.gold_price,
        "unlock_unit_id": unit_id,
        "is_active": True,
        "sort_order": spec.sort_order,
    }


async def seed_game_catalog(catalog: CatalogRepository) -> None:
    """Bring the classes and skills in the database up to date with this file.

    Ultimates are bound to the first few units of the learn path in order. An
    install with no content loaded yet simply gets no ultimates rather than
    failing -- they appear once the question bank is imported and this runs
    again on the next startup.
    """
    for class_spec in CLASSES:
        await catalog.upsert_class(class_spec.code, _class_row(class_spec))

    for skill_spec in SKILLS:
        await catalog.upsert_skill(skill_spec.code, _skill_row(skill_spec, None))

    unit_ids = await catalog.first_unit_ids(len(ULTIMATES))
    for ultimate, unit_id in zip(ULTIMATES, unit_ids, strict=False):
        await catalog.upsert_skill(ultimate.code, _skill_row(ultimate, unit_id))


def resolve_class(code: str | None) -> ClassSpec | None:
    return next((spec for spec in CLASSES if spec.code == code), None)


@dataclass(frozen=True)
class ItemSpec:
    code: str
    name: str
    kind: ItemKind
    rarity: ItemRarity
    slot: EquipmentSlot | None = None
    bonus_max_hp: int = 0
    bonus_damage_permille: int = 0
    bonus_starting_mana: int = 0
    bonus_defence: int = 0


# Individually small, and capped again when combined (`loot.total_bonus`).
# Equipment flavours a build; it must not decide a match before the first
# question. Skins and cards carry no bonus at all -- they are there to make a
# chest worth opening without touching balance.
ITEMS = (
    ItemSpec(
        code="WOODEN_SWORD",
        name="Kiếm gỗ",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.COMMON,
        slot=EquipmentSlot.WEAPON,
        bonus_damage_permille=20,
    ),
    ItemSpec(
        code="STEEL_SWORD",
        name="Kiếm thép",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.RARE,
        slot=EquipmentSlot.WEAPON,
        bonus_damage_permille=45,
    ),
    ItemSpec(
        code="RUNE_BLADE",
        name="Kiếm khắc ấn",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.EPIC,
        slot=EquipmentSlot.WEAPON,
        bonus_damage_permille=70,
    ),
    ItemSpec(
        code="LEATHER_VEST",
        name="Áo da",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.COMMON,
        slot=EquipmentSlot.ARMOR,
        bonus_max_hp=4,
    ),
    ItemSpec(
        code="CHAIN_MAIL",
        name="Giáp xích",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.RARE,
        slot=EquipmentSlot.ARMOR,
        bonus_max_hp=8,
        bonus_defence=1,
    ),
    ItemSpec(
        code="DRAGON_PLATE",
        name="Giáp rồng",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.LEGENDARY,
        slot=EquipmentSlot.ARMOR,
        bonus_max_hp=14,
        bonus_damage_permille=15,
        bonus_defence=2,
    ),

    ItemSpec(
        code="MANA_RING",
        name="Nhẫn mana",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.COMMON,
        slot=EquipmentSlot.TRINKET,
        bonus_starting_mana=4,
    ),
    ItemSpec(
        code="SAGE_AMULET",
        name="Bùa hiền triết",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.EPIC,
        slot=EquipmentSlot.TRINKET,
        bonus_starting_mana=9,
        bonus_max_hp=3,
    ),
    ItemSpec(
        code="SKIN_SCHOLAR",
        name="Trang phục Học giả",
        kind=ItemKind.SKIN,
        rarity=ItemRarity.RARE,
    ),
    ItemSpec(
        code="SKIN_NIGHT",
        name="Trang phục Dạ hành",
        kind=ItemKind.SKIN,
        rarity=ItemRarity.EPIC,
    ),
    ItemSpec(
        code="CARD_STREAK",
        name="Thẻ Chuyên cần",
        kind=ItemKind.CARD,
        rarity=ItemRarity.COMMON,
    ),
    ItemSpec(
        code="CARD_CHAMPION",
        name="Thẻ Quán quân",
        kind=ItemKind.CARD,
        rarity=ItemRarity.LEGENDARY,
    ),
)


def _item_row(spec: ItemSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "kind": spec.kind,
        "slot": spec.slot,
        "rarity": spec.rarity,
        "bonus_max_hp": spec.bonus_max_hp,
        "bonus_damage_permille": spec.bonus_damage_permille,
        "bonus_starting_mana": spec.bonus_starting_mana,
        "bonus_defence": spec.bonus_defence,
        "is_active": True,
    }


async def seed_item_catalog(items: ItemRepository) -> None:
    """Upsert the drop table. Safe to run on every start."""
    for spec in ITEMS:
        await items.upsert_item(spec.code, _item_row(spec))


# --- achievements -----------------------------------------------------------

# Thresholds, not events. Each one is a number measured against a counter the
# system already keeps, so the list can grow at any time and a player already
# past a threshold unlocks it on their next sync rather than having to earn it
# again. See `services/game/achievements.py` for what that rules out.
#
# Three tiers per axis, spaced so the first is reached in a sitting, the second
# takes a habit and the third takes months. A tier nobody reaches is not an
# aspiration, it is a dead row.
ACHIEVEMENTS = (
    # --- progression: the level curve itself ---
    AchievementSpec(
        code="LEVEL_5",
        name="Nhập môn",
        description="Đạt cấp 5.",
        category=AchievementCategory.PROGRESSION,
        metric=AchievementMetric.LEVEL,
        threshold=5,
        icon_code="LEVEL",
        sort_order=10,
    ),
    AchievementSpec(
        code="LEVEL_20",
        name="Lão luyện",
        description="Đạt cấp 20.",
        category=AchievementCategory.PROGRESSION,
        metric=AchievementMetric.LEVEL,
        threshold=20,
        icon_code="LEVEL",
        sort_order=11,
    ),
    AchievementSpec(
        code="LEVEL_50",
        name="Bậc thầy",
        description="Đạt cấp 50.",
        category=AchievementCategory.PROGRESSION,
        metric=AchievementMetric.LEVEL,
        threshold=50,
        icon_code="CROWN",
        sort_order=12,
    ),
    # --- learning: showing up, and what showing up adds up to ---
    # The first two read `day_streak`, which falls when a day is missed, so
    # they are a badge for a streak the player is holding right now. The
    # thirty-day one reads `best_day_streak` instead: a month of study should
    # not be taken away by one weekend.
    AchievementSpec(
        code="STREAK_3",
        name="Ba ngày liền",
        description="Học ba ngày liên tiếp.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.DAY_STREAK,
        threshold=3,
        icon_code="FLAME",
        sort_order=20,
    ),
    AchievementSpec(
        code="STREAK_7",
        name="Trọn một tuần",
        description="Học bảy ngày liên tiếp.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.DAY_STREAK,
        threshold=7,
        icon_code="FLAME",
        sort_order=21,
    ),
    AchievementSpec(
        code="STREAK_30",
        name="Ba mươi ngày",
        description="Từng giữ chuỗi ba mươi ngày.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.BEST_DAY_STREAK,
        threshold=30,
        icon_code="FLAME",
        sort_order=22,
    ),
    AchievementSpec(
        code="MASTERED_50",
        name="Năm mươi câu",
        description="Thuộc năm mươi câu hỏi.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.CHALLENGES_MASTERED,
        threshold=50,
        icon_code="BOOK",
        sort_order=23,
    ),
    AchievementSpec(
        code="MASTERED_250",
        name="Vốn từ dày",
        description="Thuộc hai trăm năm mươi câu hỏi.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.CHALLENGES_MASTERED,
        threshold=250,
        icon_code="BOOK",
        sort_order=24,
    ),
    AchievementSpec(
        code="MASTERED_1000",
        name="Nghìn câu",
        description="Thuộc một nghìn câu hỏi.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.CHALLENGES_MASTERED,
        threshold=1000,
        icon_code="BOOK",
        sort_order=25,
    ),
    AchievementSpec(
        code="ATTEMPTS_500",
        name="Cần cù",
        description="Trả lời năm trăm lượt.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.TOTAL_ATTEMPTS,
        threshold=500,
        icon_code="TARGET",
        sort_order=26,
    ),
    AchievementSpec(
        code="LESSONS_10",
        name="Mười bài học",
        description="Hoàn thành mười bài học.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.LESSONS_COMPLETED,
        threshold=10,
        icon_code="PATH",
        sort_order=27,
    ),
    AchievementSpec(
        code="LESSONS_50",
        name="Đi hết chặng dài",
        description="Hoàn thành năm mươi bài học.",
        category=AchievementCategory.LEARNING,
        metric=AchievementMetric.LESSONS_COMPLETED,
        threshold=50,
        icon_code="PATH",
        sort_order=28,
    ),
    # --- the arena ---
    AchievementSpec(
        code="PVP_WIN_1",
        name="Trận thắng đầu tiên",
        description="Thắng một trận đấu.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_WINS,
        threshold=1,
        icon_code="SWORD",
        sort_order=30,
    ),
    AchievementSpec(
        code="PVP_WIN_25",
        name="Quen mặt đấu trường",
        description="Thắng hai mươi lăm trận.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_WINS,
        threshold=25,
        icon_code="SWORD",
        sort_order=31,
    ),
    AchievementSpec(
        code="PVP_WIN_100",
        name="Trăm trận",
        description="Thắng một trăm trận.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_WINS,
        threshold=100,
        icon_code="TROPHY",
        sort_order=32,
    ),
    AchievementSpec(
        code="PVP_RATING_1200",
        name="Leo hạng",
        description="Đạt 1200 điểm xếp hạng.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_RATING,
        threshold=1200,
        icon_code="BOLT",
        sort_order=33,
    ),
    AchievementSpec(
        code="PVP_RATING_1600",
        name="Cao thủ",
        description="Đạt 1600 điểm xếp hạng.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_RATING,
        threshold=1600,
        icon_code="BOLT",
        sort_order=34,
    ),
    AchievementSpec(
        code="PVP_STREAK_5",
        name="Năm trận liên tiếp",
        description="Từng thắng năm trận liền.",
        category=AchievementCategory.PVP,
        metric=AchievementMetric.PVP_BEST_STREAK,
        threshold=5,
        icon_code="TROPHY",
        sort_order=35,
    ),
    # --- the learn path's gates ---
    AchievementSpec(
        code="BATTLE_WIN_1",
        name="Hạ gục quái đầu tiên",
        description="Thắng một trận đánh quái.",
        category=AchievementCategory.PVE,
        metric=AchievementMetric.BATTLES_WON,
        threshold=1,
        icon_code="SKULL",
        sort_order=40,
    ),
    AchievementSpec(
        code="BATTLE_WIN_25",
        name="Thợ săn",
        description="Thắng hai mươi lăm trận đánh quái.",
        category=AchievementCategory.PVE,
        metric=AchievementMetric.BATTLES_WON,
        threshold=25,
        icon_code="SKULL",
        sort_order=41,
    ),
    AchievementSpec(
        code="BATTLE_WIN_100",
        name="Khắc tinh của quái vật",
        description="Thắng một trăm trận đánh quái.",
        category=AchievementCategory.PVE,
        metric=AchievementMetric.BATTLES_WON,
        threshold=100,
        icon_code="CROWN",
        sort_order=42,
    ),
)


def _achievement_row(spec: AchievementSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "category": spec.category,
        "metric": spec.metric,
        "threshold": spec.threshold,
        "icon_code": spec.icon_code,
        "sort_order": spec.sort_order,
        "is_hidden": spec.is_hidden,
        "is_active": True,
    }


async def seed_achievement_catalog(achievements: AchievementRepository) -> None:
    """Upsert the achievement list. Safe to run on every start."""
    for spec in ACHIEVEMENTS:
        await achievements.upsert_achievement(spec.code, _achievement_row(spec))


# --- monsters ---------------------------------------------------------------


@dataclass(frozen=True)
class MonsterSpec:
    code: str
    name: str
    description: str
    tier: int
    max_hp: int
    attack_damage: int
    art_code: str
    is_boss: bool = False
    damage_reduction_permille: int = 0
    enrage_after_rounds: int = ENRAGE_AFTER_ROUNDS
    enrage_multiplier_permille: int = ENRAGE_MULTIPLIER_PERMILLE
    sort_order: int = 0


# Six tiers, each with the monster that stands on ordinary lessons and the boss
# that closes the unit.
#
# Health sets one thing and one thing only: how many correct answers a gate
# takes. A lesson holds ten questions, and an ordinary gate is meant to be the
# whole lesson -- so every ordinary monster carries the same health, sized to
# the tenth correct answer at a normal pace, and every boss to the fifteenth.
# Answering fast still gets there sooner (about eight and eleven), because
# speed and combos multiply the same numbers. That is why the health column
# barely moves across tiers: the tier is expressed in what the monster does
# back, not in how long it takes to fall.
#
# Attack is sized against the fight it now has time to happen in. An ordinary
# gate runs about a minute and costs a player at a normal pace a quarter to a
# half of a 100-health bar; a boss runs about a minute and a half and costs
# most of it. A player who gets one answer in four wrong wins the early gates
# and loses the late ones, which is what a gate is for.
#
# The two health numbers that break the pattern are the armoured ones: a
# monster that soaks a tenth or a seventh of every blow needs proportionally
# less health to still fall on the same answer.
#
# `weak_topic_id` is left NULL everywhere on purpose. The wiring is in place
# (`element_multiplier` feeds `resolve_blow`), but no balance leans on it yet.
MONSTERS = (
    MonsterSpec(
        code="SLIME",
        name="Slime",
        description="Chậm và yếu. Con quái đầu tiên ai cũng hạ được.",
        tier=1,
        max_hp=235,
        attack_damage=8,
        art_code="SLIME",
        sort_order=1,
    ),
    MonsterSpec(
        code="SLIME_KING",
        name="Slime Vương",
        description="To gấp đôi và biết giận. Giữ cửa cuối của unit.",
        tier=1,
        max_hp=375,
        attack_damage=11,
        art_code="SLIME_KING",
        is_boss=True,
        sort_order=2,
    ),
    MonsterSpec(
        code="GOBLIN",
        name="Yêu tinh",
        description="Nhanh nhẹn, đánh đau hơn Slime.",
        tier=2,
        max_hp=235,
        attack_damage=9,
        art_code="GOBLIN",
        sort_order=3,
    ),
    MonsterSpec(
        code="GOBLIN_CHIEF",
        name="Tù trưởng Yêu tinh",
        description="Cầm đầu cả bầy. Càng để lâu càng hung.",
        tier=2,
        max_hp=375,
        attack_damage=12,
        art_code="GOBLIN_CHIEF",
        is_boss=True,
        sort_order=4,
    ),
    MonsterSpec(
        code="DIRE_WOLF",
        name="Sói hoang",
        description="Không cho bạn nghỉ giữa hai câu hỏi.",
        tier=3,
        max_hp=235,
        attack_damage=10,
        art_code="DIRE_WOLF",
        enrage_after_rounds=7,
        sort_order=5,
    ),
    MonsterSpec(
        code="ALPHA_WOLF",
        name="Sói đầu đàn",
        description="Nổi điên sớm hơn cả bầy của nó.",
        tier=3,
        max_hp=375,
        attack_damage=13,
        art_code="ALPHA_WOLF",
        is_boss=True,
        enrage_after_rounds=7,
        sort_order=6,
    ),
    MonsterSpec(
        code="GOLEM",
        name="Golem đá",
        description="Da đá: mọi đòn đánh vào nó đều nhẹ đi một phần.",
        tier=4,
        max_hp=225,
        attack_damage=11,
        art_code="GOLEM",
        damage_reduction_permille=100,
        sort_order=7,
    ),
    MonsterSpec(
        code="STONE_TITAN",
        name="Thần đá",
        description="Cả một vách núi biết đi. Đòn chậm nhưng rất nặng.",
        tier=4,
        max_hp=345,
        attack_damage=14,
        art_code="STONE_TITAN",
        is_boss=True,
        damage_reduction_permille=150,
        sort_order=8,
    ),
    MonsterSpec(
        code="WRAITH",
        name="Oán linh",
        description="Đánh thẳng vào tinh thần. Sai một câu là trả giá.",
        tier=5,
        max_hp=235,
        attack_damage=12,
        art_code="WRAITH",
        sort_order=9,
    ),
    MonsterSpec(
        code="LICH",
        name="Vua Lich",
        description="Chờ bạn mệt rồi mới ra đòn thật.",
        tier=5,
        max_hp=360,
        attack_damage=15,
        art_code="LICH",
        is_boss=True,
        damage_reduction_permille=100,
        sort_order=10,
    ),
    MonsterSpec(
        code="DRAKE",
        name="Rồng con",
        description="Chưa phải rồng, nhưng đã đủ để thiêu bạn.",
        tier=6,
        max_hp=235,
        attack_damage=13,
        art_code="DRAKE",
        sort_order=11,
    ),
    MonsterSpec(
        code="DRAGON",
        name="Rồng lửa",
        description="Cửa cuối của con đường. Sai bốn câu là hết máu.",
        tier=6,
        max_hp=345,
        attack_damage=16,
        art_code="DRAGON",
        is_boss=True,
        damage_reduction_permille=150,
        sort_order=12,
    ),
)


def _monster_row(spec: MonsterSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "description": spec.description,
        "tier": spec.tier,
        "max_hp": spec.max_hp,
        "attack_damage": spec.attack_damage,
        "damage_reduction_permille": spec.damage_reduction_permille,
        "enrage_after_rounds": spec.enrage_after_rounds,
        "enrage_multiplier_permille": spec.enrage_multiplier_permille,
        "is_boss": spec.is_boss,
        "art_code": spec.art_code,
        "sort_order": spec.sort_order,
        "is_active": True,
    }


async def seed_monster_catalog(monsters: MonsterRepository) -> None:
    """Upsert the monster catalog. Safe to run on every start.

    Never deletes: a battle in history names its monster by code, and retiring
    one is done with `is_active = false`.
    """
    for spec in MONSTERS:
        await monsters.upsert_monster(spec.code, _monster_row(spec))
