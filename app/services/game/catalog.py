"""The class and skill catalog, and the seeder that upserts it.

Every number here is balance data. Changing one is a data change: the engine
reads magnitudes and feeds them to `combat.resolve_blow`, so no value in this
file can introduce a new code path.

Seeding is an upsert keyed on `code`, so it runs on every startup and is
harmless to repeat. It never deletes: a skill somebody already unlocked stays
unlockable-from, and retiring one is done with `is_active = false`.
"""

from dataclasses import dataclass

from app.models.game.game_item import EquipmentSlot, ItemKind, ItemRarity
from app.models.game.skill import SkillEffect, SkillUnlockKind
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.item_repository import ItemRepository

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
    sort_order: int


# Health and damage trade against each other; starting mana decides how early
# a class can act. A warrior outlasts, a mage races, an assassin builds.
CLASSES = (
    ClassSpec(
        code=WARRIOR,
        name="Chiến binh",
        description="Nhiều máu, đòn nhẹ hơn. Sống lâu để thắng bằng độ bền.",
        max_hp=130,
        damage_permille=900,
        starting_mana=10,
        sort_order=1,
    ),
    ClassSpec(
        code=MAGE,
        name="Pháp sư",
        description="Sát thương cao nhưng mỏng manh. Kết thúc trận trước khi bị bắt kịp.",
        max_hp=80,
        damage_permille=1250,
        starting_mana=30,
        sort_order=2,
    ),
    ClassSpec(
        code=ASSASSIN,
        name="Sát thủ",
        description="Cân bằng, mạnh lên theo chuỗi trả lời đúng liên tiếp.",
        max_hp=100,
        damage_permille=1050,
        starting_mana=20,
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
    ),
    ItemSpec(
        code="DRAGON_PLATE",
        name="Giáp rồng",
        kind=ItemKind.EQUIPMENT,
        rarity=ItemRarity.LEGENDARY,
        slot=EquipmentSlot.ARMOR,
        bonus_max_hp=14,
        bonus_damage_permille=15,
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
        "is_active": True,
    }


async def seed_item_catalog(items: ItemRepository) -> None:
    """Upsert the drop table. Safe to run on every start."""
    for spec in ITEMS:
        await items.upsert_item(spec.code, _item_row(spec))
