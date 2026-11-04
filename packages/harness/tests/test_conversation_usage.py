from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.usage import RequestUsage

from course_harness.conversation_usage import (
    ConversationUsage,
    add_response,
    forget_context,
    read_usage,
    write_usage,
)

ARN = "arn:aws:bedrock:eu-central-1:123456789012:application-inference-profile/abc"


def _response(model: str, provider: str, **tokens: int) -> ModelResponse:
    usage = RequestUsage()
    for name, count in tokens.items():
        setattr(usage, name, count)
    return ModelResponse(
        parts=[TextPart("ok")],
        usage=usage,
        model_name=model,
        provider_name=provider,
    )


def test_usage_totals_responses_and_prices_the_model_behind_an_arn() -> None:
    first = _response(
        ARN, "bedrock", input_tokens=10_000, cache_write_tokens=9_000, output_tokens=500
    )
    second = _response(
        ARN, "bedrock", input_tokens=11_000, cache_read_tokens=9_000, output_tokens=700
    )

    usage = add_response(ConversationUsage(), first, "anthropic.claude-haiku-5-5")
    usage = add_response(usage, second, "anthropic.claude-haiku-5-5")

    assert usage.requests == 2
    assert usage.context_tokens == 11_700
    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_write_tokens) == (
        21_000,
        9_000,
        9_000,
    )
    assert usage.output_tokens == 1_200
    assert usage.cost_usd is not None and usage.cost_usd > 0
    assert usage.unpriced_requests == 0


def test_unknown_models_are_counted_but_not_priced() -> None:
    priced = add_response(
        ConversationUsage(),
        _response("claude-haiku-5-5", "anthropic", input_tokens=1_000, output_tokens=100),
    )
    mixed = add_response(priced, _response(ARN, "bedrock", input_tokens=2_000, output_tokens=100))

    assert mixed.cost_usd == priced.cost_usd
    assert mixed.unpriced_requests == 1
    assert mixed.requests == 2


def test_compaction_forgets_the_context_size_but_keeps_totals(tmp_path: Path) -> None:
    path = tmp_path / "usage" / "conversation.json"
    write_usage(path, ConversationUsage(requests=3, context_tokens=90_000, output_tokens=4_000))

    forget_context(path)
    forget_context(tmp_path / "usage" / "missing.json")

    assert read_usage(path) == ConversationUsage(requests=3, output_tokens=4_000)
    assert not (tmp_path / "usage" / "missing.json").exists()
    assert read_usage(tmp_path / "nothing.json") == ConversationUsage()
