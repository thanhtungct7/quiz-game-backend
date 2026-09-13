from datetime import UTC, datetime

import httpx
import pytest

from app.api.dependencies import get_current_user, get_game_service
from app.core.exceptions import BenchmarkExamNotEligibleError
from app.main import app
from app.models.auth.user import User
from app.models.duo.duo_rating import DuoRating
from app.models.game.game_class import GameClass
from app.models.game.game_item import EquipmentSlot, GameItem, ItemKind
from app.models.game.gold_transaction import GoldReason
from app.models.game.season import GameSeason, SeasonRating
from app.models.game.skill import Skill
from app.models.game.user_game_profile import UserGameProfile
from app.services.game.catalog import (
    CLASSES,
    ITEMS,
    SKILLS,
    SKIN_PRICE_RARE,
    ULTIMATES,
    WARRIOR,
)
from app.services.game.cefr import LEVEL_CAPS
from app.services.game.energy import MAX_ENERGY
from app.services.game.game_service import CLASS_CHANGE_GOLD, GameService
from app.services.game.leveling import exp_for_level

ULTIMATE_UNIT_ID = "unit-1"


def _classes() -> list[GameClass]:
    return [
        GameClass(
            id=f"class-{spec.code}",
            code=spec.code,
            name=spec.name,
            description=spec.description,
            max_hp=spec.max_hp,
            damage_permille=spec.damage_permille,
            starting_mana=spec.starting_mana,
            defence=spec.defence,
            sort_order=spec.sort_order,
            is_active=True,
        )
        for spec in CLASSES
    ]


def _skills() -> list[Skill]:
    """The real catalog, so these tests break if the balance data goes wrong."""
    rows = []
    for spec in SKILLS:
        rows.append(
            Skill(
                id=f"skill-{spec.code}",
                code=spec.code,
                name=spec.name,
                description=spec.description,
                effect=spec.effect,
                class_code=spec.class_code,
                tier=spec.tier,
                parent_code=spec.parent_code,
                mana_cost=spec.mana_cost,
                magnitude=spec.magnitude,
                duration_rounds=spec.duration_rounds,
                unlock_kind=spec.unlock_kind,
                unlock_level=spec.unlock_level,
                gold_price=spec.gold_price,
                unlock_unit_id=None,
                is_active=True,
                sort_order=spec.sort_order,
            )
        )
    ultimate = ULTIMATES[0]
    rows.append(
        Skill(
            id=f"skill-{ultimate.code}",
            code=ultimate.code,
            name=ultimate.name,
            description=ultimate.description,
            effect=ultimate.effect,
            class_code=None,
            tier=ultimate.tier,
            parent_code=None,
            mana_cost=ultimate.mana_cost,
            magnitude=ultimate.magnitude,
            duration_rounds=ultimate.duration_rounds,
            unlock_kind=ultimate.unlock_kind,
            unlock_level=1,
            gold_price=0,
            unlock_unit_id=ULTIMATE_UNIT_ID,
            is_active=True,
            sort_order=ultimate.sort_order,
        )
    )
    return rows


class FakeProfiles:
    def __init__(self, profile: UserGameProfile | None = None) -> None:
        self.profile = profile

    async def get_by_user(self, user_id: str) -> UserGameProfile | None:
        return self.profile

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        if self.profile is None:
            self.profile = _profile()
        return self.profile

    async def save(
        self, profile: UserGameProfile, data: dict[str, object]
    ) -> UserGameProfile:
        for field, value in data.items():
            setattr(profile, field, value)
        return profile


class FakeCatalog:
    def __init__(self) -> None:
        self.classes = _classes()
        self.skills = _skills()

    async def list_classes(self) -> list[GameClass]:
        return self.classes

    async def get_class(self, code: str) -> GameClass | None:
        return next((row for row in self.classes if row.code == code), None)

    async def list_skills(self) -> list[Skill]:
        return self.skills

    async def get_skill(self, skill_id: str) -> Skill | None:
        return next((row for row in self.skills if row.id == skill_id), None)


class FakeUserSkills:
    def __init__(self, owned: set[str] | None = None) -> None:
        self.owned = owned or set()
        self.slots: dict[str, int] = {}

    async def owned_skill_ids(self, user_id: str) -> set[str]:
        return set(self.owned)

    async def grant(self, user_id: str, skill_id: str) -> None:
        self.owned.add(skill_id)

    async def grant_many(self, user_id: str, skill_ids: list[str]) -> None:
        self.owned.update(skill_ids)

    async def equipped_slot_by_skill_id(self, user_id: str) -> dict[str, int]:
        return dict(self.slots)

    async def loadout(self, user_id: str) -> list[tuple[object, Skill]]:
        catalog = {row.id: row for row in _skills()}

        class _Slot:
            def __init__(self, index: int) -> None:
                self.slot_index = index

        return [
            (_Slot(index), catalog[skill_id])
            for skill_id, index in sorted(self.slots.items(), key=lambda pair: pair[1])
        ]

    async def replace_loadout(self, user_id: str, skill_ids: list[str]) -> None:
        self.slots = {skill_id: index for index, skill_id in enumerate(skill_ids)}

    async def clear_loadout(self, user_id: str) -> None:
        self.slots = {}


