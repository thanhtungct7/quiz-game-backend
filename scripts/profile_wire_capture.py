"""Write the profile wire fixture the Android client's contract test decodes.

Built from the backend's own response models rather than typed out by hand, for
the reason `scripts/duo_wire_capture.py` gives: rename a field in
`app/schemas/profile/profile.py`, regenerate, and the Android DTO that still
expects the old name fails its test.

That guard matters more here than usual. The client decodes with
`ignoreUnknownKeys = true`, so a renamed field does not raise -- it silently
reads as the DTO's default, and a profile card quietly shows a level of 1 and a
rating of 0 with nothing in any log to say why.

Usage:
    conda run -n backend python -m scripts.profile_wire_capture
    conda run -n backend python -m scripts.profile_wire_capture --out /some/path.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.game.achievement import AchievementCategory
from app.schemas.game.game import EnergyRead
from app.schemas.profile.profile import (
    AchievementListRead,
    AchievementProgressRead,
    AchievementRead,
    CombatBreakdownRead,
    CombatStatsRead,
    LearningStatsRead,
    PublicProfileRead,
    PvpStatsRead,
    SelfProfileRead,
    StatSourceRead,
)
from app.services.game.cefr import CefrBand
from app.services.game.combat_stats import StatSourceKind
from app.services.game.season import RankTier

# The app repository sits beside this one; the fixture lives with the test that
# reads it.
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2]
    / "duo-game-app/app/src/test/resources/profile_wire_capture.json"
)

ME = "6b1f9d1c-2f5a-4a7e-9b0e-1d0c6f2a3b41"
THEM = "3d0b412d-180b-4cb1-ab3b-8cca58fa3867"
JOINED_AT = datetime(2026, 1, 14, 9, 30, tzinfo=UTC)


def _pvp() -> PvpStatsRead:
    return PvpStatsRead(
        rating=1420,
        tier=RankTier.GOLD,
        matches_played=30,
        wins=19,
        losses=9,
        draws=2,
        win_rate=63.3,
    )


def _learning() -> LearningStatsRead:
    return LearningStatsRead(
        challenges_attempted=420,
        challenges_mastered=355,
        total_attempts=610,
        accuracy=58.2,
    )


def _combat() -> CombatStatsRead:
    """A mage in full gear. The four numbers the card draws as bars."""
    return CombatStatsRead(hp=118, atk=27, defence=6, mana=14, damage_permille=1_350)


def _featured() -> list[AchievementRead]:
    """The three most recent, which is exactly what the overview tab has room for."""
    return [
        AchievementRead(
            code="STREAK_7",
            name="Trọn một tuần",
            description="Học bảy ngày liên tiếp.",
            category=AchievementCategory.LEARNING,
            icon_code="FLAME",
            unlocked_at=JOINED_AT,
        ),
        AchievementRead(
            code="PVP_WINS_10",
            name="Tay đấu",
            description="Thắng mười trận đấu.",
            category=AchievementCategory.PVP,
            icon_code="SWORD",
            unlocked_at=JOINED_AT,
        ),
        AchievementRead(
            code="LEVEL_20",
            name="Lão luyện",
            description="Đạt cấp 20.",
            category=AchievementCategory.PROGRESSION,
            icon_code="LEVEL",
            unlocked_at=JOINED_AT,
        ),
    ]


def combat_breakdown() -> CombatBreakdownRead:
    """`GET /profile/me/combat` -- where each of the four numbers came from.

    The lines add up to the total exactly, including the clipped equipment
    line; the fixture keeps that property so the client's modal can be checked
    against it.
    """
    return CombatBreakdownRead(
        total=_combat(),
        sources=[
            StatSourceRead(
                kind=StatSourceKind.CLASS,
                code="MAGE",
                label="Pháp sư",
                hp=90,
                atk=22,
                defence=0,
                mana=10,
            ),
            StatSourceRead(
                kind=StatSourceKind.EQUIPMENT,
                code="STAFF_EMBER",
                label="Trượng Than Hồng",
                hp=0,
                atk=5,
                defence=2,
                mana=2,
            ),
            StatSourceRead(
                kind=StatSourceKind.EQUIPMENT,
                code="ROBE_SILK",
                label="Áo choàng Lụa",
                hp=20,
                atk=0,
                defence=4,
                mana=2,
            ),
            StatSourceRead(
                kind=StatSourceKind.STREAK,
                code="STREAK",
                label="Chuỗi ngày",
                hp=8,
                atk=0,
                defence=0,
                mana=0,
            ),
        ],
    )


def achievements() -> AchievementListRead:
    """`GET /profile/me/achievements` -- the whole shelf, earned and not.

    One of each state, because the tab draws them differently: unlocked with a
    date, in progress with a bar, and untouched at zero.
    """
    return AchievementListRead(
        unlocked_count=7,
        total=21,
        items=[
            AchievementProgressRead(
                code="LEVEL_20",
                name="Lão luyện",
                description="Đạt cấp 20.",
                category=AchievementCategory.PROGRESSION,
                icon_code="LEVEL",
                unlocked_at=JOINED_AT,
                threshold=20,
                current=20,
                unlocked=True,
            ),
            AchievementProgressRead(
                code="LEVEL_50",
                name="Bậc thầy",
                description="Đạt cấp 50.",
                category=AchievementCategory.PROGRESSION,
                icon_code="CROWN",
                unlocked_at=None,
                threshold=50,
                current=34,
                unlocked=False,
            ),
            AchievementProgressRead(
                code="BATTLES_WON_25",
                name="Thợ săn quái",
                description="Hạ hai mươi lăm con quái.",
                category=AchievementCategory.PVE,
                icon_code="SKULL",
                unlocked_at=None,
                threshold=25,
                current=0,
                unlocked=False,
            ),
        ],
    )


def public_card() -> PublicProfileRead:
    """Another player, as a leaderboard row or lobby opens them."""
    return PublicProfileRead(
        id=THEM,
        username="Đối thủ",
        bio="Luyện TOEIC mỗi tối.",
        avatar_url=f"/api/v1/users/{THEM}/avatar?v=abcd1234",
        joined_at=JOINED_AT,
        level=34,
        cefr=CefrBand.B1,
        toeic_estimate=620,
        class_code="MAGE",
        day_streak=12,
        best_day_streak=40,
        pvp=_pvp(),
        learning=_learning(),
        combat=_combat(),
        featured_achievements=_featured(),
        total_achievements_unlocked=7,
    )


def self_card() -> SelfProfileRead:
    """The caller's own profile, with the private half filled in."""
    return SelfProfileRead(
        **public_card().model_dump(),
        email="player@example.com",
        has_uploaded_avatar=True,
        gold=1250,
        energy=EnergyRead(current=3, maximum=5, next_regen_at=JOINED_AT),
        total_exp=56_100,
        exp_for_current_level=54_450,
        exp_for_next_level=57_800,
        exp_to_next_level=1_700,
        next_cefr=CefrBand.B2,
        next_cefr_at_level=51,
    )


