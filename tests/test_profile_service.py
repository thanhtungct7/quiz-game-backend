"""The aggregator: what a profile reads when the rows behind it are missing,
empty or full."""

from datetime import UTC, datetime

import pytest

from app.core.config import settings
from app.core.exceptions import UserNotFoundError
from app.models.auth.user import User
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.models.game.achievement import (
    Achievement,
    AchievementCategory,
    AchievementMetric,
    UserAchievement,
)
from app.models.game.game_class import GameClass
from app.models.game.game_item import EquipmentSlot, GameItem, ItemKind, ItemRarity
from app.models.game.user_game_profile import UserGameProfile
from app.repository.profile.profile_stats_repository import LearningTotals
from app.services.game.achievements import AchievementMetrics
from app.services.game.cefr import CefrBand, cefr_floor
from app.services.game.combat import MAX_DAMAGE, MAX_HP, streak_buff
from app.services.game.combat_stats import StatSourceKind, attack_for
from app.services.game.energy import MAX_ENERGY
from app.services.game.leveling import exp_for_level
from app.services.game.season import RankTier
from app.services.profile.profile_service import ProfileService


def _make_user(**overrides: object) -> User:
    user = User(
        id="user-1",
        email="player@example.com",
        username="Player",
        bio="Hello",
        is_active=True,
    )
    user.created_at = datetime.now(UTC)
    for name, value in overrides.items():
        setattr(user, name, value)
    return user


def _make_profile(**overrides: object) -> UserGameProfile:
    profile = UserGameProfile(user_id="user-1")
    profile.total_exp = 0
    profile.gold = 0
    profile.class_code = None
    profile.day_streak = 0
    profile.best_day_streak = 0
    profile.energy = MAX_ENERGY
    profile.energy_updated_at = datetime.now(UTC)
    for name, value in overrides.items():
        setattr(profile, name, value)
    return profile


