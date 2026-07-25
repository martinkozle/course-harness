import json
from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.providers import ProviderCapabilities


async def verified_capabilities(_request: object) -> ProviderCapabilities:
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=131_072,
        vision=False,
    )


async def verified_account(_request: object) -> None:
    return None


async def course_planning_model(
    messages: list[ModelMessage], _info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    tool_has_returned = any(
        isinstance(message, ModelRequest)
        and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    )
    if not tool_has_returned:
        command = {
            "title": "Causal Inference in Practice",
            "audience": "Applied researchers who know regression",
            "goals": ["Reason clearly about interventions"],
            "outcomes": ["Draw and critique a causal graph"],
            "lectures": [
                {"title": "From association to intervention"},
                {"title": "Confounding and adjustment"},
            ],
        }
        yield {
            0: DeltaToolCall(
                name="replace_course_plan",
                json_args=json.dumps({"command": command}),
                tool_call_id="playwright-course-plan",
            )
        }
    else:
        yield "I created a two-Lecture Course Plan."


temporary_root = TemporaryDirectory(prefix="course-harness-e2e-", dir=".cache")
root = Path(temporary_root.name)
workspace = root / "playwright-workspace"
workspace.mkdir()

uvicorn.run(
    create_app(
        folder_picker=lambda: workspace,
        recent_store_path=root / "user-data" / "recent-workspaces.json",
        provider_store_path=root / "user-data" / "provider",
        chat_store_path=root / "user-data" / "chat",
        agent_model=FunctionModel(stream_function=course_planning_model),
        provider_validator=verified_capabilities,
        provider_account_validator=verified_account,
    ),
    host="127.0.0.1",
    port=18765,
)


temporary_root = TemporaryDirectory(prefix="course-harness-e2e-", dir=".cache")
root = Path(temporary_root.name)
workspace = root / "playwright-workspace"
workspace.mkdir()

uvicorn.run(
    create_app(
        folder_picker=lambda: workspace,
        recent_store_path=root / "user-data" / "recent-workspaces.json",
        provider_store_path=root / "user-data" / "provider",
        chat_store_path=root / "user-data" / "chat",
        agent_model=FunctionModel(stream_function=course_planning_model),
        provider_validator=verified_capabilities,
        provider_account_validator=verified_account,
    ),
    host="127.0.0.1",
    port=18765,
)
