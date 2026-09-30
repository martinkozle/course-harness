"""Local traces of Course Agent runs, kept for debugging outside the Course Workspace.

Each Conversation gets one JSON Lines file. A model request record keeps only the messages
that differ from the previous request in the same Conversation, and says how many earlier
messages were reused unchanged. When ``history_rewritten``, ``instructions_changed``, or
``tools_changed`` is set, the provider could not reuse its prompt cache for the whole
conversation so far, which is the usual reason a local model spends a long time on prompt
processing. Tool call records show where a run waited on its own tools.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    WrapModelRequestHandler,
    WrapRunHandler,
    WrapToolExecuteHandler,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
)
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.run import AgentRunResult
from pydantic_ai.tools import ToolDefinition

logger = logging.getLogger(__name__)

TRACE_RETENTION_SECONDS = 14 * 24 * 60 * 60


def prune_traces(traces_dir: Path, *, now: float | None = None) -> None:
    """Remove traces of Conversations that have been idle for the retention period."""
    cutoff = (now if now is not None else time.time()) - TRACE_RETENTION_SECONDS
    try:
        candidates = list(traces_dir.glob("*.json*"))
    except OSError:
        return
    for path in candidates:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _without_binary_data(value: Any) -> Any:
    """Replace inline file bytes, which would bloat a trace, with their size."""
    if isinstance(value, dict):
        if value.get("kind") == "binary" and isinstance(value.get("data"), str):
            return {**value, "data": f"<{len(value['data'])} base64 characters omitted>"}
        return {key: _without_binary_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_binary_data(item) for item in value]
    return value


def _dump_messages(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    dumped = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    for message in dumped:
        # Only the latest request's instructions are sent; they are traced on their own.
        message.pop("instructions", None)
    return _without_binary_data(dumped)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _tool_definitions(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [dataclasses.asdict(tool) for tool in tools]


@dataclass
class AgentTrace(AbstractCapability[Any]):
    """Record one Conversation's model requests and tool calls to ``path``."""

    path: Path
    _last: dict[str, Any] | None = field(default=None, init=False, repr=False)

    @property
    def _state_path(self) -> Path:
        return self.path.with_suffix(".state.json")

    def _write(self, record: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as trace:
                trace.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")
        except OSError as error:
            logger.warning("Agent trace could not be written to %s: %s", self.path, error)

    def _previous(self) -> dict[str, Any]:
        if self._last is None:
            try:
                self._last = json.loads(self._state_path.read_text(encoding="utf-8"))
            except OSError, ValueError:
                self._last = {}
        return self._last

    def _remember(self, state: dict[str, Any]) -> None:
        self._last = state
        try:
            self._state_path.write_text(json.dumps(state), encoding="utf-8")
        except OSError as error:
            logger.warning("Agent trace state could not be saved: %s", error)

    async def wrap_run(
        self, ctx: RunContext[Any], *, handler: WrapRunHandler
    ) -> AgentRunResult[Any]:
        started = time.monotonic()
        self._write(
            {
                "event": "run_start",
                "at": _now(),
                "run_id": ctx.run_id,
                "conversation_id": ctx.conversation_id,
                "agent": ctx.agent.name if ctx.agent is not None else None,
                "model": ctx.model.model_name,
                "provider": ctx.model.system,
            }
        )
        status, error = "completed", None
        try:
            return await handler()
        except BaseException as caught:
            status = "cancelled" if not isinstance(caught, Exception) else "failed"
            error = repr(caught)
            raise
        finally:
            self._write(
                {
                    "event": "run_end",
                    "at": _now(),
                    "run_id": ctx.run_id,
                    "status": status,
                    "error": error,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                }
            )

    async def wrap_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        record: dict[str, Any] = {"event": "model_request", "at": _now(), "run_id": ctx.run_id}
        started = time.monotonic()
        try:
            record.update(self._describe_request(ctx, request_context))
        except Exception as error:  # A trace must never stop a run.
            logger.warning("Agent trace could not describe a model request: %s", error)
        try:
            response = await handler(request_context)
        except BaseException as caught:
            record["error"] = repr(caught)
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            self._write(record)
            raise
        record["duration_ms"] = round((time.monotonic() - started) * 1000)
        try:
            record["response"] = _dump_messages([response])[0]
            record["usage"] = dataclasses.asdict(response.usage)
        except Exception as error:
            logger.warning("Agent trace could not describe a model response: %s", error)
        self._write(record)
        return response

    def _describe_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> dict[str, Any]:
        messages = request_context.messages
        dumped = _dump_messages(messages)
        hashes = [_digest(message) for message in dumped]
        latest = messages[-1] if messages else None
        instructions = latest.instructions if isinstance(latest, ModelRequest) else None
        parameters = request_context.model_request_parameters
        tools = _tool_definitions([*parameters.function_tools, *parameters.output_tools])
        instructions_hash, tools_hash = _digest(instructions), _digest(tools)

        previous = self._previous()
        previous_hashes: list[str] = previous.get("message_hashes", [])
        reused = 0
        for before, now in zip(previous_hashes, hashes, strict=False):
            if before != now:
                break
            reused += 1
        instructions_changed = previous.get("instructions") != instructions_hash
        tools_changed = previous.get("tools") != tools_hash
        self._remember(
            {"message_hashes": hashes, "instructions": instructions_hash, "tools": tools_hash}
        )
        description: dict[str, Any] = {
            "step": ctx.run_step,
            "model": request_context.model.model_name,
            "provider": request_context.model.system,
            "message_count": len(messages),
            "reused_messages": reused,
            "history_rewritten": bool(previous_hashes) and reused < len(previous_hashes),
            "instructions_changed": bool(previous) and instructions_changed,
            "tools_changed": bool(previous) and tools_changed,
            "settings": request_context.model_settings,
            "new_messages": dumped[reused:],
        }
        if instructions_changed:
            description["instructions"] = instructions
        if tools_changed:
            description["tools"] = tools
        return description

    async def wrap_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: Any,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        record: dict[str, Any] = {
            "event": "tool_call",
            "at": _now(),
            "run_id": ctx.run_id,
            "tool": call.tool_name,
            "tool_call_id": call.tool_call_id,
        }
        started = time.monotonic()
        try:
            return await handler(args)
        except BaseException as caught:
            record["error"] = repr(caught)
            raise
        finally:
            record["duration_ms"] = round((time.monotonic() - started) * 1000)
            self._write(record)
