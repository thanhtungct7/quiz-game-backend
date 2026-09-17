"""Talk to the configured DeepSeek model once, to check the key and model names.

    conda run -n backend python scripts/try_llm.py

Reads DEEPSEEK_* from .env. Plays two turns of the café scenario, then asks for
feedback, printing what comes back -- no database involved.
"""

import asyncio
from datetime import UTC, datetime

from app.core.config import settings
from app.models.conversation.conversation_message import ConversationMessage, MessageRole
from app.services.ai.llm_client import build_llm_client
from app.services.conversation import prompts
from app.services.conversation.scenarios import get_scenario


async def main() -> None:
    llm = build_llm_client(settings)
    scenario = get_scenario("cafe_order")
    if llm is None or scenario is None:
        raise SystemExit("DEEPSEEK_API_KEY is not set in .env")

    now = datetime.now(UTC)
    messages = [
        ConversationMessage(seq=1, role=MessageRole.ASSISTANT, content=scenario.opening_line)
    ]
    print(f"AI: {scenario.opening_line}")
    for line in ["Hello, I want one latte and a cake please.", "How much it cost?"]:
        print(f"YOU: {line}")
        messages.append(
            ConversationMessage(seq=len(messages) + 1, role=MessageRole.USER, content=line)
        )
        reply = await llm.generate_text(
            model=settings.deepseek_chat_model,
            system=prompts.chat_system_prompt(scenario, "A2"),
            turns=prompts.to_chat_turns(messages),
            max_output_tokens=1024,
        )
        print(f"AI ({settings.deepseek_chat_model}): {reply}")
        messages.append(
            ConversationMessage(seq=len(messages) + 1, role=MessageRole.ASSISTANT, content=reply)
        )

    feedback = await llm.generate_json(
        model=settings.deepseek_feedback_model,
        system=prompts.FEEDBACK_SYSTEM_PROMPT,
        prompt=prompts.feedback_prompt(scenario, "A2", messages),
        schema=prompts.ConversationFeedback,
        max_output_tokens=4096,
    )
    print(f"\nFEEDBACK ({settings.deepseek_feedback_model}, {datetime.now(UTC) - now}):")
    print(feedback.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
