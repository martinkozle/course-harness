"""Private, Workspace-scoped Course Agent conversation storage."""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.ui.ag_ui import AGUIAdapter

from course_harness.workspaces import workspace_identity


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    role: Literal["user", "assistant"]
    content: str


class PendingApproval(BaseModel):
    id: str
    message: str


class ChatTranscript(BaseModel):
    messages: list[ChatMessage]
    approval: PendingApproval | None = None


class ConversationSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    archived: bool = False
    has_pending_approval: bool = False


class ConversationCatalog(BaseModel):
    active_id: str
    conversations: list[ConversationSummary]


class CompactionPreview(BaseModel):
    summary: str
    source_message_count: int
    revision: str


class ConversationConflict(ValueError):
    pass


def chat_session_path(store_path: Path, workspace: Path) -> Path:
    """Location of the pre-threading chat file, retained for one-off migration."""
    return store_path / f"{workspace_identity(workspace)}.json"


def _directory(store_path: Path, workspace: Path) -> Path:
    return store_path / workspace_identity(workspace)


def _index_path(store_path: Path, workspace: Path) -> Path:
    return _directory(store_path, workspace) / "index.json"


def _thread_path(store_path: Path, workspace: Path, conversation_id: str) -> Path:
    return _directory(store_path, workspace) / f"{conversation_id}.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _empty_thread() -> dict[str, object]:
    return {
        "version": 2,
        "history": [],
        "messages": [],
        "retained_messages": [],
        "summary": None,
        "compactions": [],
    }


def _catalog(store_path: Path, workspace: Path) -> dict[str, object]:
    path = _index_path(store_path, workspace)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    legacy = chat_session_path(store_path, workspace)
    conversation_id = uuid4().hex
    timestamp = _now()
    metadata = ConversationSummary(
        id=conversation_id,
        title="Conversation 1",
        created_at=timestamp,
        updated_at=timestamp,
    )
    if legacy.is_file():
        payload = json.loads(legacy.read_text(encoding="utf-8"))
        history = ModelMessagesTypeAdapter.validate_python(payload["history"])
        thread = _empty_thread()
        thread["history"] = ModelMessagesTypeAdapter.dump_python(history, mode="json")
        thread["messages"] = ChatTranscript.model_validate(
            {"messages": payload["messages"]}
        ).model_dump(mode="json")["messages"]
        metadata.has_pending_approval = _pending_approval(history) is not None
    else:
        thread = _empty_thread()
    result: dict[str, object] = {
        "version": 2,
        "active_id": conversation_id,
        "conversations": [metadata.model_dump(mode="json")],
    }
    _atomic_write(_thread_path(store_path, workspace, conversation_id), thread)
    _atomic_write(path, result)
    legacy.unlink(missing_ok=True)
    return result


def _summaries(index: dict[str, object]) -> list[ConversationSummary]:
    return [
        ConversationSummary.model_validate(item)
        for item in cast(list[object], index["conversations"])
    ]


def _find(index: dict[str, object], conversation_id: str) -> ConversationSummary:
    for item in _summaries(index):
        if item.id == conversation_id:
            return item
    raise KeyError(conversation_id)


def _read_thread(store_path: Path, workspace: Path, conversation_id: str) -> dict[str, object]:
    _find(_catalog(store_path, workspace), conversation_id)
    return json.loads(
        _thread_path(store_path, workspace, conversation_id).read_text(encoding="utf-8")
    )


def _update_metadata(index: dict[str, object], updated: ConversationSummary) -> None:
    index["conversations"] = [
        updated.model_dump(mode="json") if item.id == updated.id else item.model_dump(mode="json")
        for item in _summaries(index)
    ]


def list_conversations(store_path: Path, workspace: Path) -> ConversationCatalog:
    index = _catalog(store_path, workspace)
    summaries = _summaries(index)
    for item in summaries:
        item.has_pending_approval = _has_pending_approval(store_path, workspace, item.id)
    return ConversationCatalog(
        active_id=str(index["active_id"]),
        conversations=sorted(summaries, key=lambda item: item.updated_at, reverse=True),
    )


def active_conversation_id(store_path: Path, workspace: Path) -> str:
    return list_conversations(store_path, workspace).active_id


def create_conversation(
    store_path: Path, workspace: Path, title: str | None = None
) -> ConversationCatalog:
    index = _catalog(store_path, workspace)
    conversation_id = uuid4().hex
    timestamp = _now()
    label = title.strip() if title else f"Conversation {len(_summaries(index)) + 1}"
    if not label:
        raise ValueError("Conversation title cannot be empty")
    metadata = ConversationSummary(
        id=conversation_id, title=label, created_at=timestamp, updated_at=timestamp
    )
    _atomic_write(_thread_path(store_path, workspace, conversation_id), _empty_thread())
    index["conversations"] = [
        *cast(list[object], index["conversations"]),
        metadata.model_dump(mode="json"),
    ]
    index["active_id"] = conversation_id
    _atomic_write(_index_path(store_path, workspace), index)
    return list_conversations(store_path, workspace)