def new_account_card() -> SelfProfileRead:
    """A brand-new account: no game profile row, no rating row, nothing answered.

    Pinned deliberately. This is the payload every field's default has to
    survive, and it is the one the client is most likely to get wrong.
    """
    return SelfProfileRead(
        id=ME,
        username=None,
        bio=None,
        avatar_url=None,
        joined_at=JOINED_AT,
        level=1,
        cefr=CefrBand.A1,
        toeic_estimate=10,
        class_code=None,
        day_streak=0,
        best_day_streak=0,
        pvp=PvpStatsRead(
            rating=1000,
            tier=RankTier.BRONZE,
            matches_played=0,
            wins=0,
            losses=0,
            draws=0,
            win_rate=0.0,
        ),
        learning=LearningStatsRead(
            challenges_attempted=0,
            challenges_mastered=0,
            total_attempts=0,
            accuracy=0.0,
        ),
        # No class picked and nothing equipped: the baseline every account
        # fights on before it has chosen anything.
        combat=CombatStatsRead(hp=100, atk=20, defence=0, mana=0, damage_permille=1_000),
        featured_achievements=[],
        total_achievements_unlocked=0,
        email="new@example.com",
        has_uploaded_avatar=False,
        gold=0,
        energy=EnergyRead(current=5, maximum=5, next_regen_at=None),
        total_exp=0,
        exp_for_current_level=0,
        exp_for_next_level=100,
        exp_to_next_level=100,
        next_cefr=CefrBand.A2,
        next_cefr_at_level=11,
    )


def build() -> dict[str, Any]:
    return {
        "SELF": self_card().model_dump(mode="json"),
        "PUBLIC": public_card().model_dump(mode="json"),
        "SELF_NEW_ACCOUNT": new_account_card().model_dump(mode="json"),
        "COMBAT_BREAKDOWN": combat_breakdown().model_dump(mode="json"),
        "ACHIEVEMENTS": achievements().model_dump(mode="json"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(payload)} bodies to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
