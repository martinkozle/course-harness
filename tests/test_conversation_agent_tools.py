"""The Course Agent may inspect earlier conversations without changing them."""

from pathlib import Path

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from course_harness.chat_history import (
    create_conversation,
    list_conversations,
    read_conversation_transcript,
    save_chat_history,
    update_conversation,
)
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    create_autonomous_course_agent,
)


def test_course_agent_can_read_archived_conversation_without_mutating_it(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    chat_store = tmp_path / "private-chat"
    save_chat_history(
        chat_store,
        workspace,
        [
            ModelRequest(parts=[UserPromptPart(content="Keep the first lecture practical.")]),
            ModelResponse(parts=[TextPart(content="I will focus on worked examples.")]),
        ],
    )
    archived_id = list_conversations(chat_store, workspace).active_id
    create_conversation(chat_store, workspace, "Next lecture")
    update_conversation(chat_store, workspace, archived_id, archived=True)
    original = read_conversation_transcript(chat_store, workspace, archived_id)

    def recall(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        returned = [
            part
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        if not returned:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="list_conversations", args={}, tool_call_id="list-1")]
            )
        if len(returned) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="read_conversation",
                        args={"conversation_id": archived_id},
                        tool_call_id="read-1",
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(content="I found the earlier direction.")])

    result = create_autonomous_course_agent().run_sync(
        "Recall the earlier lecture direction.",
        model=FunctionModel(function=recall),
        deps=CourseAgentDeps(
            course_state=CourseAgentState(),
            workspace=workspace,
            data_dir=tmp_path / "library",
            cache_dir=tmp_path / "cache",
            chat_store_path=chat_store,
        ),
    )

    returns = [
        str(part.content)
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert len(returns) == 2
    assert archived_id in returns[0]
    assert "conversation_catalog" in returns[0]
    assert "Keep the first lecture practical." in returns[1]
    assert "conversation_history" in returns[1]
    assert read_conversation_transcript(chat_store, workspace, archived_id) == original
