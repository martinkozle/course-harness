import asyncio
import json
import re
from collections.abc import AsyncIterator
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory

import uvicorn
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.chat_history import is_course_state_part
from course_harness.course_plan import read_course_plan
from course_harness.presentation import read_presentation_for_lecture
from course_harness.providers import (
    ContextWindowUnknownError,
    ModelSuggestion,
    PromptCachingUnknownError,
    ProviderCapabilities,
    ProviderConfigurationRequest,
)
from course_harness.sources import read_sources_index

# A model whose server, like some vLLM deployments, does not report its context window.
UNREPORTED_CONTEXT_MODEL = "vllm/unreported-context"


async def verified_capabilities(request: ProviderConfigurationRequest) -> ProviderCapabilities:
    context_window = 131_072
    if request.model == UNREPORTED_CONTEXT_MODEL:
        if request.context_window is None:
            raise ContextWindowUnknownError(
                "The provider did not report a context window for this model. "
                "Enter the model's context size to continue."
            )
        context_window = request.context_window
    # An application inference profile ARN does not reveal whether it can cache prompts.
    if request.model.startswith("arn:") and request.prompt_caching is None:
        raise PromptCachingUnknownError(
            "Course Harness cannot tell from this model ID whether the model supports "
            "prompt caching. Say whether it does to continue."
        )
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=context_window,
        vision=False,
        prompt_caching=request.prompt_caching,
    )


async def verified_account(_request: object) -> None:
    return None


async def no_model_suggestions(_store: Path, _account_id: str) -> list[ModelSuggestion]:
    # Listing Bedrock models would reach the developer's real AWS configuration.
    return []


ATTACHMENT = re.compile(r"^\[Attachment: (resource-[0-9a-f]{12}) ", re.MULTILINE)


def attached_image_request(messages: list[ModelMessage]) -> tuple[str, set[str]] | None:
    """Return the attached Resource and the tools returned since the latest such request."""
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if (
                isinstance(part, UserPromptPart)
                and isinstance(part.content, str)
                and not is_course_state_part(part)
            ):
                match = ATTACHMENT.search(part.content)
                if match is None or "Use the attached image" not in part.content:
                    return None
                returned = {
                    later.tool_name
                    for request in messages[index:]
                    if isinstance(request, ModelRequest)
                    for later in request.parts
                    if isinstance(later, ToolReturnPart)
                }
                return match.group(1), returned
    return None


def place_attached_image(resource_id: str, returned: set[str]) -> str | dict[int, DeltaToolCall]:
    if "view_image" not in returned:
        return {
            0: DeltaToolCall(
                name="view_image",
                json_args=json.dumps({"image_id": resource_id}),
                tool_call_id="playwright-view-image",
            )
        }
    if "admit_source" not in returned:
        return {
            0: DeltaToolCall(
                name="admit_source",
                json_args=json.dumps({"resource_id": resource_id}),
                tool_call_id="playwright-admit-image",
            )
        }
    if "replace_presentation" in returned:
        return "I placed the attached image on a new Slide."
    assert active_workspace is not None
    plan = read_course_plan(active_workspace)
    sources = read_sources_index(active_workspace)
    assert plan is not None and sources is not None
    source = next(item for item in sources.sources if item.resource_id == resource_id)
    lecture = plan.lectures[0]
    existing = read_presentation_for_lecture(active_workspace, lecture.id)
    slides: list[dict[str, object]] = [
        {"id": slide.id, "layout": slide.layout, "title": slide.title}
        for slide in (existing.slides if existing else [])
    ]
    slides.append(
        {
            "layout": "image",
            "title": "The attached figure",
            "image_source_id": source.id,
            "caption": "A figure the Course Author attached",
            "citations": [{"source_id": source.id, "label": source.label}],
        }
    )
    return {
        0: DeltaToolCall(
            name="replace_presentation",
            json_args=json.dumps({"command": {"lecture_id": lecture.id, "slides": slides}}),
            tool_call_id="playwright-image-slide",
        )
    }


