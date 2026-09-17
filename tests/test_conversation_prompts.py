from app.models.conversation.conversation_message import ConversationMessage, MessageRole
from app.services.ai.llm_client import CONVERSATION_START, ChatTurn, normalize_turns
from app.services.conversation import prompts
from app.services.conversation.scenarios import SCENARIOS, get_scenario


def _message(seq: int, role: MessageRole, content: str) -> ConversationMessage:
    return ConversationMessage(id=f"m{seq}", session_id="s", seq=seq, role=role, content=content)


def test_scenario_codes_are_unique_and_every_goal_has_its_translation() -> None:
    codes = [scenario.code for scenario in SCENARIOS]
    assert len(codes) == len(set(codes))
    for scenario in SCENARIOS:
        assert (scenario.goal is None) == (scenario.goal_vi is None), scenario.code
        assert scenario.opening_line and scenario.useful_phrases


def test_chat_prompt_carries_role_goal_and_level() -> None:
    scenario = get_scenario("doctor_visit")
    assert scenario is not None

    system = prompts.chat_system_prompt(scenario, "B1")

    assert scenario.ai_role in system
    assert scenario.goal is not None and scenario.goal in system
    assert "CEFR B1" in system
    assert prompts.GOAL_DONE_MARKER in system


def test_open_conversation_prompt_has_no_goal_marker() -> None:
    scenario = get_scenario("free_talk")
    assert scenario is not None

    system = prompts.chat_system_prompt(scenario, "A2")

    assert prompts.GOAL_DONE_MARKER not in system
    assert "No fixed goal" in system


def test_goal_marker_is_found_in_any_spacing_or_case() -> None:
    assert prompts.split_goal_marker("Bye! [[GOAL_DONE]]") == ("Bye!", True)
    assert prompts.split_goal_marker("Bye! [[ goal_done ]]\n") == ("Bye!", True)
    assert prompts.split_goal_marker("See you soon.") == ("See you soon.", False)


def test_only_recent_history_is_sent() -> None:
    messages = [
        _message(i, MessageRole.USER if i % 2 else MessageRole.ASSISTANT, f"line {i}")
        for i in range(1, 31)
    ]

    turns = prompts.to_chat_turns(messages)

    assert len(turns) == prompts.HISTORY_MESSAGES
    assert turns[-1] == ChatTurn("model", "line 30")


def test_transcript_labels_speakers() -> None:
    scenario = get_scenario("cafe_order")
    assert scenario is not None
    messages = [
        _message(1, MessageRole.ASSISTANT, "Hi!"),
        _message(2, MessageRole.USER, "A tea, please."),
    ]

    assert prompts.transcript(messages) == "AI: Hi!\nLEARNER: A tea, please."


def test_turns_open_with_the_user_and_alternate() -> None:
    turns = normalize_turns(
        [
            ChatTurn("model", "Hi!"),
            ChatTurn("user", "Tea."),
            ChatTurn("user", "Hello?"),
        ]
    )

    assert turns == [
        ChatTurn("user", CONVERSATION_START),
        ChatTurn("model", "Hi!"),
        ChatTurn("user", "Tea.\nHello?"),
    ]
