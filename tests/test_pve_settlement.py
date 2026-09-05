"""Paying out a won lesson battle, and refusing to pay for it twice."""

from datetime import UTC, datetime

from app.models.game.gold_transaction import GoldReason
from app.models.game.user_game_profile import UserGameProfile
from app.services.game.settlement import BattlePayout, GameSettlementService
from app.services.pve.rewards import BattleRewardInput, compute_battle_reward, replayed

USER = "user-1"
LESSON = "lesson-1"
BATTLE = "battle-1"


class FakeProfileRepository:
    def __init__(self) -> None:
        self.rows: dict[str, UserGameProfile] = {}

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
        for field, value in data.items():
            setattr(profile, field, value)
        return profile


class FakeGoldLedger:
    """Enforces the same unique (user_id, reason, ref_id) the table does."""

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
        key = (user_id, reason, ref_id)
        if any(row[:3] == key for row in self.rows):
            return False
        self.rows.append((user_id, reason, ref_id, amount))
        return True

    def reasons(self) -> list[GoldReason]:
        return [row[1] for row in self.rows]


def _service() -> tuple[GameSettlementService, FakeProfileRepository, FakeGoldLedger]:
    profiles = FakeProfileRepository()
    ledger = FakeGoldLedger()
    return (
        GameSettlementService(profiles=profiles, ledger=ledger),  # type: ignore[arg-type]
        profiles,
        ledger,
    )


def _payout(*, correct_count: int = 10, is_boss: bool = False) -> BattlePayout:
    full = compute_battle_reward(
        BattleRewardInput(
            won=True, correct_count=correct_count, is_boss=is_boss, flawless=False
        )
    )
    share = replayed(full)
    return BattlePayout(
        first_clear_exp=full.exp,
        first_clear_gold=full.gold,
        replay_exp=share.exp,
        replay_gold=share.gold,
    )


async def test_the_first_clear_of_a_lesson_pays_full_price() -> None:
    service, profiles, ledger = _service()
    payout = _payout(correct_count=10)

    settlement = await service.settle_battle(
        user_id=USER, battle_id=BATTLE, lesson_id=LESSON, payout=payout
    )

    assert settlement.first_clear is True
    assert settlement.exp.delta == payout.first_clear_exp
    assert settlement.gold.delta == payout.first_clear_gold
    assert profiles.rows[USER].gold == payout.first_clear_gold
    # Keyed on the lesson, so the lesson can never pay full price again.
    assert ledger.rows == [
        (USER, GoldReason.BATTLE_WIN, LESSON, payout.first_clear_gold)
    ]


async def test_beating_the_same_lesson_again_pays_the_replay_share() -> None:
    service, profiles, ledger = _service()
    payout = _payout(correct_count=10)

    await service.settle_battle(
        user_id=USER, battle_id=BATTLE, lesson_id=LESSON, payout=payout
    )
    again = await service.settle_battle(
        user_id=USER, battle_id="battle-2", lesson_id=LESSON, payout=payout
    )

    assert again.first_clear is False
    assert again.exp.delta == payout.replay_exp
    assert again.gold.delta == payout.replay_gold
    assert ledger.reasons() == [GoldReason.BATTLE_WIN, GoldReason.BATTLE_REPLAY]
    assert profiles.rows[USER].gold == payout.first_clear_gold + payout.replay_gold


async def test_settling_the_same_battle_twice_moves_nothing_the_second_time() -> None:
    service, profiles, ledger = _service()
    payout = _payout()

    await service.settle_battle(
        user_id=USER, battle_id=BATTLE, lesson_id=LESSON, payout=payout
    )
    await service.settle_battle(
        user_id=USER, battle_id="battle-2", lesson_id=LESSON, payout=payout
    )
    gold_before_retry = profiles.rows[USER].gold
    exp_before_retry = profiles.rows[USER].total_exp

    retry = await service.settle_battle(
        user_id=USER, battle_id="battle-2", lesson_id=LESSON, payout=payout
    )

    assert retry.exp.delta == 0
    assert retry.gold.delta == 0
    assert retry.first_clear is False
    assert profiles.rows[USER].gold == gold_before_retry
    assert profiles.rows[USER].total_exp == exp_before_retry
    assert len(ledger.rows) == 2


async def test_another_player_still_gets_their_own_first_clear() -> None:
    service, profiles, _ = _service()
    payout = _payout()

    await service.settle_battle(
        user_id=USER, battle_id=BATTLE, lesson_id=LESSON, payout=payout
    )
    other = await service.settle_battle(
        user_id="user-2", battle_id="battle-2", lesson_id=LESSON, payout=payout
    )

    assert other.first_clear is True
    assert profiles.rows["user-2"].gold == payout.first_clear_gold


async def test_a_battle_never_touches_the_season_ladder() -> None:
    """PvE pays experience and gold; it must not move a PvP rating."""
    service, _, _ = _service()

    settlement = await service.settle_battle(
        user_id=USER, battle_id=BATTLE, lesson_id=LESSON, payout=_payout()
    )

    assert not hasattr(settlement, "season")