class FakeLedger:
    def __init__(self) -> None:
        self.rows: list[tuple[str, GoldReason, str, int]] = []

    async def grant(
        self,
        *,
        user_id: str,
        amount: int,
        reason: GoldReason,
        ref_id: str,
        balance_after: int,
    ) -> bool:
        if any(row[:3] == (user_id, reason, ref_id) for row in self.rows):
            return False
        self.rows.append((user_id, reason, ref_id, amount))
        return True


class FakeItems:
    def __init__(self, owned: dict[str, int] | None = None) -> None:
        self.catalog = _items()
        self.owned_counts = owned or {}
        self.worn: dict[EquipmentSlot, str] = {}

    async def get_item(self, item_id: str) -> GameItem | None:
        return next((item for item in self.catalog if item.id == item_id), None)

    async def list_purchasable(self) -> list[GameItem]:
        return [item for item in self.catalog if item.gold_price > 0]

    async def drop_pool(self) -> list[GameItem]:
        return [item for item in self.catalog if item.gold_price == 0]

    async def add_to_inventory(self, user_id: str, item_id: str) -> None:
        self.owned_counts[item_id] = self.owned_counts.get(item_id, 0) + 1

    async def owned(self, user_id: str) -> list[tuple[object, GameItem]]:
        by_id = {item.id: item for item in self.catalog}

        class _Row:
            def __init__(self, quantity: int) -> None:
                self.quantity = quantity

        return [
            (_Row(count), by_id[item_id])
            for item_id, count in self.owned_counts.items()
            if item_id in by_id
        ]

    async def equipment(self, user_id: str) -> list[tuple[object, GameItem]]:
        by_id = {item.id: item for item in self.catalog}
        return [(object(), by_id[item_id]) for item_id in self.worn.values()]

    async def replace_equipment(
        self, user_id: str, by_slot: dict[EquipmentSlot, str]
    ) -> None:
        self.worn = dict(by_slot)


class FakeSeasons:
    def __init__(self, season: GameSeason | None = None) -> None:
        self.season = season or GameSeason(
            id="season-1",
            code="S202608",
            name="Mùa 08/2026",
            starts_at=datetime(2026, 8, 1, tzinfo=UTC),
            ends_at=datetime(2026, 8, 31, tzinfo=UTC),
            is_active=True,
        )
        self.ratings: dict[str, SeasonRating] = {}

    async def active(self) -> GameSeason:
        return self.season

    async def create(self, season: GameSeason) -> GameSeason:
        self.season = season
        return season

    async def rating(self, season_id: str, user_id: str) -> SeasonRating | None:
        return self.ratings.get(user_id)


class FakeRatings:
    def __init__(self, rating: int | None = None) -> None:
        self.value = rating

    async def get_by_user(self, user_id: str) -> DuoRating | None:
        if self.value is None:
            return None
        return DuoRating(id="rating-1", user_id=user_id, rating=self.value)


class FakeProgress:
    def __init__(self, completed_units: set[str] | None = None) -> None:
        self.completed = completed_units or set()

    async def completed_unit_ids(self, user_id: str) -> set[str]:
        return set(self.completed)


class Harness:
    def __init__(
        self,
        *,
        profile: UserGameProfile | None = None,
        owned: set[str] | None = None,
        completed_units: set[str] | None = None,
        owned_items: dict[str, int] | None = None,
        all_time_rating: int | None = None,
    ) -> None:
        self.profiles = FakeProfiles(profile)
        self.catalog = FakeCatalog()
        self.skills = FakeUserSkills(owned)
        self.ledger = FakeLedger()
        self.progress = FakeProgress(completed_units)
        self.items = FakeItems(owned_items)
        self.seasons = FakeSeasons()
        self.ratings = FakeRatings(all_time_rating)

    def service(self) -> GameService:
        return GameService(
            profiles=self.profiles,  # type: ignore[arg-type]
            catalog=self.catalog,  # type: ignore[arg-type]
            skills=self.skills,  # type: ignore[arg-type]
            ledger=self.ledger,  # type: ignore[arg-type]
            progress=self.progress,  # type: ignore[arg-type]
            items=self.items,  # type: ignore[arg-type]
            seasons=self.seasons,  # type: ignore[arg-type]
            ratings=self.ratings,  # type: ignore[arg-type]
        )

    async def request(
        self, method: str, path: str, json: dict[str, object] | None = None
    ) -> httpx.Response:
        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_game_service] = self.service
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, f"/api/v1/game{path}", json=json)
        finally:
            app.dependency_overrides.clear()