async def course_planning_model(
    messages: list[ModelMessage], _info: AgentInfo
) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
    attached = attached_image_request(messages)
    if attached is not None:
        yield place_attached_image(*attached)
        return
    if any(
        isinstance(part, UserPromptPart)
        and isinstance(part.content, str)
        and "Wait until I stop you." in part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    ):
        await asyncio.Event().wait()
        yield "This response should have been stopped."
        return
    if any(
        isinstance(part, UserPromptPart)
        and isinstance(part.content, str)
        and "Review the lecture sequence." in part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    ):
        yield "The lecture sequence is ready for review."
        return
    returned_tools = {
        part.tool_name
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    if "replace_course_plan" not in returned_tools:
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
        return

    plan = read_course_plan(active_workspace) if active_workspace is not None else None
    sources = read_sources_index(active_workspace) if active_workspace is not None else None
    first_lecture = plan.lectures[0] if plan and plan.lectures else None
    first_source = sources.sources[0] if sources and sources.sources else None
    presentation = (
        read_presentation_for_lecture(active_workspace, first_lecture.id)
        if active_workspace is not None and first_lecture is not None
        else None
    )
    if first_lecture is not None and first_source is not None and presentation is None:
        command = {
            "lecture_id": first_lecture.id,
            "slides": [
                {
                    "layout": "title",
                    "title": "Causal foundations",
                    "subtitle": "From association to intervention",
                    "purpose": "Orient the Course Author to the central question.",
                },
                {
                    "layout": "bullets",
                    "title": "The counterfactual question",
                    "bullets": [
                        "Compare outcomes under treatment and control.",
                        "Use a causal graph to make assumptions visible.",
                    ],
                    "purpose": "Ground the intervention question in the admitted Source.",
                    "citations": [
                        {
                            "source_id": first_source.id,
                            "label": first_source.label,
                            "line_start": 0,
                            "line_end": 4,
                        }
                    ],
                },
            ],
        }
        yield {
            0: DeltaToolCall(
                name="replace_presentation",
                json_args=json.dumps({"command": command}),
                tool_call_id="playwright-cited-presentation",
            )
        }
        return

    if "replace_presentation" in returned_tools:
        yield "I created a cited Presentation grounded in the admitted Source."
        return

    tool_has_returned = any(
        isinstance(message, ModelRequest)
        and any(isinstance(part, ToolReturnPart) for part in message.parts)
        for message in messages
    )
    yield "I created a two-Lecture Course Plan." if tool_has_returned else "I am ready to help."


temporary_root = TemporaryDirectory(prefix="course-harness-e2e-", dir=".cache")
root = Path(temporary_root.name)
# Detected Credentials come only from this fake home, never from the developer's shell.
credential_home = root / "home"
(credential_home / ".aws").mkdir(parents=True)
(credential_home / ".aws" / "config").write_text(
    "[profile work]\nregion = eu-central-1\n", encoding="utf-8"
)
workspace_ids = count(1)
active_workspace: Path | None = None


def create_test_workspace() -> Path:
    global active_workspace
    workspace = root / f"playwright-workspace-{next(workspace_ids)}"
    workspace.mkdir()
    active_workspace = workspace
    return workspace


uvicorn.run(
    create_app(
        folder_picker=create_test_workspace,
        recent_store_path=root / "user-data" / "recent-workspaces.json",
        provider_store_path=root / "user-data" / "provider",
        chat_store_path=root / "user-data" / "chat",
        library_data_path=root / "library-data",
        library_cache_path=root / "library-cache",
        templates_data_path=root / "templates-data",
        templates_cache_path=root / "templates-cache",
        release_data_path=root / "release-data",
        precompute_template_backgrounds=False,
        agent_model=FunctionModel(stream_function=course_planning_model),
        provider_validator=verified_capabilities,
        provider_account_validator=verified_account,
        provider_model_lister=no_model_suggestions,
        credential_environ={"HOME": str(credential_home)},
        # The smoke journey must not reach the default Exa Connector over the network.
        connector_connect=lambda connector: MCPToolset(
            "http://127.0.0.1:9/mcp", id=connector.id, init_timeout=2
        ),
    ),
    host="127.0.0.1",
    port=18765,
)
