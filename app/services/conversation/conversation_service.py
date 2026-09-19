"""AI conversation practice: a learner talks through an everyday scenario with
the AI in a role, then gets feedback on how it went.

Every AI call is on demand -- starting a conversation costs nothing (the
opening line is written in the scenario), and feedback and translations are
stored the first time so asking again is free.

A learner's line is committed before the AI is asked for a reply. If that call
fails the line stays, the conversation reads as awaiting a reply, and `retry`
asks again for the same line rather than the client sending it twice.
"""

from datetime import datetime, time

from app.core.config import Settings
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
from app.repository.conversation.conversation_repository import ConversationRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.schemas.conversation.conversation import (
    ConversationDetailRead,
    ConversationSummaryRead,
    HintRead,
    MessageRead,
    ScenarioRead,
    TranslationRead,
    TurnRead,
)
from app.services.ai.llm_client import ChatTurn, LlmClient
from app.services.conversation import prompts
from app.services.conversation.scenarios import SCENARIOS, Scenario, get_scenario
from app.services.game.cefr import CefrBand, cefr_for_level
from app.services.game.daily_quest_service import DailyQuestTracker
from app.services.game.daily_quests import QuestEvent
from app.services.game.leveling import effective_level
from app.services.game.streak import STREAK_TZ, today_in_streak_tz

CHAT_MAX_OUTPUT_TOKENS = 1024
FEEDBACK_MAX_OUTPUT_TOKENS = 4096
HINT_MAX_OUTPUT_TOKENS = 1024
TRANSLATE_MAX_OUTPUT_TOKENS = 1024