def _user() -> User:
    return User(id="user-1", email="user@example.com")


def _profile(
    *,
    total_exp: int = 0,
    level: int = 1,
    gold: int = 0,
    class_code: str | None = None,
    energy: int = MAX_ENERGY,
    day_streak: int = 0,
    benchmark_cleared_level: int = 0,
    skin_code: str | None = None,
) -> UserGameProfile:
    """A profile as it comes back from the database: every column spelled out,
    because constructing the model outside a session skips the defaults."""
    return UserGameProfile(
        id="profile-1",
        user_id="user-1",
        total_exp=total_exp,
        level=level,
        gold=gold,
        class_code=class_code,
        energy=energy,
        energy_updated_at=datetime.now(UTC),
        day_streak=day_streak,
        best_day_streak=day_streak,
        last_active_date=None,
        benchmark_cleared_level=benchmark_cleared_level,
        skin_code=skin_code,
    )


def _items() -> list[GameItem]:
    return [
        GameItem(
            id=f"item-{spec.code}",
            code=spec.code,
            name=spec.name,
            kind=spec.kind,
            slot=spec.slot,
            rarity=spec.rarity,
            bonus_exp_permille=spec.bonus_exp_permille,
            bonus_gold_permille=spec.bonus_gold_permille,
            gold_price=spec.gold_price,
            is_active=True,
        )
        for spec in ITEMS
    ]


def _node(body: dict[str, object], code: str) -> dict[str, object]:
    return next(node for node in body["skills"] if node["code"] == code)  # type: ignore[index,union-attr]


# --- profile ---------------------------------------------------------------


async def test_profile_reports_level_and_progress_toward_the_next() -> None:
    harness = Harness(profile=_profile(total_exp=450, level=3, gold=120))

    response = await harness.request("GET", "/profile")

    assert response.status_code == 200
    body = response.json()
    assert body["level"] == 3
    assert body["exp_for_current_level"] == 300
    assert body["exp_for_next_level"] == 600
    assert body["exp_to_next_level"] == 150
    assert body["gold"] == 120
    assert body["class_code"] is None


#: Granted free on first contact with the game layer, so they are owned in every
#: test that touches a `/game` endpoint -- including the ones asserting that a
#: refused unlock granted nothing.
STARTER_IDS = {"skill-STRIKE_X2", "skill-SHIELD"}


async def test_a_player_who_never_played_gets_a_profile_and_the_starters() -> None:
    harness = Harness()

    response = await harness.request("GET", "/profile")

    assert response.status_code == 200
    assert response.json()["level"] == 1
    # The starter skills are granted and equipped on first contact, so nobody
    # ever walks into a match with an empty skill bar.
    assert harness.skills.owned == STARTER_IDS
    assert set(harness.skills.slots) == STARTER_IDS


async def test_the_reported_level_is_derived_not_the_stored_column() -> None:
    harness = Harness(profile=_profile(total_exp=1000, level=1))

    response = await harness.request("GET", "/profile")

    assert response.json()["level"] == 5


async def test_the_profile_requires_authentication() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/game/profile")

    assert response.status_code == 401


# --- benchmark exam ----------------------------------------------------------


async def test_a_level_past_the_first_cap_is_held_back() -> None:
    cap = LEVEL_CAPS[0]
    harness = Harness(profile=_profile(total_exp=exp_for_level(cap + 5)))

    body = (await harness.request("GET", "/profile")).json()

    assert body["level"] == cap
    assert body["pending_benchmark_level"] == cap


async def test_nothing_is_pending_before_experience_reaches_the_cap() -> None:
    cap = LEVEL_CAPS[0]
    harness = Harness(profile=_profile(total_exp=exp_for_level(cap - 1)))

    body = (await harness.request("GET", "/profile")).json()

    assert body["level"] == cap - 1
    assert body["pending_benchmark_level"] is None


async def test_the_old_verdict_endpoint_is_gone() -> None:
    # A pass used to be posted straight from the client. It must not be
    # reachable any more: a cap is only ever cleared by a graded sitting.
    cap = LEVEL_CAPS[0]
    harness = Harness(profile=_profile(total_exp=exp_for_level(cap + 5)))

    response = await harness.request("POST", "/benchmark-exam", {"cap_level": cap})

    assert response.status_code == 404
    assert harness.profiles.profile.benchmark_cleared_level == 0