def activate_conversation(
    store_path: Path, workspace: Path, conversation_id: str
) -> ConversationCatalog:
    index = _catalog(store_path, workspace)
    item = _find(index, conversation_id)
    if item.archived:
        raise ConversationConflict("Archived conversations must be restored before opening")
    index["active_id"] = conversation_id
    _atomic_write(_index_path(store_path, workspace), index)
    return list_conversations(store_path, workspace)


def update_conversation(
    store_path: Path,
    workspace: Path,
    conversation_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
) -> ConversationCatalog:
    index = _catalog(store_path, workspace)
    item = _find(index, conversation_id)
    if archived is True and _has_pending_approval(store_path, workspace, conversation_id):
        raise ConversationConflict(
            "Resolve the pending approval before archiving this conversation"
        )
    if archived is True and index["active_id"] == conversation_id:
        raise ConversationConflict("Open another conversation before archiving this one")
    if title is not None:
        title = title.strip()
        if not title:
            raise ValueError("Conversation title cannot be empty")
        item.title = title
    if archived is not None:
        item.archived = archived
    item.updated_at = _now()
    _update_metadata(index, item)
    _atomic_write(_index_path(store_path, workspace), index)
    return list_conversations(store_path, workspace)


def delete_conversation(
    store_path: Path, workspace: Path, conversation_id: str
) -> ConversationCatalog:
    index = _catalog(store_path, workspace)
    _find(index, conversation_id)
    if _has_pending_approval(store_path, workspace, conversation_id):
        raise ConversationConflict("Resolve the pending approval before deleting this conversation")
    remaining = [entry for entry in _summaries(index) if entry.id != conversation_id]
    if not remaining:
        replacement_id = uuid4().hex
        timestamp = _now()
        remaining = [
            ConversationSummary(
                id=replacement_id,
                title="Conversation 1",
                created_at=timestamp,
                updated_at=timestamp,
            )
        ]
        _atomic_write(_thread_path(store_path, workspace, replacement_id), _empty_thread())
    index["conversations"] = [entry.model_dump(mode="json") for entry in remaining]
    if index["active_id"] == conversation_id:
        index["active_id"] = next(
            (entry.id for entry in remaining if not entry.archived), remaining[0].id
        )
        if all(entry.archived for entry in remaining):
            first = remaining[0]
            first.archived = False
            _update_metadata(index, first)
    _atomic_write(_index_path(store_path, workspace), index)
    _thread_path(store_path, workspace, conversation_id).unlink(missing_ok=True)
    return list_conversations(store_path, workspace)


def _pending_approval(history: list[ModelMessage]) -> PendingApproval | None:
    returned_ids = {
        part.tool_call_id
        for message in history
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    for message in reversed(history):
        if not isinstance(message, ModelResponse):
            continue
        for part in reversed(message.parts):
            if isinstance(part, ToolCallPart) and part.tool_call_id not in returned_ids:
                return PendingApproval(
                    id=f"int-{part.tool_call_id}",
                    message=f"Approve {part.tool_name}({part.args_as_json_str()})?",
                )
    return None


def _has_pending_approval(store_path: Path, workspace: Path, conversation_id: str) -> bool:
    thread = _read_thread(store_path, workspace, conversation_id)
    history = ModelMessagesTypeAdapter.validate_python(thread["history"])
    return _pending_approval(history) is not None


def read_conversation_transcript(
    store_path: Path, workspace: Path, conversation_id: str
) -> ChatTranscript:
    thread = _read_thread(store_path, workspace, conversation_id)
    history = ModelMessagesTypeAdapter.validate_python(thread["history"])
    return ChatTranscript.model_validate(
        {"messages": thread["messages"], "approval": _pending_approval(history)}
    )


def read_chat_transcript(store_path: Path, workspace: Path) -> ChatTranscript:
    return read_conversation_transcript(
        store_path, workspace, active_conversation_id(store_path, workspace)
    )


def read_chat_history(
    store_path: Path, workspace: Path, conversation_id: str | None = None
) -> list[ModelMessage]:
    conversation_id = conversation_id or active_conversation_id(store_path, workspace)
    thread = _read_thread(store_path, workspace, conversation_id)
    history = ModelMessagesTypeAdapter.validate_python(thread["history"])
    summary = thread.get("summary")
    if isinstance(summary, str) and summary:
        return [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=(
                            "Earlier conversation summary, reviewed by the Course Author:\n"
                            f"{summary}"
                        )
                    )
                ]
            ),
            *history,
        ]
    return history


