import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from course_harness.app import create_app
from course_harness.course_agent import (
    CoursePlanLectureCommand,
    ReplaceCoursePlanCommand,
    apply_course_plan_command,
)
from course_harness.providers import (
    ProviderCapabilities,
    ProviderConfigurationRequest,
    ProviderValidationError,
    validate_provider_capabilities,
)


async def _verified_capabilities(_request: object) -> ProviderCapabilities:
    return ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=131_072,
        vision=False,
    )


async def _verified_account(_request: object) -> None:
    return None


def _provider_request() -> dict[str, object]:
    return {
        "kind": "openrouter",
        "model": "openai/gpt-oss-20b:free",
        "api_key": "openrouter-secret",
    }


def _plan_command(**changes: object) -> ReplaceCoursePlanCommand:
    values: dict[str, object] = {
        "title": "Causal Inference",
        "audience": "Applied researchers",
        "lectures": [{"title": "Foundations"}],
    }
    values.update(changes)
    return ReplaceCoursePlanCommand.model_validate(values)


def test_course_plan_revisions_cannot_silently_replace_lecture_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    original = apply_course_plan_command(workspace, _plan_command())

    with pytest.raises(ValueError, match="must preserve existing Lecture IDs"):
        apply_course_plan_command(
            workspace,
            _plan_command(lectures=[{"title": "Renamed foundations"}]),
        )

    updated = apply_course_plan_command(
        workspace,
        _plan_command(
            lectures=[
                CoursePlanLectureCommand(id=original.lectures[0].id, title="Renamed foundations")
            ]
        ),
    )
    assert updated.lectures[0].id == original.lectures[0].id


async def _post_stream(app: Any, path: str, payload: object) -> tuple[int, str]:
    body = json.dumps(payload).encode()
    request_sent = False
    response_status = 0
    response_parts: list[bytes] = []

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if request_sent:
            return {"type": "http.disconnect"}
        request_sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_status
        if message["type"] == "http.response.start":
            response_status = message["status"]
        elif message["type"] == "http.response.body" and message.get("body"):
            response_parts.append(message["body"])

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.4"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"accept", b"text/event-stream"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return response_status, b"".join(response_parts).decode()


