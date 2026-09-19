from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import (
    AiUnavailableError,
    ConversationAwaitingReplyError,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTooShortError,
    DailyConversationLimitError,
    ScenarioNotFoundError,
)
from app.models.conversation.conversation_message import ConversationMessage, MessageRole
from app.models.conversation.conversation_session import ConversationSession, ConversationStatus
from app.models.game.user_game_profile import UserGameProfile
from app.services.ai.llm_client import ChatTurn
from app.services.conversation.conversation_service import ConversationService
from app.services.conversation.prompts import (
    GOAL_DONE_MARKER,
    BetterPhrase,
    ConversationFeedback,
    Correction,
    HintList,
    HintSuggestion,
)
from app.services.conversation.scenarios import get_scenario
from app.services.game.daily_quests import QuestEvent
from app.services.game.leveling import exp_for_level

# 10:00 in Vietnam.
NOW = datetime(2026, 9, 17, 3, 0, tzinfo=UTC)
USER = "user-1"


class FakeConversations:
    def __init__(self) -> None:
        self.sessions: dict[str, ConversationSession] = {}
        self.rows: dict[str, list[ConversationMessage]] = {}
        self.saves = 0

    async def create_session(
        self, session: ConversationSession, opening: ConversationMessage
    ) -> ConversationSession:
        session.id = str(uuid4())
        self.sessions[session.id] = session
        opening.id = str(uuid4())
        opening.session_id = session.id
        self.rows[session.id] = [opening]
        return session

    async def get_session(self, user_id: str, session_id: str) -> ConversationSession | None:
        session = self.sessions.get(session_id)
        return session if session is not None and session.user_id == user_id else None

    async def list_sessions(
        self, user_id: str, *, limit: int, before: datetime | None
    ) -> list[ConversationSession]:
        rows = [s for s in self.sessions.values() if s.user_id == user_id]
        if before is not None:
            rows = [s for s in rows if s.started_at < before]
        return sorted(rows, key=lambda s: s.started_at, reverse=True)[:limit]

    async def count_started_since(self, user_id: str, since: datetime) -> int:
        return sum(
            1 for s in self.sessions.values() if s.user_id == user_id and s.started_at >= since
        )

    async def messages(self, session_id: str) -> list[ConversationMessage]:
        return list(self.rows.get(session_id, []))

    async def get_message(self, session_id: str, message_id: str) -> ConversationMessage | None:
        return next((m for m in self.rows.get(session_id, []) if m.id == message_id), None)

    async def add_message(
        self, session: ConversationSession, role: MessageRole, content: str, seq: int
    ) -> ConversationMessage:
        message = ConversationMessage(
            id=str(uuid4()),
            session_id=session.id,
            seq=seq,
            role=role,
            content=content,
            created_at=NOW,
        )
        self.rows[session.id].append(message)
        return message

    async def save(self) -> None:
        self.saves += 1

    async def delete_session(self, session: ConversationSession) -> None:
        del self.sessions[session.id]
        del self.rows[session.id]


class FakeProfiles:
    def __init__(self, profile: UserGameProfile | None = None) -> None:
        self.profile = profile

    async def get_by_user(self, user_id: str) -> UserGameProfile | None:
        return self.profile


class FakeLlm:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.json_answers: list[BaseModel] = []
        self.fail = False
        self.text_calls: list[dict[str, Any]] = []
        self.json_calls: list[dict[str, Any]] = []

    async def generate_text(
        self, *, model: str, system: str, turns: Sequence[ChatTurn], max_output_tokens: int
    ) -> str:
        self.text_calls.append({"model": model, "system": system, "turns": list(turns)})
        if self.fail:
            raise AiUnavailableError("down")
        return self.replies.pop(0) if self.replies else "Sure, anything else?"

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[BaseModel],
        max_output_tokens: int,
    ) -> BaseModel:
        self.json_calls.append({"model": model, "prompt": prompt, "schema": schema})
        if self.fail:
            raise AiUnavailableError("down")
        return self.json_answers.pop(0)


class FakeQuestTracker:
    def __init__(self) -> None:
        self.events: list[tuple[str, QuestEvent]] = []

    async def track_quietly(self, user_id: str, event: QuestEvent, now: datetime) -> list[Any]:
        self.events.append((user_id, event))
        return []


def _service(
    llm: FakeLlm | None = None,
    profile: UserGameProfile | None = None,
    conversations: FakeConversations | None = None,
    quests: FakeQuestTracker | None = None,
) -> tuple[ConversationService, FakeConversations]:
    conversations = conversations or FakeConversations()
    service = ConversationService(
        conversations=cast(Any, conversations),
        profiles=cast(Any, FakeProfiles(profile)),
        llm=llm,
        config=settings,
        quests=cast(Any, quests),
    )
    return service, conversations