def _visible_messages(history: list[ModelMessage]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for message in AGUIAdapter.dump_messages(history):
        if (
            message.role in ("user", "assistant")
            and isinstance(message.content, str)
            and message.content
        ):
            result.append(
                ChatMessage(id=message.id, role=message.role, content=message.content).model_dump()
            )
    return result


def save_chat_history(
    store_path: Path,
    workspace: Path,
    history: list[ModelMessage],
    conversation_id: str | None = None,
) -> None:
    conversation_id = conversation_id or active_conversation_id(store_path, workspace)
    index = _catalog(store_path, workspace)
    item = _find(index, conversation_id)
    thread = _read_thread(store_path, workspace, conversation_id)
    if thread.get("summary") and history and isinstance(history[0], ModelRequest):
        first = history[0]
        if (
            len(first.parts) == 1
            and isinstance(first.parts[0], UserPromptPart)
            and first.parts[0].content
            == (
                f"Earlier conversation summary, reviewed by the Course Author:\n{thread['summary']}"
            )
        ):
            history = history[1:]
    history_payload = ModelMessagesTypeAdapter.dump_python(history, mode="json")
    retained = ChatTranscript.model_validate(
        {"messages": thread.get("retained_messages", [])}
    ).model_dump()["messages"]
    thread["history"] = history_payload
    thread["messages"] = [*retained, *_visible_messages(history)]
    item.updated_at = _now()
    item.has_pending_approval = _pending_approval(history) is not None
    _atomic_write(_thread_path(store_path, workspace, conversation_id), thread)
    _update_metadata(index, item)
    _atomic_write(_index_path(store_path, workspace), index)


def clear_chat_history(store_path: Path, workspace: Path) -> None:
    conversation_id = active_conversation_id(store_path, workspace)
    index = _catalog(store_path, workspace)
    item = _find(index, conversation_id)
    if _has_pending_approval(store_path, workspace, conversation_id):
        raise ConversationConflict("Resolve the pending approval before clearing this conversation")
    _atomic_write(_thread_path(store_path, workspace, conversation_id), _empty_thread())
    item.updated_at = _now()
    _update_metadata(index, item)
    _atomic_write(_index_path(store_path, workspace), index)


def _revision(thread: dict[str, object]) -> str:
    payload = json.dumps(
        [thread["history"], thread["messages"], thread.get("summary")], sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def preview_compaction(
    store_path: Path, workspace: Path, conversation_id: str
) -> CompactionPreview:
    thread = _read_thread(store_path, workspace, conversation_id)
    history = ModelMessagesTypeAdapter.validate_python(thread["history"])
    if _pending_approval(history):
        raise ConversationConflict(
            "Resolve the pending approval before compacting this conversation"
        )
    messages = ChatTranscript.model_validate({"messages": thread["messages"]}).messages
    if not messages:
        raise ConversationConflict("There are no messages to compact")
    selected = messages[:2] + (messages[-8:] if len(messages) > 10 else messages[2:])
    lines = [f"{message.role}: {message.content.strip()[:350]}" for message in selected]
    omitted = len(messages) - len(selected)
    if omitted:
        lines.insert(2, f"[{omitted} earlier messages omitted from this draft summary]")
    summary = ("Conversation so far:\n" + "\n".join(lines))[:4000]
    return CompactionPreview(
        summary=summary,
        source_message_count=len(messages),
        revision=_revision(thread),
    )


def compact_conversation(
    store_path: Path,
    workspace: Path,
    conversation_id: str,
    *,
    summary: str,
    revision: str,
) -> ChatTranscript:
    index = _catalog(store_path, workspace)
    item = _find(index, conversation_id)
    if item.archived:
        raise ConversationConflict("Restore this conversation before compacting it")
    thread = _read_thread(store_path, workspace, conversation_id)
    history = ModelMessagesTypeAdapter.validate_python(thread["history"])
    if _pending_approval(history):
        raise ConversationConflict(
            "Resolve the pending approval before compacting this conversation"
        )
    if _revision(thread) != revision:
        raise ConversationConflict("Conversation changed; review a new compaction preview")
    summary = summary.strip()
    if not summary:
        raise ValueError("Compaction summary cannot be empty")
    if len(summary) > 4000:
        raise ValueError("Compaction summary is too long")
    thread["compactions"] = [
        *cast(list[object], thread.get("compactions", [])),
        {
            "conversation_id": conversation_id,
            "source_revision": revision,
            "source_message_count": len(cast(list[object], thread["messages"])),
            "created_at": _now(),
            "summary": summary,
        },
    ]
    thread["retained_messages"] = thread["messages"]
    thread["history"] = []
    thread["summary"] = summary
    item.updated_at = _now()
    _atomic_write(_thread_path(store_path, workspace, conversation_id), thread)
    _update_metadata(index, item)
    _atomic_write(_index_path(store_path, workspace), index)
    return read_conversation_transcript(store_path, workspace, conversation_id)
