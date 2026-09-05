"""Applying a finished match's payout to a player's game profile.

This is the one place experience and gold actually move, and it is written so
that running it twice for the same match is harmless: the gold ledger's unique
(user_id, reason, ref_id) constraint decides whether a payout is new, and the
experience write rides along on that same decision.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.game.game_item import ItemRarity
from app.models.game.gold_transaction import GoldReason
from app.models.game.user_game_profile import UserGameProfile
from app.repository.game.activity_repository import ActivityRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.season_repository import SeasonRepository
from app.services.duo.scoring import MatchOutcome
from app.services.game.leveling import apply_exp, level_for_exp
from app.services.game.loot import ItemDrop, roll_item
from app.services.game.rewards import RewardInput, compute_reward
from app.services.game.season import RankTier, tier_for_rating
from app.services.game.season_service import ensure_active_season, opening_rating
from app.services.game.streak import streak_after, today_in_streak_tz

_REASON_BY_OUTCOME = {
    MatchOutcome.WIN: GoldReason.MATCH_WIN,
    MatchOutcome.LOSE: GoldReason.MATCH_LOSS,
    MatchOutcome.DRAW: GoldReason.MATCH_DRAW,
}


@dataclass(frozen=True)
class ExpAward:
    before: int
    after: int
    level_before: int
    level_after: int

    @property
    def delta(self) -> int:
        return self.after - self.before

    @property
    def leveled_up(self) -> bool:
        return self.level_after > self.level_before


@dataclass(frozen=True)
class GoldAward:
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before


@dataclass(frozen=True)
class LootDrop:
    item_id: str
    code: str
    name: str
    rarity: ItemRarity


@dataclass(frozen=True)
class SeasonChange:
    season_code: str
    rating_before: int
    rating_after: int
    tier_before: RankTier
    tier_after: RankTier

    @property
    def promoted(self) -> bool:
        return self.tier_after != self.tier_before


@dataclass(frozen=True)
class StreakChange:
    day_streak: int
    best_day_streak: int
    extended: bool


@dataclass(frozen=True)
class BattlePayout:
    """What one lesson battle is worth, at both prices.

    The caller works out the numbers (`services/pve/rewards.py`); which of the
    two pairs is actually paid is decided here, by whether the gold ledger
    accepts the first-clear row for this lesson.
    """

    first_clear_exp: int
    first_clear_gold: int
    replay_exp: int
    replay_gold: int


@dataclass(frozen=True)
class BattleSettlement:
    exp: ExpAward
    gold: GoldAward
    first_clear: bool
    loot: LootDrop | None = None
    streak: StreakChange | None = None


@dataclass(frozen=True)
class PlayerSettlement:
    exp: ExpAward
    gold: GoldAward
    loot: LootDrop | None = None
    season: SeasonChange | None = None
    streak: StreakChange | None = None


class GameSettlementService:
    def __init__(
        self,
        *,
        profiles: GameProfileRepository,
        ledger: GoldTransactionRepository,
        items: ItemRepository | None = None,
        seasons: SeasonRepository | None = None,
        activity: ActivityRepository | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.profiles = profiles
        self.ledger = ledger
        # The retention layer is optional so the economy can be settled on its
        # own; a caller that leaves these out simply gets no chest or ladder
        # movement rather than an error.
        self.items = items
        self.seasons = seasons
        self.activity = activity
        self.rng = rng or random.Random()  # noqa: S311 -- loot rolls, not security

    async def settle_match(
        self,
        *,
        match_id: str,
        rewards: dict[str, RewardInput],
        season_ratings: dict[str, int] | None = None,
    ) -> dict[str, PlayerSettlement]:
        """Pay out one finished match to every player in it."""
        return {
            user_id: await self._settle_player(
                match_id, user_id, data, (season_ratings or {}).get(user_id)
            )
            for user_id, data in rewards.items()
        }

    async def _settle_player(
        self,
        match_id: str,
        user_id: str,
        data: RewardInput,
        all_time_rating: int | None = None,
    ) -> PlayerSettlement:
        profile = await self.profiles.get_or_create(user_id)
        exp_before = profile.total_exp
        level_before = profile.level
        gold_before = profile.gold

        reward = compute_reward(data)
        gold_after = max(0, gold_before + reward.gold_delta)

        granted = await self.ledger.grant(
            user_id=user_id,
            amount=reward.gold_delta,
            reason=_REASON_BY_OUTCOME[data.outcome],
            ref_id=match_id,
            balance_after=gold_after,
        )
        if not granted:
            # This match has already paid this player. Report the standing
            # balance as an unchanged award rather than moving anything.
            return PlayerSettlement(
                exp=ExpAward(exp_before, exp_before, level_before, level_before),
                gold=GoldAward(gold_before, gold_before),
            )

        exp_after = apply_exp(
            exp_before, reward.exp_delta, floor_at_current_level=True
        )
        level_after = level_for_exp(exp_after)
        await self.profiles.save(
            profile,
            {
                "total_exp": exp_after,
                "level": level_after,
                "gold": gold_after,
                "updated_at": datetime.now(UTC),
            },
        )
        streak = await self._record_activity(user_id, profile)
        loot = await self._roll_chest(
            user_id, match_id, won=data.outcome is MatchOutcome.WIN
        )
        season = await self._apply_season(user_id, data.outcome, all_time_rating)

        return PlayerSettlement(
            exp=ExpAward(exp_before, exp_after, level_before, level_after),
            gold=GoldAward(gold_before, gold_after),
            loot=loot,
            season=season,
            streak=streak,
        )

    async def settle_battle(
        self,
        *,
        user_id: str,
        battle_id: str,
        lesson_id: str,
        payout: BattlePayout,
        roll_chest: bool = False,
    ) -> BattleSettlement:
        """Pay out one won lesson battle.

        Anti-grinding is the ledger's existing unique (user_id, reason, ref_id)
        rather than a new column: the first clear is a BATTLE_WIN row keyed on
        the *lesson*, so a lesson can pay full price exactly once and forever;
        every run after it is a BATTLE_REPLAY row keyed on the *battle*, one row
        each, at the smaller price. Settling the same battle twice inserts
        neither and moves nothing.

        Elo and the season ladder are deliberately untouched: PvE must not
        climb the PvP ladder.
        """
        profile = await self.profiles.get_or_create(user_id)
        exp_before = profile.total_exp
        level_before = profile.level
        gold_before = profile.gold

        first_clear = await self.ledger.grant(
            user_id=user_id,
            amount=payout.first_clear_gold,
            reason=GoldReason.BATTLE_WIN,
            ref_id=lesson_id,
            balance_after=gold_before + payout.first_clear_gold,
        )
        if first_clear:
            exp_delta, gold_delta = payout.first_clear_exp, payout.first_clear_gold
        else:
            exp_delta, gold_delta = payout.replay_exp, payout.replay_gold
            replayed = await self.ledger.grant(
                user_id=user_id,
                amount=gold_delta,
                reason=GoldReason.BATTLE_REPLAY,
                ref_id=battle_id,
                balance_after=gold_before + gold_delta,
            )
            if not replayed:
                # This battle has already been settled. Report the standing
                # balance as an unchanged award rather than moving anything.
                return BattleSettlement(
                    exp=ExpAward(exp_before, exp_before, level_before, level_before),
                    gold=GoldAward(gold_before, gold_before),
                    first_clear=False,
                )

        gold_after = max(0, gold_before + gold_delta)
        exp_after = apply_exp(exp_before, exp_delta, floor_at_current_level=True)
        level_after = level_for_exp(exp_after)
        await self.profiles.save(
            profile,
            {
                "total_exp": exp_after,
                "level": level_after,
                "gold": gold_after,
                "updated_at": datetime.now(UTC),
            },
        )
        streak = await self._record_activity(user_id, profile)
        # Chests are a boss reward here, so an ordinary lesson stays worth
        # doing without turning the path into a slot machine.
        loot = await self._roll_chest(user_id, battle_id, won=roll_chest)

        return BattleSettlement(
            exp=ExpAward(exp_before, exp_after, level_before, level_after),
            gold=GoldAward(gold_before, gold_after),
            first_clear=first_clear,
            loot=loot,
            streak=streak,
        )

    async def _record_activity(
        self, user_id: str, profile: UserGameProfile
    ) -> StreakChange | None:
        """Count today toward the streak. Playing counts as studying."""
        if self.activity is None:
            return None
        today = today_in_streak_tz(datetime.now(UTC))
        await self.activity.record(user_id, today, matches=1)
        extended = profile.last_active_date != today
        streak = streak_after(profile.day_streak, profile.last_active_date, today)
        best = max(profile.best_day_streak, streak)
        await self.profiles.save(
            profile,
            {
                "day_streak": streak,
                "best_day_streak": best,
                "last_active_date": today,
                "updated_at": datetime.now(UTC),
            },
        )
        return StreakChange(day_streak=streak, best_day_streak=best, extended=extended)

    async def _roll_chest(
        self, user_id: str, match_id: str, *, won: bool
    ) -> LootDrop | None:
        """One chest per player per match, guarded by the loot ledger."""
        if self.items is None:
            return None
        pool = [
            ItemDrop(item_id=item.id, code=item.code, name=item.name, rarity=item.rarity)
            for item in await self.items.list_items()
        ]
        drop = roll_item(self.rng, pool, won=won)
        # The claim goes in even when nothing dropped, so an empty catalog
        # cannot be re-rolled later into a real reward for the same match.
        claimed = await self.items.claim_loot(
            user_id, match_id, drop.item_id if drop is not None else None
        )
        if not claimed or drop is None:
            return None
        await self.items.add_to_inventory(user_id, drop.item_id)
        return LootDrop(
            item_id=drop.item_id, code=drop.code, name=drop.name, rarity=drop.rarity
        )

    async def _apply_season(
        self, user_id: str, outcome: MatchOutcome, all_time_rating: int | None
    ) -> SeasonChange | None:
        """Mirror the result onto the current season's ladder.

        `duo_ratings` stays the all-time rating and is never reset; this is the
        projection the ladder resets each season.
        """
        if self.seasons is None or all_time_rating is None:
            return None
        season = await ensure_active_season(self.seasons)
        existing = await self.seasons.rating(season.id, user_id)
        record = existing or await self.seasons.get_or_create_rating(
            season.id, user_id, opening_rating=opening_rating(all_time_rating)
        )

        before = record.rating
        # The season ladder tracks the same Elo movement as the all-time
        # rating, so a player's climb is the same skill measured over a window.
        after = max(0, before + (all_time_rating - before) // 2)
        await self.seasons.save_rating(
            record,
            {
                "rating": after,
                "peak_rating": max(record.peak_rating, after),
                "matches_played": record.matches_played + 1,
                "wins": record.wins + (1 if outcome is MatchOutcome.WIN else 0),
                "losses": record.losses + (1 if outcome is MatchOutcome.LOSE else 0),
                "draws": record.draws + (1 if outcome is MatchOutcome.DRAW else 0),
            },
        )
        return SeasonChange(
            season_code=season.code,
            rating_before=before,
            rating_after=after,
            tier_before=tier_for_rating(before),
            tier_after=tier_for_rating(after),
        )