def _feedback(score: int = 80) -> ConversationFeedback:
    return ConversationFeedback(
        score=score,
        goal_completed=True,
        summary_vi="  Tốt lắm.  ",
        corrections=[
            Correction(
                original="I want coffee",
                corrected="I'd like a coffee",
                explanation_vi="Lịch sự hơn",
            )
        ],
        better_phrases=[BetterPhrase(original="Give me", natural="Could I have", note_vi="")],
        new_words=["latte", " ", "takeaway"],
    )


async def test_starting_a_conversation_opens_with_the_scenario_line_and_no_ai_call() -> None:
    llm = FakeLlm()
    service, _ = _service(llm)

    detail = await service.start(USER, "cafe_order", NOW)

    assert detail.cefr == "A1"
    assert detail.status is ConversationStatus.ACTIVE
    assert [m.content for m in detail.messages] == [get_scenario("cafe_order").opening_line]  # type: ignore[union-attr]
    assert detail.messages[0].role is MessageRole.ASSISTANT
    assert llm.text_calls == [] and llm.json_calls == []


async def test_the_conversation_uses_the_learners_capped_level() -> None:
    profile = UserGameProfile(
        user_id=USER, total_exp=exp_for_level(40), benchmark_cleared_level=None
    )
    service, _ = _service(FakeLlm(), profile)

    detail = await service.start(USER, "cafe_order", NOW)

    # Level 40 on experience alone, but held at the first cap until its exam is passed.
    assert detail.cefr == "A1"


async def test_unknown_scenario_is_refused() -> None:
    service, _ = _service(FakeLlm())

    with pytest.raises(ScenarioNotFoundError):
        await service.start(USER, "nope", NOW)


async def test_without_an_ai_client_nothing_starts() -> None:
    service, conversations = _service(llm=None)

    with pytest.raises(AiUnavailableError):
        await service.start(USER, "cafe_order", NOW)
    assert conversations.sessions == {}


async def test_the_daily_limit_counts_from_midnight_vietnam_time() -> None:
    service, conversations = _service(FakeLlm())
    for _ in range(settings.conversation_sessions_per_day):
        await service.start(USER, "cafe_order", NOW)

    with pytest.raises(DailyConversationLimitError):
        await service.start(USER, "cafe_order", NOW)

    # 00:30 the next day in Vietnam is still 17:30 UTC the same day.
    tomorrow_vn = datetime(2026, 9, 17, 17, 30, tzinfo=UTC)
    detail = await service.start(USER, "cafe_order", tomorrow_vn)
    assert detail.status is ConversationStatus.ACTIVE


async def test_sending_stores_both_lines_and_sends_history_to_the_ai() -> None:
    llm = FakeLlm()
    llm.replies = ["Great choice. Anything to eat?"]
    service, conversations = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    turn = await service.send(USER, session_id, "A latte, please.")

    assert turn.user_message.seq == 2 and turn.assistant_message.seq == 3
    assert turn.assistant_message.content == "Great choice. Anything to eat?"
    assert turn.user_turns == 1 and not turn.should_finish
    call = llm.text_calls[0]
    assert call["model"] == settings.deepseek_chat_model
    assert "barista" in call["system"]
    assert [t.role for t in call["turns"]] == ["model", "user"]
    assert len(conversations.rows[session_id]) == 3


async def test_the_goal_marker_is_removed_and_asks_to_finish() -> None:
    llm = FakeLlm()
    llm.replies = [f"Here you go. Have a nice day! {GOAL_DONE_MARKER}"]
    service, _ = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    turn = await service.send(USER, session_id, "Thanks, here's the money.")

    assert turn.assistant_message.content == "Here you go. Have a nice day!"
    assert turn.should_finish
    assert (await service.get(USER, session_id)).should_finish


async def test_the_goal_marker_means_nothing_in_open_conversation() -> None:
    llm = FakeLlm()
    llm.replies = [f"Bye! {GOAL_DONE_MARKER}"]
    service, _ = _service(llm)
    session_id = (await service.start(USER, "free_talk", NOW)).id

    turn = await service.send(USER, session_id, "I have to go.")

    assert turn.assistant_message.content == "Bye!"
    assert not turn.should_finish


async def test_running_out_of_turns_closes_the_conversation() -> None:
    service, _ = _service(FakeLlm())
    session_id = (await service.start(USER, "cafe_order", NOW)).id
    max_turns = get_scenario("cafe_order").max_turns  # type: ignore[union-attr]

    for i in range(max_turns):
        turn = await service.send(USER, session_id, f"line {i}")
    assert turn.should_finish

    with pytest.raises(ConversationClosedError):
        await service.send(USER, session_id, "one more")
    with pytest.raises(ConversationClosedError):
        await service.hint(USER, session_id)


