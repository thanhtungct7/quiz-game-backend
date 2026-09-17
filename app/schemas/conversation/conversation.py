from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.models.conversation.conversation_message import MessageRole
from app.models.conversation.conversation_session import ConversationStatus
from app.services.conversation.prompts import ConversationFeedback, HintSuggestion

MAX_MESSAGE_LENGTH = 500


class ScenarioRead(BaseModel):
    code: str
    title_vi: str
    category_vi: str
    suggested_levels: list[str]
    setting: str
    ai_role: str
    user_role: str
    goal_vi: str | None
    useful_phrases: list[str]
    max_turns: int


class ConversationStartRequest(BaseModel):
    scenario_code: str = Field(min_length=1, max_length=50)


class MessageSendRequest(BaseModel):
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Message must not be blank")
        return stripped


class MessageRead(BaseModel):
    id: str
    seq: int
    role: MessageRole
    content: str
    translation_vi: str | None
    created_at: datetime


class ConversationSummaryRead(BaseModel):
    id: str
    scenario_code: str
    title_vi: str
    status: ConversationStatus
    user_turns: int
    max_turns: int
    score: int | None
    started_at: datetime
    finished_at: datetime | None


class ConversationDetailRead(BaseModel):
    id: str
    scenario: ScenarioRead
    cefr: str
    status: ConversationStatus
    user_turns: int
    max_turns: int
    # The AI marked the goal done, or the turns ran out: time to ask for feedback.
    should_finish: bool
    # The last message is the learner's and has no reply: offer to retry it.
    awaiting_reply: bool
    messages: list[MessageRead]
    feedback: ConversationFeedback | None
    started_at: datetime
    finished_at: datetime | None


class TurnRead(BaseModel):
    user_message: MessageRead
    assistant_message: MessageRead
    user_turns: int
    max_turns: int
    should_finish: bool


class HintRead(BaseModel):
    suggestions: list[HintSuggestion]


class TranslationRead(BaseModel):
    message_id: str
    translation_vi: str