async def test_clearing_a_cap_releases_the_level() -> None:
    cap = LEVEL_CAPS[0]
    harness = Harness(profile=_profile(total_exp=exp_for_level(cap + 5)))

    profile = await harness.service().clear_benchmark_cap("user-1", cap)

    assert profile.level == cap + 5
    assert profile.pending_benchmark_level is None


async def test_clearing_an_unknown_cap_level_is_refused() -> None:
    harness = Harness(profile=_profile(total_exp=exp_for_level(99)))

    with pytest.raises(BenchmarkExamNotEligibleError):
        await harness.service().clear_benchmark_cap("user-1", 11)


async def test_clearing_a_cap_again_is_a_no_op_not_an_error() -> None:
    cap = LEVEL_CAPS[0]
    harness = Harness(
        profile=_profile(total_exp=exp_for_level(cap + 5), benchmark_cleared_level=cap)
    )

    profile = await harness.service().clear_benchmark_cap("user-1", cap)

    assert profile.level == cap + 5


async def test_clearing_a_later_cap_also_clears_every_cap_below_it() -> None:
    # Sitting the level-25 Benchmark Exam implies the level-10 one is behind
    # the player too -- `benchmark_cleared_level` is a single high-water mark,
    # not a set of individually-held caps.
    second_cap = LEVEL_CAPS[1]
    harness = Harness(profile=_profile(total_exp=exp_for_level(second_cap + 3)))

    profile = await harness.service().clear_benchmark_cap("user-1", second_cap)

    assert profile.level == second_cap + 3
    assert harness.profiles.profile.benchmark_cleared_level == second_cap


# --- classes ---------------------------------------------------------------


async def test_classes_are_listed_with_the_current_one_marked() -> None:
    harness = Harness(profile=_profile(class_code=WARRIOR))

    body = (await harness.request("GET", "/classes")).json()

    assert {row["code"] for row in body} == {spec.code for spec in CLASSES}
    assert [row["code"] for row in body if row["is_current"]] == [WARRIOR]


async def test_the_first_class_is_free() -> None:
    harness = Harness(profile=_profile(gold=0))

    response = await harness.request("POST", "/class", {"class_code": WARRIOR})

    assert response.status_code == 200
    assert response.json()["class_code"] == WARRIOR
    assert response.json()["gold"] == 0
    assert harness.ledger.rows == []


async def test_changing_class_costs_gold_and_clears_the_loadout() -> None:
    harness = Harness(profile=_profile(gold=800, class_code=WARRIOR))
    harness.skills.slots = {"skill-STRIKE_X2": 0}

    response = await harness.request("POST", "/class", {"class_code": "MAGE"})

    assert response.status_code == 200
    assert response.json()["class_code"] == "MAGE"
    assert response.json()["gold"] == 800 - CLASS_CHANGE_GOLD
    assert harness.skills.slots == {}
    # Unlocked skills survive a class change; only the equipped bar is cleared.
    assert harness.ledger.rows[0][1] == GoldReason.CLASS_CHANGE


async def test_changing_class_without_the_gold_is_refused() -> None:
    harness = Harness(profile=_profile(gold=10, class_code=WARRIOR))

    response = await harness.request("POST", "/class", {"class_code": "MAGE"})

    assert response.status_code == 400
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.class_code == WARRIOR


async def test_reselecting_the_same_class_is_free_and_keeps_the_loadout() -> None:
    harness = Harness(profile=_profile(gold=800, class_code=WARRIOR))
    harness.skills.slots = {"skill-STRIKE_X2": 0}

    response = await harness.request("POST", "/class", {"class_code": WARRIOR})

    assert response.status_code == 200
    assert response.json()["gold"] == 800
    assert harness.skills.slots == {"skill-STRIKE_X2": 0}


async def test_an_unknown_class_is_a_404() -> None:
    harness = Harness(profile=_profile())

    response = await harness.request("POST", "/class", {"class_code": "NECROMANCER"})

    assert response.status_code == 404


# --- skill tree ------------------------------------------------------------


async def test_the_tree_marks_other_classes_skills_as_locked() -> None:
    harness = Harness(profile=_profile(total_exp=5000, gold=9999, class_code=WARRIOR))

    body = (await harness.request("GET", "/skills")).json()

    assert _node(body, "WAR_GUARD")["locked_reason"] is None
    assert _node(body, "MAGE_BOLT")["locked_reason"] == "WRONG_CLASS"
    assert _node(body, "ASSA_FOCUS")["locked_reason"] == "WRONG_CLASS"


async def test_the_tree_reports_the_first_failed_condition() -> None:
    # Level 1, no gold: the root needs a level before it needs gold.
    harness = Harness(profile=_profile(total_exp=0, gold=0, class_code=WARRIOR))

    body = (await harness.request("GET", "/skills")).json()

    assert _node(body, "WAR_GUARD")["locked_reason"] == "NEEDS_LEVEL"
    # And the node below it is gated by its parent regardless of level.
    assert _node(body, "WAR_MEND")["locked_reason"] == "NEEDS_PARENT"