def _make_class(**overrides: object) -> GameClass:
    row = GameClass(
        id="class-1",
        code="WARRIOR",
        name="Chiến binh",
        description="",
        max_hp=115,
        damage_permille=900,
        starting_mana=10,
        defence=3,
        sort_order=1,
        is_active=True,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _make_item(**overrides: object) -> GameItem:
    row = GameItem(
        id="item-1",
        code="CHAIN_MAIL",
        name="Giáp xích",
        kind=ItemKind.EQUIPMENT,
        slot=EquipmentSlot.ARMOR,
        rarity=ItemRarity.RARE,
        bonus_max_hp=8,
        bonus_damage_permille=0,
        bonus_starting_mana=0,
        bonus_defence=1,
        is_active=True,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _make_achievement(code: str = "LEVEL_5", **overrides: object) -> Achievement:
    row = Achievement(
        id=f"ach-{code}",
        code=code,
        name=code.title(),
        description="",
        category=AchievementCategory.PROGRESSION,
        metric=AchievementMetric.LEVEL,
        threshold=5,
        icon_code="LEVEL",
        sort_order=0,
        is_hidden=False,
        is_active=True,
    )
    for name, value in overrides.items():
        setattr(row, name, value)
    return row


def _held(*codes: str) -> list[tuple[UserAchievement, Achievement]]:
    """Newest first, the way the repository orders them."""
    return [
        (
            UserAchievement(
                id=f"held-{code}",
                user_id="user-1",
                achievement_id=f"ach-{code}",
                unlocked_at=datetime.now(UTC),
            ),
            _make_achievement(code),
        )
        for code in codes
    ]


def _make_rating(**overrides: object) -> DuoRating:
    rating = DuoRating(user_id="user-1")
    rating.rating = DEFAULT_RATING
    rating.matches_played = 0
    rating.wins = 0
    rating.losses = 0
    rating.draws = 0
    for name, value in overrides.items():
        setattr(rating, name, value)
    return rating


class FakeStatsRepository:
    def __init__(
        self,
        found: tuple[User, UserGameProfile | None, DuoRating | None] | None,
        totals: LearningTotals = (0, 0, 0),
    ) -> None:
        self.found = found
        self.totals = totals
        self.calls: list[str] = []

    async def identity_with_standing(
        self, user_id: str
    ) -> tuple[User, UserGameProfile | None, DuoRating | None] | None:
        self.calls.append("identity")
        return self.found

    async def learning_totals(self, user_id: str) -> LearningTotals:
        self.calls.append("learning")
        return self.totals


class FakeAchievementService:
    def __init__(
        self,
        calls: list[str],
        held: list[tuple[UserAchievement, Achievement]] | None = None,
        catalog: list[Achievement] | None = None,
    ) -> None:
        self.calls = calls
        self.held = held or []
        # Kept apart from `held` on purpose: the interesting cases are the ones
        # where the catalog offers more than the player holds.
        self.catalog_rows = catalog if catalog is not None else [row for _u, row in self.held]
        # Recorded rather than counted: a profile card that synced would be a
        # write on a read path, and that is worth failing a test over.
        self.syncs: list[str] = []

    async def unlocked(self, user_id: str) -> list[tuple[UserAchievement, Achievement]]:
        self.calls.append("achievements")
        return self.held

    async def sync(self, user_id: str) -> list[Achievement]:
        self.syncs.append(user_id)
        return []

    async def catalog(self) -> list[Achievement]:
        return self.catalog_rows

    async def metrics(self, user_id: str) -> AchievementMetrics:
        return AchievementMetrics(level=99)


class FakeCatalogRepository:
    """Shares the stats repository's call log, so the round-trip tests below can
    count every query a profile makes rather than only two thirds of them."""

    def __init__(self, calls: list[str], class_row: GameClass | None) -> None:
        self.calls = calls
        self.class_row = class_row

    async def get_class(self, code: str) -> GameClass | None:
        self.calls.append("class")
        return self.class_row


class FakeItemRepository:
    def __init__(self, calls: list[str], equipment: list[GameItem]) -> None:
        self.calls = calls
        self.equipment_rows = equipment

    async def equipment(self, user_id: str) -> list[tuple[object, GameItem]]:
        self.calls.append("equipment")
        return [(None, item) for item in self.equipment_rows]


def _service(
    repository: FakeStatsRepository,
    *,
    class_row: GameClass | None = None,
    equipment: list[GameItem] | None = None,
    held: list[tuple[UserAchievement, Achievement]] | None = None,
    achievement_catalog: list[Achievement] | None = None,
) -> ProfileService:
    return ProfileService(  # type: ignore[arg-type]
        stats=repository,
        catalog=FakeCatalogRepository(repository.calls, class_row),
        items=FakeItemRepository(repository.calls, equipment or []),
        achievements=FakeAchievementService(repository.calls, held, achievement_catalog),
        config=settings,
    )


# --- the missing rows ------------------------------------------------------


async def test_an_unknown_user_is_an_error_not_an_empty_card() -> None:
    service = _service(FakeStatsRepository(found=None))

    with pytest.raises(UserNotFoundError):
        await service.get_public("nobody")


async def test_an_account_older_than_the_game_layer_still_reads() -> None:
    """No game profile and no rating row: the two are created lazily, so this
    is an ordinary new account and not a hole in the leaderboard."""
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_public("user-1")

    assert card.level == 1
    assert card.cefr is CefrBand.A1
    assert card.class_code is None
    assert card.day_streak == 0
    assert card.pvp.rating == DEFAULT_RATING
    assert card.pvp.matches_played == 0
    assert card.pvp.win_rate == 0.0


async def test_a_player_with_no_profile_row_has_a_full_energy_bar() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_self("user-1")

    assert card.energy.current == MAX_ENERGY
    assert card.energy.next_regen_at is None
    assert card.gold == 0
    assert card.total_exp == 0


# --- what is derived rather than read --------------------------------------


async def test_level_is_recomputed_from_experience_not_read_from_the_column() -> None:
    # The cached column is deliberately wrong; the card must ignore it.
    profile = _make_profile(total_exp=exp_for_level(12), level=99)
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_public("user-1")

    assert card.level == 12


async def test_the_band_and_the_estimate_follow_the_level() -> None:
    profile = _make_profile(total_exp=exp_for_level(cefr_floor(CefrBand.B1)))
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_public("user-1")

    assert card.cefr is CefrBand.B1
    assert 0 < card.toeic_estimate <= 990


async def test_the_next_band_runs_out_at_the_top() -> None:
    profile = _make_profile(total_exp=exp_for_level(cefr_floor(CefrBand.C2)))
    service = _service(FakeStatsRepository(found=(_make_user(), profile, None)))

    card = await service.get_self("user-1")

    assert card.cefr is CefrBand.C2
    assert card.next_cefr is None
    assert card.next_cefr_at_level is None


async def test_tier_is_derived_from_rating() -> None:
    rating = _make_rating(rating=1950, matches_played=10, wins=7, losses=3)
    service = _service(FakeStatsRepository(found=(_make_user(), None, rating)))

    card = await service.get_public("user-1")

    assert card.pvp.tier is RankTier.MASTER
    assert card.pvp.win_rate == 70.0


# --- learning stats --------------------------------------------------------


async def test_accuracy_is_answers_that_landed_over_answers_given() -> None:
    service = _service(
        FakeStatsRepository(found=(_make_user(), None, None), totals=(40, 30, 60))
    )

    card = await service.get_public("user-1")

    assert card.learning.challenges_attempted == 40
    assert card.learning.challenges_mastered == 30
    assert card.learning.total_attempts == 60
    assert card.learning.accuracy == 50.0


async def test_a_player_who_has_answered_nothing_does_not_divide_by_zero() -> None:
    service = _service(
        FakeStatsRepository(found=(_make_user(), None, None), totals=(0, 0, 0))
    )

    card = await service.get_public("user-1")

    assert card.learning.accuracy == 0.0


# --- the round trips -------------------------------------------------------


async def test_a_full_profile_costs_exactly_five_queries() -> None:
    """The public card has a 100ms budget. If this starts failing, something
    began fetching per-field.

    Five is the ceiling and this is the shape that reaches it: identity and
    standing together, the study totals, the class row, the equipped items and
    the unlocked achievements. Note what is *not* in the list -- the skill bar.
    A card does not draw it, and `LoadoutBuilder` would have loaded it and
    granted starters on the way past, which is a write on a read path.
    """
    repository = FakeStatsRepository(
        found=(_make_user(), _make_profile(class_code="WARRIOR"), None)
    )
    service = _service(repository, class_row=_make_class())

    await service.get_public("user-1")

    assert repository.calls == [
        "identity",
        "learning",
        "class",
        "equipment",
        "achievements",
    ]


async def test_a_player_with_no_class_is_not_charged_for_the_class_row() -> None:
    repository = FakeStatsRepository(found=(_make_user(), _make_profile(), None))
    service = _service(repository)

    await service.get_public("user-1")

    assert repository.calls == ["identity", "learning", "equipment", "achievements"]


async def test_an_account_older_than_the_game_layer_costs_no_game_queries() -> None:
    """No game profile means no class and no equipment to hang on it, so those
    two are skipped rather than answered with nothing."""
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    service = _service(repository)

    await service.get_public("user-1")

    assert repository.calls == ["identity", "learning", "achievements"]


async def test_reading_a_profile_never_syncs_achievements() -> None:
    """A card is a pure read. Syncing belongs to the paths that could have moved
    a counter -- settling a match, finishing a lesson, opening the achievement
    tab -- and never to the thing a leaderboard fetches once per row.
    """
    repository = FakeStatsRepository(found=(_make_user(), _make_profile(), None))
    service = _service(repository)

    await service.get_public("user-1")
    await service.get_self("user-1")

    assert service.achievements.syncs == []  # type: ignore[attr-defined]


# --- the resolved stat block -----------------------------------------------


async def test_a_player_with_no_class_fights_on_the_baseline() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    combat = (await service.get_public("user-1")).combat

    assert combat.hp == MAX_HP
    assert combat.atk == MAX_DAMAGE
    assert combat.defence == 0


async def test_the_card_adds_up_class_armour_and_streak() -> None:
    """The whole point of the block: what a match would really use, not a base
    the client has to assemble out of three endpoints."""
    repository = FakeStatsRepository(
        found=(_make_user(), _make_profile(class_code="WARRIOR", day_streak=10), None)
    )
    service = _service(repository, class_row=_make_class(), equipment=[_make_item()])

    combat = (await service.get_public("user-1")).combat

    buff = streak_buff(10)
    assert combat.hp == 115 + 8 + buff.bonus_max_hp
    assert combat.defence == 3 + 1
    assert combat.mana == 10 + buff.bonus_starting_mana
    assert combat.atk == attack_for(900)
    assert combat.damage_permille == 900


async def test_a_retired_item_is_not_worn() -> None:
    """Same rule the loadout builder follows: an item pulled from the catalog
    stays in the table until the player edits their gear, and must stop
    counting the moment it is retired."""
    repository = FakeStatsRepository(
        found=(_make_user(), _make_profile(class_code="WARRIOR"), None)
    )
    service = _service(
        repository, class_row=_make_class(), equipment=[_make_item(is_active=False)]
    )

    combat = (await service.get_public("user-1")).combat

    assert combat.defence == 3


async def test_the_breakdown_adds_up_to_the_totals() -> None:
    """The invariant the modal depends on. Every line is a delta, and if they
    stopped summing to the total the screen would be quietly wrong rather than
    visibly broken."""
    repository = FakeStatsRepository(
        found=(_make_user(), _make_profile(class_code="WARRIOR", day_streak=10), None)
    )
    service = _service(repository, class_row=_make_class(), equipment=[_make_item()])

    breakdown = await service.get_combat_breakdown("user-1")

    assert sum(source.hp for source in breakdown.sources) == breakdown.total.hp
    assert sum(source.atk for source in breakdown.sources) == breakdown.total.atk
    assert sum(source.defence for source in breakdown.sources) == breakdown.total.defence
    assert sum(source.mana for source in breakdown.sources) == breakdown.total.mana
    assert [source.kind for source in breakdown.sources] == [
        StatSourceKind.CLASS,
        StatSourceKind.EQUIPMENT,
        StatSourceKind.STREAK,
    ]


async def test_the_breakdown_of_an_unknown_player_is_an_error() -> None:
    service = _service(FakeStatsRepository(found=None))

    with pytest.raises(UserNotFoundError):
        await service.get_combat_breakdown("nobody")


# --- the private half stays private ----------------------------------------


async def test_the_public_card_carries_no_private_field() -> None:
    """The whole reason `PublicProfileRead` and `SelfProfileRead` are separate
    types. A field added to the wrong one is a leak, so this is asserted on the
    serialised payload rather than on the class."""
    user = _make_user(email="secret@example.com", avatar_file_id="drive-file-id")
    service = _service(FakeStatsRepository(found=(user, _make_profile(gold=999), None)))

    payload = (await service.get_public("user-1")).model_dump()

    assert "email" not in payload
    assert "gold" not in payload
    assert "energy" not in payload
    assert "has_uploaded_avatar" not in payload
    assert "secret@example.com" not in str(payload)


async def test_the_self_card_carries_the_private_half() -> None:
    user = _make_user(email="me@example.com")
    service = _service(FakeStatsRepository(found=(user, _make_profile(gold=42), None)))

    card = await service.get_self("user-1")

    assert card.email == "me@example.com"
    assert card.gold == 42


# --- placeholders for modules that do not exist yet ------------------------


async def test_the_character_fields_answer_empty_for_now() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_public("user-1")

    assert card.title is None
    assert card.companion_character is None
    assert card.skin_code is None


# --- achievements on the card ----------------------------------------------


async def test_a_card_carries_the_three_most_recent_badges_and_the_full_count() -> None:
    """The overview tab has room for three. Sending all of them would make a
    leaderboard row carry a shelf it has no space to draw."""
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    service = _service(
        repository, held=_held("E", "D", "C", "B", "A")
    )

    card = await service.get_public("user-1")

    assert [badge.code for badge in card.featured_achievements] == ["E", "D", "C"]
    assert card.total_achievements_unlocked == 5


async def test_a_player_with_no_badges_gets_an_empty_shelf_not_a_missing_one() -> None:
    service = _service(FakeStatsRepository(found=(_make_user(), None, None)))

    card = await service.get_public("user-1")

    assert card.featured_achievements == []
    assert card.total_achievements_unlocked == 0


async def test_opening_the_achievement_tab_syncs_first() -> None:
    """The one read that does. It is what lets an account older than an
    achievement pick it up without going and earning it again."""
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    service = _service(repository, held=_held("LEVEL_5"))

    listing = await service.get_achievements("user-1")

    assert service.achievements.syncs == ["user-1"]  # type: ignore[attr-defined]
    assert listing.unlocked_count == 1
    assert listing.total == 1
    assert listing.items[0].unlocked is True


async def test_progress_on_a_locked_badge_is_clamped_to_its_threshold() -> None:
    """A bar that reads 99/5 looks like a bug. The fake reads level 99, and the
    badge below asks for 5."""
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    service = _service(
        repository, held=[], achievement_catalog=[_make_achievement("LEVEL_5", threshold=5)]
    )

    listing = await service.get_achievements("user-1")

    assert listing.unlocked_count == 0
    assert listing.items[0].current == 5
    assert listing.items[0].threshold == 5
    assert listing.items[0].unlocked_at is None


async def test_a_hidden_badge_stays_hidden_until_it_is_earned() -> None:
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    service = _service(
        repository,
        held=[],
        achievement_catalog=[
            _make_achievement("OPEN"),
            _make_achievement("SECRET", is_hidden=True),
        ],
    )

    listing = await service.get_achievements("user-1")

    assert [item.code for item in listing.items] == ["OPEN"]


async def test_a_hidden_badge_appears_once_it_is_earned() -> None:
    repository = FakeStatsRepository(found=(_make_user(), None, None))
    secret = _make_achievement("SECRET", is_hidden=True)
    service = _service(
        repository, held=_held("SECRET"), achievement_catalog=[secret]
    )

    listing = await service.get_achievements("user-1")

    assert [item.code for item in listing.items] == ["SECRET"]
    assert listing.items[0].unlocked is True