@pytest.mark.anyio
async def test_provider_configuration_is_kept_outside_the_course_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=_verified_capabilities,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/provider")
        configured = await client.put(
            "/api/provider",
            json=_provider_request(),
        )
        after = await client.get("/api/provider")

    assert before.status_code == 200
    assert before.json() == {"configured": False}
    assert configured.status_code == 200
    assert configured.json() == {
        "configured": True,
        "kind": "openrouter",
        "model": "openai/gpt-oss-20b:free",
        "base_url": "https://openrouter.ai/api/v1",
        "capabilities": {
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "context_window": 131_072,
            "vision": False,
        },
        "diagnostics": [
            "Vision input is unavailable; image attachments cannot be used with this model."
        ],
    }
    assert after.json() == configured.json()
    assert "openrouter-secret" not in repr(after.json())
    assert list(workspace.iterdir()) == []
    assert "openrouter-secret" not in "".join(
        path.read_text(encoding="utf-8") for path in workspace.rglob("*") if path.is_file()
    )
    assert (provider_store / "provider.json").is_file()
    assert (provider_store / "credentials.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.anyio
async def test_one_provider_account_can_back_multiple_selectable_model_presets(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "providers"
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=_verified_capabilities,
            provider_account_validator=_verified_account,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        account = await client.post(
            "/api/provider-accounts",
            json={
                "name": "My OpenRouter",
                "kind": "openrouter",
                "api_key": "one-reusable-secret",
            },
        )
        account_id = account.json()["id"]
        first = await client.post(
            "/api/models",
            json={
                "name": "Nemotron",
                "provider_account_id": account_id,
                "model": "nvidia/llama-3.3-nemotron-super-49b-v1:free",
            },
        )
        second = await client.post(
            "/api/models",
            json={
                "name": "HY 3",
                "provider_account_id": account_id,
                "model": "tencent/hy3:free",
            },
        )
        selected = await client.put("/api/models/selected", json={"model_id": second.json()["id"]})
        catalog = await client.get("/api/models")

    assert account.status_code == 201
    assert "api_key" not in account.json()
    assert first.status_code == 201
    assert second.status_code == 201
    assert selected.status_code == 200
    assert len(catalog.json()["provider_accounts"]) == 1
    assert [preset["name"] for preset in catalog.json()["model_presets"]] == [
        "Nemotron",
        "HY 3",
    ]
    assert catalog.json()["selected_model_id"] == second.json()["id"]
    credentials = (provider_store / "credentials.json").read_text(encoding="utf-8")
    assert credentials.count("one-reusable-secret") == 1


@pytest.mark.anyio
async def test_provider_settings_are_unavailable_until_a_workspace_is_bound(
    tmp_path: Path,
) -> None:
    transport = httpx2.ASGITransport(
        app=create_app(
            provider_store_path=tmp_path / "provider",
            provider_validator=_verified_capabilities,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        status = await client.get("/api/provider")
        configured = await client.put("/api/provider", json=_provider_request())

    assert status.status_code == 409
    assert configured.status_code == 409
    assert not (tmp_path / "provider").exists()


@pytest.mark.anyio
async def test_provider_capability_failures_are_explained_before_a_run(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    async def unsupported(_request: object) -> ProviderCapabilities:
        return ProviderCapabilities(
            tool_calling=False,
            structured_output=False,
            streaming=False,
            context_window=8_192,
            vision=False,
        )

    transport = httpx2.ASGITransport(
        app=create_app(
            workspace, provider_store_path=provider_store, provider_validator=unsupported
        )
    )
    request = _provider_request()

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/provider", json=request)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "Tool calling is required" in detail
    assert "Streaming is required" in detail
    assert "at least 16,384 tokens" in detail
    assert not provider_store.exists()


@pytest.mark.anyio
async def test_structured_output_is_informational_when_typed_tools_are_supported(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()

    async def tool_capable(_request: object) -> ProviderCapabilities:
        return ProviderCapabilities(
            tool_calling=True,
            structured_output=False,
            streaming=True,
            context_window=131_072,
            vision=False,
        )

    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=tmp_path / "provider",
            provider_validator=tool_capable,
        )
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/provider", json=_provider_request())

    assert response.status_code == 200
    assert response.json()["capabilities"]["structured_output"] is False
    assert any(
        "native structured output" in item.lower() for item in response.json()["diagnostics"]
    )


@pytest.mark.anyio
async def test_openrouter_configuration_rejects_an_unauthenticated_key() -> None:
    def openrouter(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/v1/key":
            return httpx2.Response(
                401,
                json={"error": {"message": "Missing Authentication header", "code": 401}},
            )
        return httpx2.Response(
            200,
            json={
                "data": {
                    "id": "tencent/hy3:free",
                    "context_length": 262_144,
                    "supported_parameters": ["tools", "structured_outputs"],
                    "architecture": {"input_modalities": ["text"]},
                }
            },
        )

    request = ProviderConfigurationRequest.model_validate(_provider_request())
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(openrouter)) as client:
        with pytest.raises(ProviderValidationError, match="API key was rejected"):
            await validate_provider_capabilities(request, http_client=client)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "base_url", "expected_url"),
    [
        ("anthropic", None, "https://api.anthropic.com"),
        ("openai-compatible", "http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1"),
    ],
)
async def test_direct_and_openai_compatible_providers_are_supported(
    tmp_path: Path, kind: str, base_url: str | None, expected_url: str
) -> None:
    workspace = tmp_path / kind
    workspace.mkdir()
    request = _provider_request()
    request.update({"kind": kind, "model": "course-planning-model", "base_url": base_url})
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=tmp_path / "provider",
            provider_validator=_verified_capabilities,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/provider", json=request)

    assert response.status_code == 200
    assert response.json()["kind"] == kind
    assert response.json()["base_url"] == expected_url


@pytest.mark.anyio
async def test_chat_streams_a_validated_course_plan_and_survives_reopening(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"
    chat_store = tmp_path / "user-data" / "chat"

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
                    {"title": "From association to intervention", "group": "Foundations"},
                    {"title": "Confounding and adjustment", "group": "Foundations"},
                ],
            }
            yield {
                0: DeltaToolCall(
                    name="replace_course_plan",
                    json_args=json.dumps({"command": command}),
                    tool_call_id="course-plan-1",
                )
            }
        else:
            yield "I created a two-Lecture Course Plan."

    model = FunctionModel(stream_function=course_planning_model)
    app = create_app(
        workspace,
        provider_store_path=provider_store,
        chat_store_path=chat_store,
        agent_model=model,
        provider_validator=_verified_capabilities,
    )
    run_input = {
        "threadId": "course-agent",
        "runId": "run-1",
        "state": {},
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "Create a practical causal inference Course for applied researchers.",
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        before_approval = await client.get("/api/course")
        pending_chat = await client.get("/api/chat")
        proposal_events = [
            json.loads(line.removeprefix("data: "))
            for line in proposal_body.splitlines()
            if line.startswith("data: ")
        ]
        interrupt = proposal_events[-1]["outcome"]["interrupts"][0]
        approval_input = {
            **run_input,
            "runId": "run-2",
            "messages": [],
            "resume": [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": True},
                }
            ],
        }
        stream_status, stream_body = await _post_stream(app, "/api/agent", approval_input)
        course_response = await client.get("/api/course")
        chat_response = await client.get("/api/chat")
        await client.post("/api/workspace/close")

    assert proposal_status == 200
    assert before_approval.status_code == 404
    assert pending_chat.json()["approval"]["id"] == "int-course-plan-1"
    assert "TOOL_CALL_START" in [event["type"] for event in proposal_events]
    assert stream_status == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream_body.splitlines()
        if line.startswith("data: ")
    ]
    event_types = [event["type"] for event in events]
    assert event_types[0] == "RUN_STARTED"
    assert "TOOL_CALL_RESULT" in event_types
    assert "ACTIVITY_SNAPSHOT" in event_types
    assert "STATE_SNAPSHOT" in event_types
    assert "TEXT_MESSAGE_CONTENT" in event_types
    assert event_types[-1] == "RUN_FINISHED"

    assert course_response.status_code == 200
    plan = course_response.json()
    assert plan["title"] == "Causal Inference in Practice"
    assert [lecture["title"] for lecture in plan["lectures"]] == [
        "From association to intervention",
        "Confounding and adjustment",
    ]
    assert "title: Causal Inference in Practice" in (workspace / "course.yaml").read_text(
        encoding="utf-8"
    )
    assert chat_response.json() == {
        "approval": None,
        "messages": [
            {
                "id": chat_response.json()["messages"][0]["id"],
                "role": "user",
                "content": "Create a practical causal inference Course for applied researchers.",
            },
            {
                "id": chat_response.json()["messages"][1]["id"],
                "role": "assistant",
                "content": "I created a two-Lecture Course Plan.",
            },
        ],
    }

    reopened = create_app(
        workspace,
        provider_store_path=provider_store,
        chat_store_path=chat_store,
        agent_model=model,
        provider_validator=_verified_capabilities,
    )
    reopened_transport = httpx2.ASGITransport(app=reopened)
    async with httpx2.AsyncClient(transport=reopened_transport, base_url="http://test") as client:
        reopened_chat = await client.get("/api/chat")
        reopened_course = await client.get("/api/course")

    assert reopened_chat.json() == chat_response.json()
    assert reopened_course.json() == plan


@pytest.mark.anyio
async def test_active_agent_run_locks_competing_workspace_mutations(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def waiting_model(_messages: list[ModelMessage], _info: AgentInfo) -> AsyncIterator[str]:
        started.set()
        await finish.wait()
        yield "No Course changes were proposed."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        agent_model=FunctionModel(stream_function=waiting_model),
        provider_validator=_verified_capabilities,
    )
    run_input = {
        "threadId": "course-agent",
        "runId": "locked-run",
        "state": {},
        "messages": [{"id": "user-lock", "role": "user", "content": "Think about this."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        running = asyncio.create_task(_post_stream(app, "/api/agent", run_input))
        await started.wait()
        competing = await client.post(
            "/api/course",
            json={
                "title": "Racing Course",
                "audience": "Anyone",
                "lectures": [{"title": "One"}],
            },
        )
        finish.set()
        stream_status, _ = await running

    assert competing.status_code == 409
    assert "already running" in competing.json()["detail"]
    assert stream_status == 200
    assert not (workspace / "course.yaml").exists()


@pytest.mark.anyio
async def test_live_openrouter_smoke_uses_only_an_explicitly_free_model(
    tmp_path: Path,
) -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    model_name = os.environ.get("COURSE_HARNESS_LIVE_OPENROUTER_MODEL")
    if not api_key or not model_name:
        pytest.skip("set both live OpenRouter variables to run the optional smoke check")
    if not model_name.endswith(":free"):
        pytest.skip("COURSE_HARNESS_LIVE_OPENROUTER_MODEL must end in :free")

    workspace = tmp_path / "live-course"
    workspace.mkdir()
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "user-data" / "provider",
        chat_store_path=tmp_path / "user-data" / "chat",
    )
    transport = httpx2.ASGITransport(app=app)
    provider_request = _provider_request()
    provider_request.update({"model": model_name, "api_key": api_key})
    run_input = {
        "threadId": "course-agent",
        "runId": "live-free-smoke",
        "state": {},
        "messages": [
            {
                "id": "live-user",
                "role": "user",
                "content": "Create a one-Lecture Course Plan about careful statistical reasoning.",
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        configured = await client.put("/api/provider", json=provider_request)
        stream_status, stream_body = await _post_stream(app, "/api/agent", run_input)
        created = await client.get("/api/course")

    assert configured.status_code == 200
    assert api_key not in repr(configured.json())
    assert stream_status == 200
    assert '"type":"RUN_FINISHED"' in stream_body
    assert created.status_code == 200


@pytest.mark.anyio
async def test_autonomous_mode_applies_course_plan_changes_without_interrupt(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "auto-course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"
    chat_store = tmp_path / "user-data" / "chat"

    model_calls = []

    async def course_planning_model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        model_calls.append("stream")
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if not tool_has_returned:
            command = {
                "title": "Self-driving Course",
                "audience": "ML practitioners",
                "lectures": [{"title": "Why automate?"}, {"title": "Safety first"}],
            }
            yield {
                0: DeltaToolCall(
                    name="replace_course_plan",
                    json_args=json.dumps({"command": command}),
                    tool_call_id="auto-plan-1",
                )
            }
        else:
            yield "I created a two-Lecture Course Plan automatically."

    def model_sync(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        model_calls.append("sync")
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if tool_has_returned:
            return ModelResponse(
                parts=[TextPart(content="I created a two-Lecture Course Plan automatically.")]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="replace_course_plan",
                    args=json.dumps(
                        {
                            "command": {
                                "title": "Self-driving Course",
                                "audience": "ML practitioners",
                                "lectures": [
                                    {"title": "Why automate?"},
                                    {"title": "Safety first"},
                                ],
                            }
                        }
                    ),
                    tool_call_id="auto-plan-1",
                )
            ]
        )

    model = FunctionModel(stream_function=course_planning_model, function=model_sync)
    app = create_app(
        workspace,
        provider_store_path=provider_store,
        chat_store_path=chat_store,
        agent_model=model,
        provider_validator=_verified_capabilities,
    )
    autonomous_input = {
        "threadId": "course-agent",
        "runId": "auto-run",
        "state": {},
        "messages": [{"id": "auto-user", "role": "user", "content": "Plan a Course."}],
        "tools": [],
        "context": [],
        "forwardedProps": {"mode": "autonomous"},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        stream_status, stream_body = await _post_stream(app, "/api/agent", autonomous_input)
        course_response = await client.get("/api/course")
        chat_response = await client.get("/api/chat")

    assert stream_status == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream_body.splitlines()
        if line.startswith("data: ")
    ]
    event_types = [event["type"] for event in events]
    assert event_types[-1] == "RUN_FINISHED"
    outcome = events[-1].get("outcome", {})
    assert not outcome.get("interrupts"), "autonomous mode should not produce interrupts"

    assert course_response.status_code == 200
    plan = course_response.json()
    assert plan["title"] == "Self-driving Course"
    assert len(plan["lectures"]) == 2
    assert chat_response.json()["approval"] is None
    app = create_app(
        workspace,
        provider_store_path=provider_store,
        chat_store_path=chat_store,
        agent_model=model,
        provider_validator=_verified_capabilities,
    )
    autonomous_input = {
        "threadId": "course-agent",
        "runId": "auto-run",
        "state": {},
        "messages": [{"id": "auto-user", "role": "user", "content": "Plan a Course."}],
        "tools": [],
        "context": [],
        "forwardedProps": {"mode": "autonomous"},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        stream_status, stream_body = await _post_stream(app, "/api/agent", autonomous_input)
        course_response = await client.get("/api/course")
        chat_response = await client.get("/api/chat")

    assert stream_status == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream_body.splitlines()
        if line.startswith("data: ")
    ]
    event_types = [event["type"] for event in events]
    assert event_types[-1] == "RUN_FINISHED"
    outcome = events[-1].get("outcome", {})
    assert not outcome.get("interrupts"), "autonomous mode should not produce interrupts"

    assert course_response.status_code == 200
    plan = course_response.json()
    assert plan["title"] == "Self-driving Course"
    assert len(plan["lectures"]) == 2
    assert chat_response.json()["approval"] is None


@pytest.mark.anyio
async def test_cancelling_an_active_agent_run_preserves_partial_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "cancel-course"
    workspace.mkdir()
    server_started = asyncio.Event()
    finish = asyncio.Event()

    async def long_running_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        server_started.set()
        await finish.wait()
        yield "The Course Agent thought about your request but made no changes."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        agent_model=FunctionModel(stream_function=long_running_model),
        provider_validator=_verified_capabilities,
    )
    run_input = {
        "threadId": "course-agent",
        "runId": "cancel-me",
        "state": {},
        "messages": [{"id": "user-cancel", "role": "user", "content": "Do something long."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        running = asyncio.create_task(_post_stream(app, "/api/agent", run_input))
        await server_started.wait()
        cancel_response = await client.post("/api/agent/cancel")
        finish.set()
        stream_status, _ = await running
        course_response = await client.get("/api/course")

    assert cancel_response.status_code == 204
    assert not (workspace / "course.yaml").exists()
    assert course_response.status_code == 404


@pytest.mark.anyio
async def test_cancel_endpoint_is_unavailable_when_no_workspace_is_bound() -> None:
    transport = httpx2.ASGITransport(app=create_app())

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/agent/cancel")

    assert response.status_code == 409
    assert response.json() == {"detail": "No Course Workspace is active"}


@pytest.mark.anyio
async def test_agent_can_archive_and_restore_slides(tmp_path: Path) -> None:
    workspace = tmp_path / "archive-course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    from course_harness.course_plan import (
        CoursePlanInput,
        LectureInput,
        create_course_plan,
        create_course_plan_file,
        initialize_workspace_history,
    )
    from course_harness.presentation import (
        BulletsSlide,
        Presentation,
        TitleSlide,
        write_presentation,
    )

    plan = create_course_plan(
        CoursePlanInput(
            title="Archive test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    slide_a = TitleSlide(id="slide-aaa111222333", title="Keep me")
    slide_b = BulletsSlide(id="slide-bbb444555666", title="Archive me", bullets=["X"])
    pres = Presentation(
        id="presentation-abc123def456",
        lecture_id=plan.lectures[0].id,
        slides=[slide_a, slide_b],
    )
    write_presentation(workspace, pres)

    async def archive_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name="archive_slide",
                json_args=json.dumps({"slide_id": "slide-bbb444555666", "archived": True}),
                tool_call_id="archive-1",
            )
        }

    app = create_app(
        workspace,
        provider_store_path=provider_store,
        agent_model=FunctionModel(stream_function=archive_model),
        provider_validator=_verified_capabilities,
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        _, body = await _post_stream(
            app,
            "/api/agent",
            {
                "threadId": "test",
                "runId": "archive-run",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "Archive that slide"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    # Autonomous mode — no interrupt for archive_slide (requires_approval=False by default)
    state_events = [e for e in events if e["type"] == "STATE_SNAPSHOT"]
    assert state_events, "archive should emit STATE_SNAPSHOT"
    assert "presentations" in state_events[0]["snapshot"]

    from course_harness.presentation import read_presentation_for_lecture

    result = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert result is not None
    archives = [s for s in result.slides if s.archived]
    actives = [s for s in result.slides if not s.archived]
    assert len(archives) == 1
    assert archives[0].id == "slide-bbb444555666"
    assert len(actives) == 1
    assert actives[0].id == "slide-aaa111222333"


@pytest.mark.anyio
async def test_agent_can_delete_presentation_and_requires_approval_in_guided(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "delete-course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    from course_harness.course_plan import (
        CoursePlanInput,
        LectureInput,
        create_course_plan,
        create_course_plan_file,
        initialize_workspace_history,
    )
    from course_harness.presentation import (
        Presentation,
        TitleSlide,
        write_presentation,
    )

    plan = create_course_plan(
        CoursePlanInput(
            title="Delete test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    pres = Presentation(
        id="presentation-abc123def456",
        lecture_id=plan.lectures[0].id,
        slides=[TitleSlide(id="slide-aaa111222333", title="Intro")],
    )
    write_presentation(workspace, pres)

    async def delete_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in _messages
        )
        if not tool_has_returned:
            yield {
                0: DeltaToolCall(
                    name="delete_presentation",
                    json_args=json.dumps({"lecture_id": plan.lectures[0].id}),
                    tool_call_id="delete-1",
                )
            }
        else:
            yield "Presentation deleted."

    app = create_app(
        workspace,
        provider_store_path=provider_store,
        agent_model=FunctionModel(stream_function=delete_model),
        provider_validator=_verified_capabilities,
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        _, body = await _post_stream(
            app,
            "/api/agent",
            {
                "threadId": "test",
                "runId": "delete-run",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "Delete the presentation"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    # Guided mode — delete_presentation should produce an interrupt
    outcome = events[-1].get("outcome", {})
    interrupts = outcome.get("interrupts", [])
    assert interrupts, "Guided mode should produce an interrupt for delete_presentation"

    # Now approve the deletion
    interrupt = interrupts[0]
    _, approve_body = await _post_stream(
        app,
        "/api/agent",
        {
            "threadId": "test",
            "runId": "delete-approve",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "resume": [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": True},
                }
            ],
        },
    )
    approve_events = [
        json.loads(line.removeprefix("data: "))
        for line in approve_body.splitlines()
        if line.startswith("data: ")
    ]
    assert approve_events[-1]["type"] == "RUN_FINISHED"

    from course_harness.presentation import read_presentation_for_lecture

    result = read_presentation_for_lecture(workspace, plan.lectures[0].id)
    assert result is None, "Presentation should be deleted after approval"


@pytest.mark.anyio
async def test_presentation_approval_preview_contains_slide_outline(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "preview-course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    from course_harness.course_plan import (
        CoursePlanInput,
        LectureInput,
        create_course_plan,
        create_course_plan_file,
        initialize_workspace_history,
    )

    plan = create_course_plan(
        CoursePlanInput(
            title="Preview test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)

    async def pres_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name="replace_presentation",
                json_args=json.dumps(
                    {
                        "command": {
                            "lecture_id": plan.lectures[0].id,
                            "slides": [
                                {"layout": "title", "title": "Welcome"},
                                {"layout": "bullets", "title": "Key points", "bullets": ["A", "B"]},
                                {"layout": "closing", "title": "Thanks"},
                            ],
                        }
                    }
                ),
                tool_call_id="pres-1",
            )
        }

    app = create_app(
        workspace,
        provider_store_path=provider_store,
        agent_model=FunctionModel(stream_function=pres_model),
        provider_validator=_verified_capabilities,
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        _, body = await _post_stream(
            app,
            "/api/agent",
            {
                "threadId": "test",
                "runId": "pres-run",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "Create slides"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    outcome = events[-1].get("outcome", {})
    interrupts = outcome.get("interrupts", [])
    assert interrupts, "Guided mode should produce an interrupt for replace_presentation"

    interrupt_message = interrupts[0].get("message", "")
    assert "replace_presentation(" in interrupt_message
    assert '"layout": "title"' in interrupt_message
    assert '"slides":' in interrupt_message
    assert "Welcome" in interrupt_message


@pytest.mark.anyio
async def test_presentation_state_snapshots_stream_on_canvas_updates(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "canvas-course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    from course_harness.course_plan import (
        CoursePlanInput,
        LectureInput,
        create_course_plan,
        create_course_plan_file,
        initialize_workspace_history,
    )

    plan = create_course_plan(
        CoursePlanInput(
            title="Canvas test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    lecture_id = plan.lectures[0].id

    call_count = 0

    async def canvas_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal call_count
        call_count += 1
        tool_has_returned = any(
            isinstance(msg, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in msg.parts)
            for msg in _messages
        )
        if not tool_has_returned:
            if call_count == 1:
                yield {
                    0: DeltaToolCall(
                        name="replace_presentation",
                        json_args=json.dumps(
                            {
                                "command": {
                                    "lecture_id": lecture_id,
                                    "slides": [
                                        {
                                            "layout": "title",
                                            "title": "Welcome",
                                            "purpose": "Opening",
                                        },
                                        {
                                            "layout": "bullets",
                                            "title": "Key points",
                                            "bullets": ["A", "B"],
                                        },
                                        {"layout": "closing", "title": "Summary"},
                                    ],
                                }
                            }
                        ),
                        tool_call_id="canvas-1",
                    )
                }
            elif call_count == 2:
                yield {
                    0: DeltaToolCall(
                        name="archive_slide",
                        json_args=json.dumps({"slide_id": "preserve", "archived": True}),
                        tool_call_id="canvas-2",
                    )
                }
        else:
            yield "Done."

    app = create_app(
        workspace,
        provider_store_path=provider_store,
        agent_model=FunctionModel(stream_function=canvas_model),
        provider_validator=_verified_capabilities,
    )

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())

        _, body = await _post_stream(
            app,
            "/api/agent",
            {
                "threadId": "canvas",
                "runId": "canvas-create",
                "state": {},
                "messages": [{"id": "u1", "role": "user", "content": "Create slides"}],
                "tools": [],
                "context": [],
                "forwardedProps": {},
            },
        )

    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    outcome = events[-1].get("outcome", {})
    interrupts = outcome.get("interrupts", [])
    assert interrupts, "Guided mode should produce an interrupt for replace_presentation"

    # Approve and check the state snapshots from the approval run
    interrupt = interrupts[0]
    _, approve_body = await _post_stream(
        app,
        "/api/agent",
        {
            "threadId": "canvas",
            "runId": "canvas-approve",
            "state": {},
            "messages": [],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "resume": [
                {
                    "interruptId": interrupt["id"],
                    "status": "resolved",
                    "payload": {"approved": True},
                }
            ],
        },
    )
    approve_events = [
        json.loads(line.removeprefix("data: "))
        for line in approve_body.splitlines()
        if line.startswith("data: ")
    ]
    assert approve_events[-1]["type"] == "RUN_FINISHED"

    state_snapshots = [e for e in approve_events if e["type"] == "STATE_SNAPSHOT"]
    assert len(state_snapshots) >= 1, "Should emit at least one state snapshot after create"

    last_snapshot = state_snapshots[-1]["snapshot"]
    assert "presentations" in last_snapshot, "State snapshot must include presentations"
    assert len(last_snapshot["presentations"]) == 1
    pres = last_snapshot["presentations"][0]
    assert pres["lecture_id"] == lecture_id
    assert len(pres["slides"]) == 3
    layouts = [s["layout"] for s in pres["slides"]]
    assert layouts == ["title", "bullets", "closing"]

    from course_harness.presentation import read_presentation_for_lecture

    stored = read_presentation_for_lecture(workspace, lecture_id)
    assert stored is not None
    assert len(stored.slides) == 3