async def test_a_node_becomes_unlockable_once_its_parent_is_owned() -> None:
    harness = Harness(
        profile=_profile(total_exp=5000, gold=9999, class_code=WARRIOR),
        owned={"skill-WAR_GUARD"},
    )

    body = (await harness.request("GET", "/skills")).json()

    assert _node(body, "WAR_GUARD")["owned"] is True
    assert _node(body, "WAR_MEND")["unlockable"] is True


async def test_gold_is_the_last_condition_checked() -> None:
    harness = Harness(
        profile=_profile(total_exp=5000, gold=0, class_code=WARRIOR),
        owned={"skill-WAR_GUARD"},
    )

    body = (await harness.request("GET", "/skills")).json()

    assert _node(body, "WAR_MEND")["locked_reason"] == "NEEDS_GOLD"


async def test_an_ultimate_is_locked_until_its_unit_is_finished() -> None:
    harness = Harness(profile=_profile(total_exp=99999, gold=99999, class_code=WARRIOR))

    body = (await harness.request("GET", "/skills")).json()

    assert _node(body, ULTIMATES[0].code)["locked_reason"] == "NEEDS_UNIT"


async def test_finishing_the_unit_unlocks_the_ultimate_with_no_level_or_gold() -> None:
    harness = Harness(
        profile=_profile(total_exp=0, gold=0),
        completed_units={ULTIMATE_UNIT_ID},
    )

    body = (await harness.request("GET", "/skills")).json()

    node = _node(body, ULTIMATES[0].code)
    assert node["unlockable"] is True
    assert node["gold_price"] == 0


# --- unlocking -------------------------------------------------------------


async def test_unlocking_charges_gold_and_grants_the_skill() -> None:
    harness = Harness(profile=_profile(total_exp=5000, gold=500, class_code=WARRIOR))

    response = await harness.request("POST", "/skills/skill-WAR_GUARD/unlock")

    assert response.status_code == 200
    assert response.json()["owned"] is True
    assert "skill-WAR_GUARD" in harness.skills.owned
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.gold == 500 - 150
    assert harness.ledger.rows[0][1] == GoldReason.SKILL_UNLOCK


async def test_unlocking_without_enough_gold_is_a_400() -> None:
    harness = Harness(profile=_profile(total_exp=5000, gold=10, class_code=WARRIOR))

    response = await harness.request("POST", "/skills/skill-WAR_GUARD/unlock")

    assert response.status_code == 400
    # The free starters are owned by then; what must not have been granted is
    # the node that was refused.
    assert harness.skills.owned == STARTER_IDS


async def test_unlocking_a_skill_you_own_is_a_409() -> None:
    harness = Harness(
        profile=_profile(total_exp=5000, gold=9999, class_code=WARRIOR),
        owned={"skill-WAR_GUARD"},
    )

    response = await harness.request("POST", "/skills/skill-WAR_GUARD/unlock")

    assert response.status_code == 409


async def test_unlocking_a_node_whose_parent_is_missing_is_a_400() -> None:
    harness = Harness(profile=_profile(total_exp=99999, gold=9999, class_code=WARRIOR))

    response = await harness.request("POST", "/skills/skill-WAR_MEND/unlock")

    assert response.status_code == 400
    assert harness.skills.owned == STARTER_IDS


async def test_unlocking_an_ultimate_before_finishing_the_unit_is_a_400() -> None:
    harness = Harness(profile=_profile(total_exp=99999, gold=9999))

    response = await harness.request("POST", f"/skills/skill-{ULTIMATES[0].code}/unlock")

    assert response.status_code == 400


async def test_unlocking_an_ultimate_costs_nothing_and_still_grants_it() -> None:
    """The free path has no charge to carry its grant, so it commits on its own.

    Worth its own test because the paid path and the free one now persist by
    different routes: a paid unlock rides the balance write inside
    `_spend_gold`, a free one has no balance write at all.
    """
    ultimate = f"skill-{ULTIMATES[0].code}"
    harness = Harness(
        profile=_profile(total_exp=5000, gold=300, class_code=WARRIOR),
        completed_units={ULTIMATE_UNIT_ID},
    )

    response = await harness.request("POST", f"/skills/{ultimate}/unlock")

    assert response.status_code == 200
    assert response.json()["owned"] is True
    assert ultimate in harness.skills.owned
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.gold == 300
    assert harness.ledger.rows == []


async def test_unlocking_an_unknown_skill_is_a_404() -> None:
    harness = Harness(profile=_profile())

    response = await harness.request("POST", "/skills/skill-NOPE/unlock")

    assert response.status_code == 404


