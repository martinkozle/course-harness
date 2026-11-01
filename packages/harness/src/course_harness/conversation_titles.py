"""Short Conversation titles proposed by the configured model from the first message."""

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings

from course_harness.chat_history import clean_generated_title

TITLE_INSTRUCTIONS = (
    "You name conversations between a Course Author and an assistant that plans and "
    "builds courses. Reply with only a title of 3 to 6 words that captures what the "
    "first message asks for. Use the message's language. No quotes, no trailing "
    "punctuation, no preamble."
)
MAX_PROMPT_CHARACTERS = 2000

_title_agent = Agent(output_type=str, instructions=TITLE_INSTRUCTIONS)


async def generate_conversation_title(model: Model, first_message: str) -> str:
    """Return a cleaned title, or an empty string when the model gave nothing usable."""
    result = await _title_agent.run(
        first_message[:MAX_PROMPT_CHARACTERS],
        model=model,
        model_settings=ModelSettings(temperature=0.2, max_tokens=512),
    )
    return clean_generated_title(result.output)
