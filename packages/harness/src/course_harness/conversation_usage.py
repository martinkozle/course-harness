"""How many tokens a Conversation has used, and roughly what they cost.

Running totals are kept beside the Conversation and updated after every model response, so the
chat can show how full the context is and what the Conversation has cost so far. Costs are
estimates from genai-prices, the price data Pydantic AI uses.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, WrapModelRequestHandler
from pydantic_ai.messages import ModelResponse
from pydantic_ai.models import ModelRequestContext

logger = logging.getLogger(__name__)


class ConversationUsage(BaseModel):
    """Token totals of every model response in a Conversation.

    ``input_tokens`` includes the cached tokens counted in ``cache_read_tokens`` and
    ``cache_write_tokens``. ``context_tokens`` is the size of the latest exchange, the input of the
    latest request plus its response, which is what the next request starts from.
    """

    requests: int = 0
    context_tokens: int | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    unpriced_requests: int = 0


def read_usage(path: Path) -> ConversationUsage:
    try:
        return ConversationUsage.model_validate_json(path.read_bytes())
    except OSError, ValidationError:
        return ConversationUsage()


def write_usage(path: Path, usage: ConversationUsage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(usage.model_dump(mode="json")), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def forget_context(path: Path) -> None:
    """After compaction, the latest exchange no longer says how large the context is."""
    if path.exists():
        write_usage(path, read_usage(path).model_copy(update={"context_tokens": None}))


def response_cost(response: ModelResponse, price_model: str | None = None) -> float | None:
    """The estimated price of one response in US dollars, or ``None`` if its model is unknown.

    ``price_model`` names the model to price when the response's model name does not, such as a
    Bedrock application inference profile ARN.
    """
    priced = dataclasses.replace(response, model_name=price_model) if price_model else response
    try:
        return float(priced.cost().total_price)
    except AssertionError, LookupError, ValueError:
        return None


def add_response(
    usage: ConversationUsage, response: ModelResponse, price_model: str | None = None
) -> ConversationUsage:
    tokens = response.usage
    cost = response_cost(response, price_model)
    return ConversationUsage(
        requests=usage.requests + 1,
        context_tokens=tokens.input_tokens + tokens.output_tokens,
        input_tokens=usage.input_tokens + tokens.input_tokens,
        cache_read_tokens=usage.cache_read_tokens + tokens.cache_read_tokens,
        cache_write_tokens=usage.cache_write_tokens + tokens.cache_write_tokens,
        output_tokens=usage.output_tokens + tokens.output_tokens,
        cost_usd=(usage.cost_usd or 0) + cost if cost is not None else usage.cost_usd,
        unpriced_requests=usage.unpriced_requests + (cost is None),
    )


@dataclasses.dataclass
class TrackConversationUsage(AbstractCapability[Any]):
    """Add each model response of a run to its Conversation's usage."""

    path: Path
    price_model: str | None = None

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        response = await handler(request_context)
        try:
            write_usage(self.path, add_response(read_usage(self.path), response, self.price_model))
        except (OSError, ValueError) as error:  # Usage must never stop a run.
            logger.warning("Conversation usage could not be saved: %s", error)
        return response