# --- loadout ---------------------------------------------------------------


async def test_setting_a_loadout_of_owned_skills_succeeds() -> None:
    harness = Harness(
        profile=_profile(class_code=WARRIOR),
        owned={"skill-STRIKE_X2", "skill-SHIELD", "skill-WAR_GUARD"},
    )

    response = await harness.request(
        "PUT",
        "/loadout",
        {"skill_ids": ["skill-WAR_GUARD", "skill-SHIELD", "skill-STRIKE_X2"]},
    )

    assert response.status_code == 200
    assert [slot["code"] for slot in response.json()["slots"]] == [
        "WAR_GUARD",
        "SHIELD",
        "STRIKE_X2",
    ]


async def test_equipping_a_skill_you_do_not_own_is_a_400() -> None:
    harness = Harness(profile=_profile(class_code=WARRIOR), owned={"skill-SHIELD"})

    response = await harness.request(
        "PUT", "/loadout", {"skill_ids": ["skill-WAR_GUARD"]}
    )

    assert response.status_code == 400


async def test_the_same_skill_cannot_fill_two_slots() -> None:
    harness = Harness(profile=_profile(class_code=WARRIOR), owned={"skill-SHIELD"})

    response = await harness.request(
        "PUT", "/loadout", {"skill_ids": ["skill-SHIELD", "skill-SHIELD"]}
    )

    assert response.status_code == 400


async def test_a_loadout_longer_than_the_slot_count_is_refused() -> None:
    harness = Harness(
        profile=_profile(class_code=WARRIOR),
        owned={"skill-STRIKE_X2", "skill-SHIELD", "skill-WAR_GUARD", "skill-WAR_MEND"},
    )

    response = await harness.request(
        "PUT",
        "/loadout",
        {
            "skill_ids": [
                "skill-STRIKE_X2",
                "skill-SHIELD",
                "skill-WAR_GUARD",
                "skill-WAR_MEND",
            ]
        },
    )

    assert response.status_code == 422


async def test_a_skill_from_another_class_cannot_be_equipped() -> None:
    # Owned from a previous life as a mage, but not usable as a warrior.
    harness = Harness(profile=_profile(class_code=WARRIOR), owned={"skill-MAGE_BOLT"})

    response = await harness.request("PUT", "/loadout", {"skill_ids": ["skill-MAGE_BOLT"]})

    assert response.status_code == 400


async def test_a_neutral_skill_is_equippable_by_every_class() -> None:
    harness = Harness(profile=_profile(class_code="MAGE"), owned={"skill-SHIELD"})

    response = await harness.request("PUT", "/loadout", {"skill_ids": ["skill-SHIELD"]})

    assert response.status_code == 200


# --- energy ----------------------------------------------------------------


async def test_the_profile_reports_the_energy_bar() -> None:
    harness = Harness(profile=_profile(energy=MAX_ENERGY))

    body = (await harness.request("GET", "/profile")).json()

    assert body["energy"]["current"] == MAX_ENERGY
    assert body["energy"]["maximum"] == MAX_ENERGY
    # A full bar is not accruing, so there is nothing to count down to.
    assert body["energy"]["next_regen_at"] is None


async def test_a_spent_bar_reports_when_the_next_point_lands() -> None:
    harness = Harness(profile=_profile(energy=1))

    body = (await harness.request("GET", "/profile")).json()

    assert body["energy"]["current"] == 1
    assert body["energy"]["next_regen_at"] is not None


async def test_the_profile_reports_the_streak() -> None:
    harness = Harness(profile=_profile(day_streak=12))

    body = (await harness.request("GET", "/profile")).json()

    assert body["day_streak"] == 12
    assert body["best_day_streak"] == 12


# --- inventory -------------------------------------------------------------


async def test_an_empty_inventory_reports_no_bonus() -> None:
    harness = Harness(profile=_profile())

    body = (await harness.request("GET", "/items")).json()

    assert body["items"] == []
    assert body["bonus_exp_permille"] == 0
    assert body["bonus_gold_permille"] == 0


async def test_owned_items_are_listed_with_their_counts() -> None:
    harness = Harness(
        profile=_profile(), owned_items={"item-WOODEN_SWORD": 2, "item-SKIN_NIGHT": 1}
    )

    body = (await harness.request("GET", "/items")).json()

    by_code = {row["code"]: row for row in body["items"]}
    assert by_code["WOODEN_SWORD"]["quantity"] == 2
    assert by_code["WOODEN_SWORD"]["equipped"] is False
    assert by_code["SKIN_NIGHT"]["slot"] is None


