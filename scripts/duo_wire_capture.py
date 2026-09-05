"""Write the duo wire fixture the Android client's contract test decodes.

Built from the backend's own response models rather than typed out by hand, so
the fixture cannot quietly drift away from the schemas it is supposed to pin
down: rename a field in `app/schemas/duo/events.py`, regenerate, and the
Android DTO that still expects the old name fails its test.

Every frame here is produced by the same `envelope(...)` the engine sends
through the socket, and every REST body by the same model the route returns.

Usage:
    conda run -n backend python -m scripts.duo_wire_capture
    conda run -n backend python -m scripts.duo_wire_capture --out /some/path.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.content.challenge import ChallengeDifficulty, ChallengeType
from app.models.duo.duo_match import DuoMatchEndReason, DuoMatchMode, DuoMatchStatus
from app.models.game.skill import SkillEffect
from app.schemas.content.course_content import (
    ChallengeOptionPublicRead,
    ChallengePublicRead,
)
from app.schemas.duo.duo import (
    DuoLeaderboardEntry,
    DuoLeaderboardRead,
    DuoMatchDetail,
    DuoMatchSummary,
    DuoSettingsRead,
    DuoSkillUseRead,
    DuoStatsRead,
    LeaderboardScope,
)
from app.schemas.duo.events import (
    ActiveEffectRead,
    AnswerResultData,
    BlowRead,
    ChatMessageData,
    ConnectedData,
    ErrorCode,
    ErrorData,
    ExpChange,
    GoldChange,
    MatchFinishedData,
    MatchFoundData,
    MatchResumeData,
    MatchStartedData,
    OpponentAnsweredData,
    OpponentDisconnectedData,
    PongData,
    QuestionPushData,
    QueueWaitingData,
    RatingChange,
    RoomCreatedData,
    ServerEvent,
    SkillUsedData,
    StateTickData,
    envelope,
)
from app.schemas.game.player_card import PlayerCardRead
from app.services.duo import clock
from app.services.duo.scoring import MatchOutcome
from app.services.game.combat import StrikeKind
from app.services.game.season import RankTier

# The app repository sits beside this one; the fixture lives with the test that
# reads it.
DEFAULT_OUT = (
    Path(__file__).resolve().parents[2]
    / "duo-game-app/app/src/test/resources/duo_wire_capture.json"
)

ME = "6b1f9d1c-2f5a-4a7e-9b0e-1d0c6f2a3b41"
THEM = "3d0b412d-180b-4cb1-ab3b-8cca58fa3867"
MATCH_ID = "6ac4779a-960f-4c55-8155-70d01b312d27"
CHALLENGE_ID = "e6e7411c-68a6-4b7b-802e-7f0823ad2f30"
CORRECT_OPTION = "7a599e65-8055-45c8-8aa0-dbd07828af10"
WRONG_OPTION = "0bd3eefa-08e0-483b-9a18-1a79e4074c7c"

SETTINGS = DuoSettingsRead(
    question_count=3, time_per_question=15, topic_ids=None, difficulty=None
)


def _card(user_id: str, username: str, rating: int, level: int) -> PlayerCardRead:
    return PlayerCardRead(
        id=user_id,
        username=username,
        avatar_url=None,
        rating=rating,
        tier=RankTier.SILVER,
        level=level,
        class_code="WARRIOR",
        day_streak=4,
    )


def _question() -> ChallengePublicRead:
    return ChallengePublicRead(
        id=CHALLENGE_ID,
        lesson_id="8d0a5f61-3b1c-4a3e-9f6d-2c7b4e1a9d05",
        type=ChallengeType.SELECT,
        question="David doesn't have    _   .",
        difficulty=ChallengeDifficulty.EASY,
        topic_id=None,
        order_index=1,
        passage=None,
        options=[
            ChallengeOptionPublicRead(
                id=CORRECT_OPTION,
                text="any money",
                order_index=1,
                image_src=None,
                audio_src=None,
            ),
            ChallengeOptionPublicRead(
                id=WRONG_OPTION,
                text="some money",
                order_index=2,
                image_src=None,
                audio_src=None,
            ),
        ],
    )


def _blow() -> BlowRead:
    return BlowRead(
        damage=18,
        strike=StrikeKind.QUICK,
        combo_count=1,
        combo_multiplier=1.0,
        is_critical=False,
        stuns_opponent=False,
        element_multiplier=1.0,
    )


def ws_frames() -> list[dict[str, Any]]:
    """One frame of every type the match screen has to understand."""
    return [
        envelope(
            ServerEvent.CONNECTED,
            ConnectedData(
                user=_card(ME, "smoke-one", 1000, 5),
                active_match_id=None,
                server_time_ms=1_204_310,
            ),
        ),
        envelope(
            ServerEvent.QUEUE_WAITING, QueueWaitingData(position=1, waited_seconds=0)
        ),
        envelope(
            ServerEvent.ROOM_CREATED,
            RoomCreatedData(match_id=MATCH_ID, room_code="ABC234", settings=SETTINGS),
        ),
        envelope(
            ServerEvent.MATCH_FOUND,
            MatchFoundData(
                match_id=MATCH_ID,
                room_code="ABC234",
                mode=DuoMatchMode.FRIEND,
                opponent=_card(THEM, "smoke-two", 1050, 6),
                settings=SETTINGS,
                host_id=ME,
                # Captured from a friend room, where the host still has to start.
                auto_start=False,
            ),
        ),
        envelope(
            ServerEvent.MATCH_STARTED,
            MatchStartedData(
                match_id=MATCH_ID,
                deck_size=3,
                speed_reference_seconds=15,
                tick_hz=clock.TICK_HZ,
                snapshot_hz=clock.SNAPSHOT_HZ,
                server_time_ms=1_207_500,
                deadline_at=1_275_000,
                your_hp=115,
                your_max_hp=115,
                your_mana=10,
                opponent_hp=100,
                opponent_max_hp=100,
            ),
        ),
        envelope(
            ServerEvent.QUESTION_PUSH,
            QuestionPushData(
                token="0f3a9c1d8b4e4d1fa2c7e5b90d61a473",
                question=_question(),
                pushed_at=1_207_540,
                deck_remaining=3,
                retry=False,
            ),
        ),
        envelope(
            ServerEvent.STATE_TICK,
            StateTickData(
                t=1_209_040,
                your_hp=115,
                your_max_hp=115,
                your_mana=28,
                your_combo=1,
                your_score=940,
                your_deck_remaining=2,
                opponent_hp=82,
                opponent_max_hp=100,
                opponent_combo=0,
                opponent_score=0,
                opponent_deck_remaining=3,
                deadline_at=1_275_000,
                lockout_ends_at=1_209_140,
                stunned_until=0,
                effects=[
                    ActiveEffectRead(
                        code="SHIELD",
                        effect=SkillEffect.DAMAGE_REDUCTION,
                        magnitude=500,
                        expires_at=1_213_040,
                    )
                ],
            ),
        ),
        envelope(
            ServerEvent.ANSWER_RESULT,
            AnswerResultData(
                token="0f3a9c1d8b4e4d1fa2c7e5b90d61a473",
                correct=True,
                option_id=CORRECT_OPTION,
                elapsed_ms=1_240,
                correct_option_ids=[CORRECT_OPTION],
                explanation="'Any' đi với câu phủ định.",
                points=940,
                blow=_blow(),
                your_score=940,
                your_mana=28,
                your_combo=1,
                your_deck_remaining=2,
                opponent_hp=82,
                lockout_ends_at=1_209_140,
            ),
        ),
        envelope(
            ServerEvent.OPPONENT_ANSWERED,
            OpponentAnsweredData(
                correct=True,
                damage=16,
                is_critical=False,
                your_hp=99,
                opponent_score=880,
                opponent_combo=1,
                opponent_deck_remaining=2,
                your_stunned_until=0,
            ),
        ),
        envelope(
            ServerEvent.SKILL_USED,
            SkillUsedData(
                user_id=ME,
                skill_code="SHIELD",
                skill_name="Khiên chắn",
                effect=SkillEffect.DAMAGE_REDUCTION,
                magnitude=500,
                mana_spent=30,
                your_hp=99,
                opponent_hp=82,
                your_mana=0,
                ready_again_at=1_213_040,
                lockout_ends_at=1_209_140,
                private={"removed_option_ids": []},
            ),
        ),
        envelope(
            ServerEvent.MATCH_RESUME,
            MatchResumeData(
                match_id=MATCH_ID,
                opponent=_card(THEM, "smoke-two", 1050, 6),
                settings=SETTINGS,
                deck_size=3,
                server_time_ms=1_212_000,
                deadline_at=1_275_000,
                token="6c2b7e10a94f4b6c8f1d3a5e7b904c2d",
                question=_question(),
                pushed_at=1_211_800,
                your_deck_remaining=2,
                opponent_deck_remaining=2,
                your_score=940,
                opponent_score=880,
                your_hp=99,
                your_max_hp=115,
                opponent_hp=82,
                opponent_max_hp=100,
                your_mana=0,
                your_combo=1,
                opponent_combo=1,
                lockout_ends_at=0,
                stunned_until=0,
                effects=[],
            ),
        ),
        envelope(
            ServerEvent.OPPONENT_DISCONNECTED, OpponentDisconnectedData(grace_seconds=30)
        ),
        envelope(
            ServerEvent.CHAT_MESSAGE,
            ChatMessageData(
                user_id=THEM,
                message="gl hf",
                sent_at=datetime(2026, 9, 4, 9, 5, 52, tzinfo=UTC),
            ),
        ),
        envelope(
            ServerEvent.MATCH_FINISHED,
            MatchFinishedData(
                match_id=MATCH_ID,
                result=MatchOutcome.WIN,
                end_reason=DuoMatchEndReason.DECK_CLEARED,
                your_score=2_760,
                opponent_score=1_720,
                your_correct=3,
                opponent_correct=2,
                deck_size=3,
                your_deck_cleared=True,
                opponent_deck_cleared=False,
                duration_seconds=41,
                rating=RatingChange(before=1000, after=1016, delta=16),
                exp=ExpChange(
                    before=120,
                    after=160,
                    delta=40,
                    level_before=5,
                    level_after=5,
                    leveled_up=False,
                ),
                gold=GoldChange(before=300, after=320, delta=20),
                your_hp_left=99,
                opponent_hp_left=46,
                loot=None,
                season=None,
                streak=None,
                energy_left=4,
            ),
        ),
        envelope(
            ServerEvent.ERROR,
            ErrorData(
                code=ErrorCode.QUESTION_CLOSED, message="That question is no longer open"
            ),
        ),
        envelope(ServerEvent.PONG, PongData(client_time_ms=884_120, server_time_ms=1_212_004)),
    ]


def rest_bodies() -> dict[str, Any]:
    summary = DuoMatchSummary(
        match_id=MATCH_ID,
        mode=DuoMatchMode.FRIEND,
        status=DuoMatchStatus.FINISHED,
        end_reason=DuoMatchEndReason.DECK_CLEARED,
        outcome=MatchOutcome.WIN,
        opponent=_card(THEM, "smoke-two", 1050, 6),
        my_score=2_760,
        opponent_score=1_720,
        my_correct=3,
        opponent_correct=2,
        question_count=3,
        duration_seconds=41,
        finished_at=datetime(2026, 9, 4, 9, 6, 27, tzinfo=UTC),
        created_at=datetime(2026, 9, 4, 9, 5, 46, tzinfo=UTC),
    )
    detail = DuoMatchDetail(
        **summary.model_dump(),
        my_hp_left=99,
        opponent_hp_left=46,
        skill_uses=[
            DuoSkillUseRead(round_index=1, skill_code="SHIELD", mana_spent=30, mine=True),
            DuoSkillUseRead(round_index=2, skill_code="DRAIN", mana_spent=20, mine=False),
        ],
    )
    stats = DuoStatsRead(
        rating=1016,
        matches_played=4,
        wins=3,
        losses=1,
        draws=0,
        win_rate=75.0,
        current_streak=2,
        best_streak=3,
    )
    board = DuoLeaderboardRead(
        entries=[
            DuoLeaderboardEntry(
                rank=1,
                user_id=THEM,
                username="smoke-two",
                avatar_url=None,
                rating=1_050,
                matches_played=6,
                wins=4,
                tier=RankTier.GOLD,
            ),
            DuoLeaderboardEntry(
                rank=2,
                user_id=ME,
                username="smoke-one",
                avatar_url=None,
                rating=1_016,
                matches_played=4,
                wins=3,
                tier=RankTier.SILVER,
            ),
        ],
        my_rank=2,
        scope=LeaderboardScope.CURRENT,
        season_code="2026-S3",
    )
    return {
        "MATCHES": [summary.model_dump(mode="json")],
        "MATCH_DETAIL": detail.model_dump(mode="json"),
        "STATS": stats.model_dump(mode="json"),
        "LEADERBOARD": board.model_dump(mode="json"),
    }


def build() -> dict[str, Any]:
    return {"ws": ws_frames(), "rest": rest_bodies()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = build()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(payload['ws'])} frames and {len(payload['rest'])} bodies to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
