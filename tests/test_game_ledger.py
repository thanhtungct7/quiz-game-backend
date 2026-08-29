from datetime import UTC, date, datetime
from random import Random

from app.models.duo.duo_match import DuoMatchEndReason
from app.models.game.game_item import EquipmentSlot, GameItem, ItemKind, ItemRarity
from app.models.game.gold_transaction import GoldReason
from app.models.game.season import GameSeason, SeasonRating
from app.models.game.user_game_profile import UserGameProfile
from app.services.duo.scoring import MatchOutcome
from app.services.game.rewards import RewardInput
from app.services.game.season import RankTier
from app.services.game.settlement import GameSettlementService

MATCH_ID = "match-1"
WINNER = "user-win"
LOSER = "user-lose"


class FakeProfileRepository:
    """In-memory stand-in. Column defaults only apply on INSERT, so the model
    is constructed with them spelled out."""

    def __init__(self) -> None:
        self.rows: dict[str, UserGameProfile] = {}
        self.saves = 0

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        if user_id not in self.rows:
            self.rows[user_id] = UserGameProfile(
                id=f"profile-{user_id}",
                user_id=user_id,
                total_exp=0,
                level=1,
                gold=0,
                energy=5,
                energy_updated_at=datetime.now(UTC),
                day_streak=0,
                best_day_streak=0,
                last_active_date=None,
            )
        return self.rows[user_id]

    async def save(
        self, profile: UserGameProfile, data: dict[str, object]
    ) -> UserGameProfile:
        self.saves += 1
        for field, value in data.items():
            setattr(profile, field, value)
        return profile