class ConversationService:
    def __init__(
        self,
        *,
        conversations: ConversationRepository,
        profiles: GameProfileRepository,
        llm: LlmClient | None,
        config: Settings,
        quests: DailyQuestTracker | None = None,
    ) -> None:
        self.conversations = conversations
        self.profiles = profiles
        self.llm = llm
        self.config = config
        self.quests = quests

    def list_scenarios(self) -> list[ScenarioRead]:
        return [_scenario_read(scenario) for scenario in SCENARIOS]

    async def start(
        self, user_id: str, scenario_code: str, now: datetime
    ) -> ConversationDetailRead:
        scenario = get_scenario(scenario_code)
        if scenario is None:
            raise ScenarioNotFoundError("No such scenario")
        # Nothing here calls the AI, but a conversation that cannot get a single
        # reply is not worth opening.
        self._require_llm()
        start_of_day = datetime.combine(today_in_streak_tz(now), time.min, tzinfo=STREAK_TZ)
        started_today = await self.conversations.count_started_since(user_id, start_of_day)
        if started_today >= self.config.conversation_sessions_per_day:
            raise DailyConversationLimitError(
                "You have used today's conversations; come back tomorrow"
            )

        session = ConversationSession(
            user_id=user_id,
            scenario_code=scenario.code,
            cefr=(await self._learner_cefr(user_id)).value,
            status=ConversationStatus.ACTIVE,
            user_turns=0,
            goal_reached=False,
            started_at=now,
        )
        opening = ConversationMessage(
            seq=1, role=MessageRole.ASSISTANT, content=scenario.opening_line, created_at=now
        )
        session = await self.conversations.create_session(session, opening)
        return await self._detail(session, scenario)

    async def list_conversations(
        self, user_id: str, *, limit: int, before: datetime | None
    ) -> list[ConversationSummaryRead]:
        sessions = await self.conversations.list_sessions(user_id, limit=limit, before=before)
        summaries = []
        for session in sessions:
            scenario = get_scenario(session.scenario_code)
            summaries.append(
                ConversationSummaryRead(
                    id=session.id,
                    scenario_code=session.scenario_code,
                    title_vi=scenario.title_vi if scenario else session.scenario_code,
                    status=session.status,
                    user_turns=session.user_turns,
                    max_turns=scenario.max_turns if scenario else session.user_turns,
                    score=session.score,
                    started_at=session.started_at,
                    finished_at=session.finished_at,
                )
            )
        return summaries

    async def get(self, user_id: str, session_id: str) -> ConversationDetailRead:
        session, scenario = await self._load(user_id, session_id)
        return await self._detail(session, scenario)

    async def send(self, user_id: str, session_id: str, content: str) -> TurnRead:
        session, scenario = await self._load(user_id, session_id)
        llm = self._require_llm()
        if _is_closed(session, scenario):
            raise ConversationClosedError("This conversation has ended")
        messages = await self.conversations.messages(session.id)
        if messages and messages[-1].role is MessageRole.USER:
            raise ConversationAwaitingReplyError("The last message has no reply yet; retry it")

        session.user_turns += 1
        user_message = await self.conversations.add_message(
            session, MessageRole.USER, content, seq=_next_seq(messages)
        )
        messages.append(user_message)
        return await self._reply(llm, session, scenario, messages, user_message)

    async def retry(self, user_id: str, session_id: str) -> TurnRead:
        session, scenario = await self._load(user_id, session_id)
        llm = self._require_llm()
        if session.status is ConversationStatus.FINISHED:
            raise ConversationClosedError("This conversation has ended")
        messages = await self.conversations.messages(session.id)
        if not messages or messages[-1].role is not MessageRole.USER:
            raise ConversationClosedError("There is no message waiting for a reply")
        return await self._reply(llm, session, scenario, messages, messages[-1])

    async def hint(self, user_id: str, session_id: str) -> HintRead:
        session, scenario = await self._load(user_id, session_id)
        llm = self._require_llm()
        if _is_closed(session, scenario):
            raise ConversationClosedError("This conversation has ended")
        messages = await self.conversations.messages(session.id)
        hints = await llm.generate_json(
            model=self.config.deepseek_chat_model,
            system=prompts.HINT_SYSTEM_PROMPT,
            prompt=prompts.hint_prompt(scenario, session.cefr, messages),
            schema=prompts.HintList,
            max_output_tokens=HINT_MAX_OUTPUT_TOKENS,
        )
        suggestions = [s for s in hints.suggestions if s.en.strip()][: prompts.HINT_COUNT]
        if not suggestions:
            raise AiUnavailableError("The AI returned no suggestions")
        return HintRead(suggestions=suggestions)

    async def translate(self, user_id: str, session_id: str, message_id: str) -> TranslationRead:
        session, _ = await self._load(user_id, session_id)
        message = await self.conversations.get_message(session.id, message_id)
        if message is None:
            raise ConversationNotFoundError("No such message")
        if message.translation_vi is None:
            llm = self._require_llm()
            message.translation_vi = await llm.generate_text(
                model=self.config.deepseek_chat_model,
                system=prompts.TRANSLATE_SYSTEM_PROMPT,
                turns=[ChatTurn("user", message.content)],
                max_output_tokens=TRANSLATE_MAX_OUTPUT_TOKENS,
            )
            await self.conversations.save()
        return TranslationRead(message_id=message.id, translation_vi=message.translation_vi)

    async def finish(self, user_id: str, session_id: str, now: datetime) -> ConversationDetailRead:
        session, scenario = await self._load(user_id, session_id)
        if session.feedback is not None:
            return await self._detail(session, scenario)
        messages = await self.conversations.messages(session.id)
        if not any(message.role is MessageRole.USER for message in messages):
            raise ConversationTooShortError("Say something before asking for feedback")
        llm = self._require_llm()
        feedback = await llm.generate_json(
            model=self.config.deepseek_feedback_model,
            system=prompts.FEEDBACK_SYSTEM_PROMPT,
            prompt=prompts.feedback_prompt(scenario, session.cefr, messages),
            schema=prompts.ConversationFeedback,
            max_output_tokens=FEEDBACK_MAX_OUTPUT_TOKENS,
        )
        feedback = prompts.tidy_feedback(feedback, has_goal=scenario.goal is not None)
        session.feedback = feedback.model_dump(mode="json")
        session.score = feedback.score
        session.status = ConversationStatus.FINISHED
        session.finished_at = now
        await self.conversations.save()
        # Only the first finish gets here -- a repeat returns early above.
        if self.quests is not None:
            await self.quests.track_quietly(user_id, QuestEvent(ai_conversations=1), now)
        return await self._detail(session, scenario, messages)

    async def delete(self, user_id: str, session_id: str) -> None:
        session, _ = await self._load(user_id, session_id)
        await self.conversations.delete_session(session)

    async def _reply(
        self,
        llm: LlmClient,
        session: ConversationSession,
        scenario: Scenario,
        messages: list[ConversationMessage],
        user_message: ConversationMessage,
    ) -> TurnRead:
        raw = await llm.generate_text(
            model=self.config.deepseek_chat_model,
            system=prompts.chat_system_prompt(scenario, session.cefr),
            turns=prompts.to_chat_turns(messages),
            max_output_tokens=CHAT_MAX_OUTPUT_TOKENS,
        )
        reply, goal_done = prompts.split_goal_marker(raw)
        if not reply:
            raise AiUnavailableError("The AI returned an empty reply")
        if goal_done and scenario.goal is not None:
            session.goal_reached = True
        assistant_message = await self.conversations.add_message(
            session, MessageRole.ASSISTANT, reply, seq=_next_seq(messages)
        )
        return TurnRead(
            user_message=_message_read(user_message),
            assistant_message=_message_read(assistant_message),
            user_turns=session.user_turns,
            max_turns=scenario.max_turns,
            should_finish=_should_finish(session, scenario),
        )

    async def _load(self, user_id: str, session_id: str) -> tuple[ConversationSession, Scenario]:
        session = await self.conversations.get_session(user_id, session_id)
        if session is None:
            raise ConversationNotFoundError("No such conversation")
        scenario = get_scenario(session.scenario_code)
        if scenario is None:
            # A scenario removed from the catalog leaves its old conversations unreadable
            # rather than half-working.
            raise ConversationNotFoundError("This conversation's scenario no longer exists")
        return session, scenario

    async def _detail(
        self,
        session: ConversationSession,
        scenario: Scenario,
        messages: list[ConversationMessage] | None = None,
    ) -> ConversationDetailRead:
        if messages is None:
            messages = await self.conversations.messages(session.id)
        return ConversationDetailRead(
            id=session.id,
            scenario=_scenario_read(scenario),
            cefr=session.cefr,
            status=session.status,
            user_turns=session.user_turns,
            max_turns=scenario.max_turns,
            should_finish=_should_finish(session, scenario),
            awaiting_reply=bool(messages) and messages[-1].role is MessageRole.USER,
            messages=[_message_read(message) for message in messages],
            feedback=(
                prompts.ConversationFeedback.model_validate(session.feedback)
                if session.feedback is not None
                else None
            ),
            started_at=session.started_at,
            finished_at=session.finished_at,
        )

    async def _learner_cefr(self, user_id: str) -> CefrBand:
        profile = await self.profiles.get_by_user(user_id)
        if profile is None:
            return CefrBand.A1
        return cefr_for_level(effective_level(profile.total_exp, profile.benchmark_cleared_level))

    def _require_llm(self) -> LlmClient:
        if self.llm is None:
            raise AiUnavailableError("AI conversation is not available right now")
        return self.llm


def _is_closed(session: ConversationSession, scenario: Scenario) -> bool:
    """No more lines can be said: finished, or out of turns."""
    return session.status is ConversationStatus.FINISHED or session.user_turns >= scenario.max_turns


def _should_finish(session: ConversationSession, scenario: Scenario) -> bool:
    return session.goal_reached or _is_closed(session, scenario)


def _next_seq(messages: list[ConversationMessage]) -> int:
    return messages[-1].seq + 1 if messages else 1


def _scenario_read(scenario: Scenario) -> ScenarioRead:
    return ScenarioRead(
        code=scenario.code,
        title_vi=scenario.title_vi,
        category_vi=scenario.category_vi,
        suggested_levels=[level.value for level in scenario.suggested_levels],
        setting=scenario.setting,
        ai_role=scenario.ai_role,
        user_role=scenario.user_role,
        goal_vi=scenario.goal_vi,
        useful_phrases=list(scenario.useful_phrases),
        max_turns=scenario.max_turns,
    )


def _message_read(message: ConversationMessage) -> MessageRead:
    return MessageRead(
        id=message.id,
        seq=message.seq,
        role=message.role,
        content=message.content,
        translation_vi=message.translation_vi,
        created_at=message.created_at,
    )
