import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.ui.ag_ui import AGUIAdapter

from course_harness.workspaces import workspace_identity


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    role: Literal["user", "assistant"]
    content: str


class ChatTranscript(BaseModel):
    messages: list[ChatMessage]


def chat_session_path(store_path: Path, workspace: Path) -> Path:
    return store_path / f"{workspace_identity(workspace)}.json"


def read_chat_history(store_path: Path, workspace: Path) -> list[ModelMessage]:
    path = chat_session_path(store_path, workspace)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ModelMessagesTypeAdapter.validate_python(payload["history"])
    except OSError, ValueError, KeyError, TypeError, json.JSONDecodeError:
        return []


def read_chat_transcript(store_path: Path, workspace: Path) -> ChatTranscript:
    path = chat_session_path(store_path, workspace)
    if not path.is_file():
        return ChatTranscript(messages=[])
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ChatTranscript.model_validate({"messages": payload["messages"]})
    except OSError, ValueError, KeyError, TypeError, json.JSONDecodeError:
        return ChatTranscript(messages=[])


def save_chat_history(store_path: Path, workspace: Path, history: list[ModelMessage]) -> None:
    store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store_path, 0o700)
    protocol_messages = AGUIAdapter.dump_messages(history)
    chat_messages: list[ChatMessage] = []
    for message in protocol_messages:
        if (
            (message.role == "user" or message.role == "assistant")
            and isinstance(message.content, str)
            and message.content
        ):
            chat_messages.append(
                ChatMessage(id=message.id, role=message.role, content=message.content)
            )
    transcript = ChatTranscript(messages=chat_messages)
    payload = {
        "version": 1,
        "history": ModelMessagesTypeAdapter.dump_python(history, mode="json"),
        "messages": transcript.model_dump(mode="json")["messages"],
    }
    path = chat_session_path(store_path, workspace)
    temporary_path = path.with_suffix(".json.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