class FakeGoldLedger:
    """Enforces the same unique (user_id, reason, ref_id) the table does."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, GoldReason, str, int, int]] = []

    async def grant(
        self,
        *,
        user_id: str,
        amount: int,
        reason: GoldReason,
        ref_id: str,
        balance_after: int,
    ) -> bool:
        key = (user_id, reason, ref_id)
        if any((row[0], row[1], row[2]) == key for row in self.rows):
            return False
        self.rows.append((user_id, reason, ref_id, amount, balance_after))
        return True


def _service() -> tuple[GameSettlementService, FakeProfileRepository, FakeGoldLedger]:
    profiles = FakeProfileRepository()
    ledger = FakeGoldLedger()
    service = GameSettlementService(profiles=profiles, ledger=ledger)  # type: ignore[arg-type]
    return service, profiles, ledger


def _rewards(
    *, end_reason: DuoMatchEndReason = DuoMatchEndReason.COMPLETED
) -> dict[str, RewardInput]:
    return {
        WINNER: RewardInput(
            outcome=MatchOutcome.WIN,
            end_reason=end_reason,
            forfeited=False,
            correct_count=6,
        ),
        LOSER: RewardInput(
            outcome=MatchOutcome.LOSE,
            end_reason=end_reason,
            forfeited=False,
            correct_count=2,
        ),
    }


async def test_settling_a_match_pays_both_players() -> None:
    service, profiles, ledger = _service()

    settlements = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert settlements[WINNER].exp.delta == 50 + 5 * 6
    assert settlements[WINNER].gold.delta == 25 + 2 * 6
    assert settlements[LOSER].exp.delta == 15
    assert settlements[LOSER].gold.delta == 8
    assert profiles.rows[WINNER].gold == 25 + 2 * 6
    assert profiles.rows[LOSER].gold == 8
    assert len(ledger.rows) == 2


async def test_settling_the_same_match_twice_pays_only_once() -> None:
    service, profiles, ledger = _service()

    first = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())
    gold_after_first = {user_id: profiles.rows[user_id].gold for user_id in profiles.rows}
    exp_after_first = {user_id: profiles.rows[user_id].total_exp for user_id in profiles.rows}

    second = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    # One ledger row per player, no matter how many times the settle runs.
    assert len(ledger.rows) == 2
    for user_id in (WINNER, LOSER):
        assert profiles.rows[user_id].gold == gold_after_first[user_id]
        assert profiles.rows[user_id].total_exp == exp_after_first[user_id]
        # The replay reports no movement rather than repeating the first payout.
        assert second[user_id].gold.delta == 0
        assert second[user_id].exp.delta == 0
        assert second[user_id].gold.after == first[user_id].gold.after
        assert second[user_id].exp.after == first[user_id].exp.after


async def test_a_replayed_settle_never_writes_the_profile() -> None:
    service, profiles, _ = _service()

    await service.settle_match(match_id=MATCH_ID, rewards=_rewards())
    saves_after_first = profiles.saves
    await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert profiles.saves == saves_after_first


async def test_two_different_matches_both_pay() -> None:
    service, profiles, ledger = _service()

    await service.settle_match(match_id="match-1", rewards=_rewards())
    await service.settle_match(match_id="match-2", rewards=_rewards())

    assert len(ledger.rows) == 4
    assert profiles.rows[WINNER].gold == 2 * (25 + 2 * 6)


async def test_a_forfeit_costs_experience_but_never_gold() -> None:
    service, profiles, ledger = _service()
    rewards = {
        LOSER: RewardInput(
            outcome=MatchOutcome.LOSE,
            end_reason=DuoMatchEndReason.OPPONENT_LEFT,
            forfeited=True,
            correct_count=3,
        )
    }
    # Start with something to lose.
    profile = await profiles.get_or_create(LOSER)
    profile.total_exp = 350
    profile.level = 3
    profile.gold = 100

    settlement = (await service.settle_match(match_id=MATCH_ID, rewards=rewards))[LOSER]

    assert settlement.exp.delta == -30
    assert settlement.gold.delta == 0
    assert profiles.rows[LOSER].gold == 100
    # Still logged, at zero, so the replay guard covers forfeits too.
    assert len(ledger.rows) == 1
    assert ledger.rows[0][3] == 0


async def test_a_forfeit_penalty_cannot_cost_a_level() -> None:
    service, profiles, _ = _service()
    profile = await profiles.get_or_create(LOSER)
    profile.total_exp = 310  # level 3 starts at 300
    profile.level = 3

    await service.settle_match(
        match_id=MATCH_ID,
        rewards={
            LOSER: RewardInput(
                outcome=MatchOutcome.LOSE,
                end_reason=DuoMatchEndReason.OPPONENT_LEFT,
                forfeited=True,
                correct_count=0,
            )
        },
    )

    assert profiles.rows[LOSER].total_exp == 300
    assert profiles.rows[LOSER].level == 3


async def test_gold_never_goes_negative() -> None:
    service, profiles, _ = _service()
    await profiles.get_or_create(WINNER)

    await service.settle_match(
        match_id=MATCH_ID,
        rewards={
            WINNER: RewardInput(
                outcome=MatchOutcome.WIN,
                end_reason=DuoMatchEndReason.COMPLETED,
                forfeited=False,
                correct_count=0,
            )
        },
    )

    assert profiles.rows[WINNER].gold >= 0


async def test_a_level_up_is_reported() -> None:
    service, _, _ = _service()

    # A win with 10 correct is 100 exp, which is exactly level 2.
    settlement = (
        await service.settle_match(
            match_id=MATCH_ID,
            rewards={
                WINNER: RewardInput(
                    outcome=MatchOutcome.WIN,
                    end_reason=DuoMatchEndReason.COMPLETED,
                    forfeited=False,
                    correct_count=10,
                )
            },
        )
    )[WINNER]

    assert settlement.exp.after == 100
    assert settlement.exp.level_before == 1
    assert settlement.exp.level_after == 2
    assert settlement.exp.leveled_up is True


# --- loot, streak and season ------------------------------------------------


class FakeItemRepository:
    def __init__(self, items: list[GameItem] | None = None) -> None:
        self.items = items if items is not None else [_item("SWORD"), _item("SHIELD")]
        self.claims: list[tuple[str, str, str | None]] = []
        self.inventory: list[tuple[str, str]] = []

    async def list_items(self) -> list[GameItem]:
        return self.items

    async def claim_loot(self, user_id: str, ref_id: str, item_id: str | None) -> bool:
        if any(claim[:2] == (user_id, ref_id) for claim in self.claims):
            return False
        self.claims.append((user_id, ref_id, item_id))
        return True

    async def add_to_inventory(self, user_id: str, item_id: str) -> None:
        self.inventory.append((user_id, item_id))


class FakeSeasonRepository:
    def __init__(self) -> None:
        self.season = GameSeason(
            id="season-1",
            code="S202608",
            name="Mùa 08/2026",
            starts_at=datetime(2026, 8, 1, tzinfo=UTC),
            ends_at=datetime(2026, 9, 30, tzinfo=UTC),
            is_active=True,
        )
        self.ratings: dict[str, SeasonRating] = {}

    async def active(self) -> GameSeason:
        return self.season

    async def rating(self, season_id: str, user_id: str) -> SeasonRating | None:
        return self.ratings.get(user_id)

    async def get_or_create_rating(
        self, season_id: str, user_id: str, *, opening_rating: int
    ) -> SeasonRating:
        if user_id not in self.ratings:
            self.ratings[user_id] = SeasonRating(
                id=f"sr-{user_id}",
                season_id=season_id,
                user_id=user_id,
                rating=opening_rating,
                peak_rating=opening_rating,
                matches_played=0,
                wins=0,
                losses=0,
                draws=0,
            )
        return self.ratings[user_id]

    async def save_rating(
        self, record: SeasonRating, data: dict[str, object]
    ) -> SeasonRating:
        for field, value in data.items():
            setattr(record, field, value)
        return record


class FakeActivityRepository:
    def __init__(self) -> None:
        self.rows: list[tuple[str, date, int, int]] = []

    async def record(
        self, user_id: str, day: date, *, lessons: int = 0, matches: int = 0
    ) -> None:
        self.rows.append((user_id, day, lessons, matches))


def _item(code: str) -> GameItem:
    return GameItem(
        id=f"item-{code}",
        code=code,
        name=code.title(),
        kind=ItemKind.EQUIPMENT,
        slot=EquipmentSlot.WEAPON,
        rarity=ItemRarity.COMMON,
        bonus_max_hp=0,
        bonus_damage_permille=10,
        bonus_starting_mana=0,
        is_active=True,
    )


def _full_service(
    *, items: FakeItemRepository | None = None
) -> tuple[
    GameSettlementService,
    FakeProfileRepository,
    FakeItemRepository,
    FakeSeasonRepository,
    FakeActivityRepository,
]:
    profiles = FakeProfileRepository()
    ledger = FakeGoldLedger()
    item_repo = items or FakeItemRepository()
    seasons = FakeSeasonRepository()
    activity = FakeActivityRepository()
    service = GameSettlementService(
        profiles=profiles,  # type: ignore[arg-type]
        ledger=ledger,  # type: ignore[arg-type]
        items=item_repo,  # type: ignore[arg-type]
        seasons=seasons,  # type: ignore[arg-type]
        activity=activity,  # type: ignore[arg-type]
        rng=Random(20260829),  # noqa: S311
    )
    return service, profiles, item_repo, seasons, activity


async def test_a_finished_match_drops_a_chest_for_each_player() -> None:
    service, _profiles, items, _seasons, _activity = _full_service()

    settlements = await service.settle_match(
        match_id=MATCH_ID, rewards=_rewards(), season_ratings={WINNER: 1016, LOSER: 984}
    )

    assert settlements[WINNER].loot is not None
    assert settlements[LOSER].loot is not None
    assert len(items.inventory) == 2


async def test_settling_twice_drops_only_one_chest() -> None:
    service, _profiles, items, _seasons, _activity = _full_service()

    await service.settle_match(match_id=MATCH_ID, rewards=_rewards())
    second = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert len(items.claims) == 2  # one per player, not four
    assert len(items.inventory) == 2
    assert second[WINNER].loot is None


async def test_an_empty_item_catalog_still_settles() -> None:
    service, _profiles, items, _seasons, _activity = _full_service(
        items=FakeItemRepository(items=[])
    )

    settlements = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert settlements[WINNER].loot is None
    # The claim is still recorded, so an empty catalog cannot be re-rolled into
    # a real reward for the same match later.
    assert len(items.claims) == 2
    assert items.inventory == []


async def test_playing_counts_toward_the_daily_streak() -> None:
    service, profiles, _items, _seasons, activity = _full_service()

    settlements = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert settlements[WINNER].streak is not None
    assert settlements[WINNER].streak.day_streak == 1
    assert settlements[WINNER].streak.extended is True
    assert profiles.rows[WINNER].day_streak == 1
    assert [row[0] for row in activity.rows] == [WINNER, LOSER]
    assert all(row[3] == 1 for row in activity.rows)


async def test_a_second_match_the_same_day_does_not_extend_the_streak() -> None:
    service, profiles, _items, _seasons, _activity = _full_service()

    await service.settle_match(match_id="match-1", rewards=_rewards())
    second = await service.settle_match(match_id="match-2", rewards=_rewards())

    assert second[WINNER].streak is not None
    assert second[WINNER].streak.day_streak == 1
    assert second[WINNER].streak.extended is False
    assert profiles.rows[WINNER].day_streak == 1


async def test_the_season_ladder_moves_with_the_result() -> None:
    service, _profiles, _items, seasons, _activity = _full_service()

    settlements = await service.settle_match(
        match_id=MATCH_ID, rewards=_rewards(), season_ratings={WINNER: 1100, LOSER: 900}
    )

    winner = settlements[WINNER].season
    loser = settlements[LOSER].season
    assert winner is not None and loser is not None
    assert winner.rating_after > winner.rating_before
    assert loser.rating_after < loser.rating_before
    assert seasons.ratings[WINNER].matches_played == 1
    assert seasons.ratings[WINNER].wins == 1
    assert seasons.ratings[LOSER].losses == 1


async def test_the_season_records_a_promotion() -> None:
    service, _profiles, _items, seasons, _activity = _full_service()
    # Sitting just under Silver, then handed a rating well above it.
    seasons.ratings[WINNER] = SeasonRating(
        id="sr-w",
        season_id="season-1",
        user_id=WINNER,
        rating=1090,
        peak_rating=1090,
        matches_played=4,
        wins=2,
        losses=2,
        draws=0,
    )

    settlements = await service.settle_match(
        match_id=MATCH_ID,
        rewards={WINNER: _rewards()[WINNER]},
        season_ratings={WINNER: 1300},
    )

    change = settlements[WINNER].season
    assert change is not None
    assert change.tier_before is RankTier.BRONZE
    assert change.tier_after is RankTier.SILVER
    assert change.promoted is True


async def test_the_peak_rating_only_ever_rises() -> None:
    service, _profiles, _items, seasons, _activity = _full_service()
    await service.settle_match(
        match_id="match-1", rewards=_rewards(), season_ratings={WINNER: 1400}
    )
    peak = seasons.ratings[WINNER].peak_rating

    await service.settle_match(
        match_id="match-2", rewards=_rewards(), season_ratings={WINNER: 900}
    )

    assert seasons.ratings[WINNER].rating < peak
    assert seasons.ratings[WINNER].peak_rating == peak


async def test_the_economy_settles_on_its_own_without_the_retention_layer() -> None:
    service, profiles, ledger = _service()

    settlements = await service.settle_match(match_id=MATCH_ID, rewards=_rewards())

    assert settlements[WINNER].loot is None
    assert settlements[WINNER].season is None
    assert settlements[WINNER].streak is None
    assert profiles.rows[WINNER].gold > 0
    assert len(ledger.rows) == 2