async def test_equipping_an_item_counts_toward_the_bonus() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-CHAIN_MAIL": 1})

    response = await harness.request("PUT", "/equipment", {"armor_id": "item-CHAIN_MAIL"})

    assert response.status_code == 200
    body = response.json()
    assert body["bonus_exp_permille"] == 45
    assert next(r for r in body["items"] if r["code"] == "CHAIN_MAIL")["equipped"] is True


async def test_the_reported_bonus_is_the_capped_one() -> None:
    harness = Harness(
        profile=_profile(),
        owned_items={"item-CHAIN_MAIL": 1, "item-DRAGON_PLATE": 1},
    )
    # Both are armour, so only the last one asked for actually goes on.
    await harness.request("PUT", "/equipment", {"armor_id": "item-DRAGON_PLATE"})

    body = (await harness.request("GET", "/items")).json()

    assert body["bonus_exp_permille"] <= 150
    assert body["bonus_gold_permille"] <= 150


async def test_equipping_an_item_you_do_not_own_is_a_400() -> None:
    harness = Harness(profile=_profile())

    response = await harness.request("PUT", "/equipment", {"armor_id": "item-CHAIN_MAIL"})

    assert response.status_code == 400


async def test_an_item_cannot_go_in_the_wrong_slot() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-CHAIN_MAIL": 1})

    response = await harness.request("PUT", "/equipment", {"weapon_id": "item-CHAIN_MAIL"})

    assert response.status_code == 400


async def test_a_cosmetic_item_cannot_be_equipped() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-SKIN_NIGHT": 1})

    response = await harness.request("PUT", "/equipment", {"armor_id": "item-SKIN_NIGHT"})

    assert response.status_code == 400


async def test_clearing_a_slot_removes_the_bonus() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-CHAIN_MAIL": 1})
    await harness.request("PUT", "/equipment", {"armor_id": "item-CHAIN_MAIL"})

    body = (await harness.request("PUT", "/equipment", {})).json()

    assert body["bonus_exp_permille"] == 0


# --- wearing a skin ---------------------------------------------------------


async def test_an_owned_skin_can_be_worn() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-SKIN_NIGHT": 1})

    response = await harness.request("PUT", "/skin", {"skin_code": "SKIN_NIGHT"})

    assert response.status_code == 200
    assert response.json()["skin_code"] == "SKIN_NIGHT"
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.skin_code == "SKIN_NIGHT"


async def test_a_null_code_takes_the_skin_off() -> None:
    harness = Harness(
        profile=_profile(skin_code="SKIN_NIGHT"), owned_items={"item-SKIN_NIGHT": 1}
    )

    body = (await harness.request("PUT", "/skin", {"skin_code": None})).json()

    assert body["skin_code"] is None


async def test_a_skin_that_is_not_owned_cannot_be_worn() -> None:
    harness = Harness(profile=_profile())

    response = await harness.request("PUT", "/skin", {"skin_code": "SKIN_NIGHT"})

    assert response.status_code == 400
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.skin_code is None


async def test_equipment_cannot_be_worn_as_a_skin() -> None:
    harness = Harness(profile=_profile(), owned_items={"item-CHAIN_MAIL": 1})

    response = await harness.request("PUT", "/skin", {"skin_code": "CHAIN_MAIL"})

    assert response.status_code == 400


async def test_the_inventory_reports_the_skin_on_show() -> None:
    harness = Harness(
        profile=_profile(skin_code="SKIN_SCHOLAR"), owned_items={"item-SKIN_SCHOLAR": 1}
    )

    body = (await harness.request("GET", "/items")).json()

    assert body["skin_code"] == "SKIN_SCHOLAR"


async def test_wearing_a_skin_moves_no_reward_bonus() -> None:
    """The whole premise of a cosmetic shop, asserted end to end."""
    harness = Harness(profile=_profile(), owned_items={"item-SKIN_LAUREATE": 1})

    body = (await harness.request("PUT", "/skin", {"skin_code": "SKIN_LAUREATE"})).json()

    assert body["bonus_exp_permille"] == 0
    assert body["bonus_gold_permille"] == 0


# --- shop ------------------------------------------------------------------


def test_nothing_on_sale_can_move_a_number_a_match_reads() -> None:
    """The shop's whole licence to exist: gold buys a look, never an edge.

    Asserted against the catalog rather than a response, so pricing an item
    that carries a buff fails here whoever adds it and whatever the API does.
    """
    for spec in ITEMS:
        if spec.gold_price > 0:
            assert spec.kind is ItemKind.SKIN, f"{spec.code} is priced but not a skin"
            assert spec.bonus_exp_permille == 0, f"{spec.code} sells an EXP buff"
            assert spec.bonus_gold_permille == 0, f"{spec.code} sells a Gold buff"