async def test_a_failed_reply_keeps_the_line_and_retry_answers_it() -> None:
    llm = FakeLlm()
    service, conversations = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    llm.fail = True
    with pytest.raises(AiUnavailableError):
        await service.send(USER, session_id, "A tea, please.")
    detail = await service.get(USER, session_id)
    assert detail.awaiting_reply and detail.user_turns == 1

    with pytest.raises(ConversationAwaitingReplyError):
        await service.send(USER, session_id, "Hello?")

    llm.fail = False
    llm.replies = ["One tea coming up."]
    turn = await service.retry(USER, session_id)
    assert turn.user_message.content == "A tea, please."
    assert turn.assistant_message.content == "One tea coming up."
    assert turn.user_turns == 1
    assert not (await service.get(USER, session_id)).awaiting_reply


async def test_retry_with_nothing_waiting_is_refused() -> None:
    service, _ = _service(FakeLlm())
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    with pytest.raises(ConversationClosedError):
        await service.retry(USER, session_id)


async def test_another_users_conversation_reads_as_missing() -> None:
    service, _ = _service(FakeLlm())
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    with pytest.raises(ConversationNotFoundError):
        await service.get("someone-else", session_id)
    with pytest.raises(ConversationNotFoundError):
        await service.send("someone-else", session_id, "hi")
    with pytest.raises(ConversationNotFoundError):
        await service.delete("someone-else", session_id)


async def test_feedback_before_saying_anything_is_refused() -> None:
    llm = FakeLlm()
    service, _ = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    with pytest.raises(ConversationTooShortError):
        await service.finish(USER, session_id, NOW)
    assert llm.json_calls == []


async def test_finishing_stores_tidied_feedback_and_never_pays_twice() -> None:
    llm = FakeLlm()
    service, _ = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id
    await service.send(USER, session_id, "I want coffee")
    llm.json_answers = [_feedback(score=140)]

    later = NOW + timedelta(minutes=5)
    detail = await service.finish(USER, session_id, later)

    assert detail.status is ConversationStatus.FINISHED
    assert detail.finished_at == later
    assert detail.feedback is not None
    assert detail.feedback.score == 100
    assert detail.feedback.summary_vi == "Tốt lắm."
    assert detail.feedback.new_words == ["latte", "takeaway"]
    assert llm.json_calls[0]["model"] == settings.deepseek_feedback_model
    assert "LEARNER: I want coffee" in llm.json_calls[0]["prompt"]

    again = await service.finish(USER, session_id, later)
    assert again.feedback == detail.feedback
    assert len(llm.json_calls) == 1

    with pytest.raises(ConversationClosedError):
        await service.send(USER, session_id, "more")

    history = await service.list_conversations(USER, limit=20, before=None)
    assert [(h.id, h.score) for h in history] == [(session_id, 100)]


async def test_the_first_finish_counts_toward_the_daily_quest_and_a_repeat_does_not() -> None:
    llm = FakeLlm()
    quests = FakeQuestTracker()
    service, _ = _service(llm, quests=quests)
    session_id = (await service.start(USER, "cafe_order", NOW)).id
    await service.send(USER, session_id, "I want coffee")
    llm.json_answers = [_feedback()]

    await service.finish(USER, session_id, NOW)
    await service.finish(USER, session_id, NOW)

    assert quests.events == [(USER, QuestEvent(ai_conversations=1))]


async def test_open_conversation_feedback_has_no_goal_verdict() -> None:
    llm = FakeLlm()
    service, _ = _service(llm)
    session_id = (await service.start(USER, "free_talk", NOW)).id
    await service.send(USER, session_id, "My week was busy.")
    llm.json_answers = [_feedback()]

    detail = await service.finish(USER, session_id, NOW)

    assert detail.feedback is not None and detail.feedback.goal_completed is None


async def test_a_translation_is_asked_for_once() -> None:
    llm = FakeLlm()
    service, conversations = _service(llm)
    detail = await service.start(USER, "cafe_order", NOW)
    opening_id = detail.messages[0].id

    llm.replies = ["Xin chào! Bạn muốn gọi gì?"]
    first = await service.translate(USER, detail.id, opening_id)
    second = await service.translate(USER, detail.id, opening_id)

    assert first.translation_vi == second.translation_vi == "Xin chào! Bạn muốn gọi gì?"
    assert len(llm.text_calls) == 1
    assert conversations.saves == 1

    with pytest.raises(ConversationNotFoundError):
        await service.translate(USER, detail.id, "missing")


async def test_hints_are_capped_and_blank_ones_dropped() -> None:
    llm = FakeLlm()
    service, _ = _service(llm)
    session_id = (await service.start(USER, "cafe_order", NOW)).id
    llm.json_answers = [
        HintList(
            suggestions=[
                HintSuggestion(en=" ", vi=""),
                *[HintSuggestion(en=f"Line {i}", vi=f"Câu {i}") for i in range(5)],
            ]
        )
    ]

    hints = await service.hint(USER, session_id)

    assert [h.en for h in hints.suggestions] == ["Line 0", "Line 1", "Line 2"]


async def test_deleting_removes_the_conversation() -> None:
    service, conversations = _service(FakeLlm())
    session_id = (await service.start(USER, "cafe_order", NOW)).id

    await service.delete(USER, session_id)

    assert conversations.sessions == {}