def test_an_item_is_either_bought_or_dropped_never_both() -> None:
    priced = {spec.code for spec in ITEMS if spec.gold_price > 0}
    droppable = {spec.code for spec in ITEMS if spec.gold_price == 0}

    assert priced and droppable
    assert not priced & droppable


async def test_the_shop_stocks_only_priced_items() -> None:
    harness = Harness(profile=_profile(gold=1000))

    body = (await harness.request("GET", "/shop")).json()

    assert body["items"]
    assert all(row["gold_price"] > 0 for row in body["items"])
    assert all(row["kind"] == "SKIN" for row in body["items"])


async def test_the_shop_reports_the_balance_and_what_is_already_owned() -> None:
    harness = Harness(profile=_profile(gold=300), owned_items={"item-SKIN_NIGHT": 1})

    body = (await harness.request("GET", "/shop")).json()

    assert body["gold"] == 300
    by_code = {row["code"]: row for row in body["items"]}
    assert by_code["SKIN_NIGHT"]["owned"] is True
    assert by_code["SKIN_SCHOLAR"]["owned"] is False


async def test_buying_a_skin_charges_it_and_hands_it_over() -> None:
    harness = Harness(profile=_profile(gold=1000))

    response = await harness.request("POST", "/shop/item-SKIN_SCHOLAR/purchase")

    assert response.status_code == 200
    body = response.json()
    assert body["gold"] == 1000 - SKIN_PRICE_RARE
    assert next(r for r in body["items"] if r["code"] == "SKIN_SCHOLAR")["owned"] is True
    assert harness.items.owned_counts["item-SKIN_SCHOLAR"] == 1


async def test_a_purchase_is_written_to_the_gold_ledger() -> None:
    harness = Harness(profile=_profile(gold=1000))

    await harness.request("POST", "/shop/item-SKIN_SCHOLAR/purchase")

    assert harness.ledger.rows == [
        ("user-1", GoldReason.ITEM_PURCHASE, "item-SKIN_SCHOLAR", -SKIN_PRICE_RARE)
    ]


async def test_buying_the_same_skin_twice_is_a_409_and_charges_once() -> None:
    harness = Harness(profile=_profile(gold=1000))
    await harness.request("POST", "/shop/item-SKIN_SCHOLAR/purchase")

    response = await harness.request("POST", "/shop/item-SKIN_SCHOLAR/purchase")

    assert response.status_code == 409
    assert harness.profiles.profile is not None
    assert harness.profiles.profile.gold == 1000 - SKIN_PRICE_RARE


async def test_buying_without_the_gold_is_refused_and_grants_nothing() -> None:
    harness = Harness(profile=_profile(gold=SKIN_PRICE_RARE - 1))

    response = await harness.request("POST", "/shop/item-SKIN_SCHOLAR/purchase")

    assert response.status_code == 400
    assert harness.items.owned_counts == {}


async def test_equipment_cannot_be_bought() -> None:
    harness = Harness(profile=_profile(gold=10_000))

    response = await harness.request("POST", "/shop/item-CHAIN_MAIL/purchase")

    assert response.status_code == 400
    assert harness.items.owned_counts == {}


async def test_buying_an_unknown_item_is_a_404() -> None:
    harness = Harness(profile=_profile(gold=1000))

    response = await harness.request("POST", "/shop/item-NOPE/purchase")

    assert response.status_code == 404


# --- season ----------------------------------------------------------------


async def test_the_current_season_is_reported_with_a_tier() -> None:
    harness = Harness(profile=_profile(), all_time_rating=1000)

    body = (await harness.request("GET", "/season/current")).json()

    assert body["code"] == "S202608"
    assert body["tier"] == "BRONZE"
    assert body["matches_played"] == 0


async def test_a_player_who_has_not_played_this_season_sees_their_opening_rating() -> None:
    # Carried part of the way down from a strong all-time rating.
    harness = Harness(profile=_profile(), all_time_rating=2000)

    body = (await harness.request("GET", "/season/current")).json()

    assert 1000 < body["rating"] < 2000
    assert body["tier"] in {"GOLD", "PLATINUM", "DIAMOND"}


async def test_the_season_reports_the_climb_to_the_next_tier() -> None:
    harness = Harness(profile=_profile(), all_time_rating=1000)

    body = (await harness.request("GET", "/season/current")).json()

    assert body["next_tier"] == "SILVER"
    assert body["rating_to_next_tier"] == 1100 - body["rating"]


async def test_the_top_tier_has_nothing_above_it() -> None:
    harness = Harness(profile=_profile(), all_time_rating=5000)

    body = (await harness.request("GET", "/season/current")).json()

    assert body["tier"] == "MASTER"
    assert body["next_tier"] is None
    assert body["rating_to_next_tier"] is None
