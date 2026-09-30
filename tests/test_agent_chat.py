import asyncio
import hashlib
import itertools
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel
from starlette.responses import StreamingResponse

from course_harness import canonical_mutation
from course_harness import providers as providers_module
from course_harness import workspace_history as history
from course_harness.app import create_app
from course_harness.chat_history import (
    COURSE_STATE_HEADING,
    UNFINISHED_TOOL_RESULT,
    active_conversation_id,
    close_unfinished_tool_calls,
    read_chat_history,
    read_chat_transcript,
    save_chat_history,
)
from course_harness.course_agent import (
    CourseAgentDeps,
    CourseAgentState,
    CoursePlanLectureCommand,
    ReplaceCoursePlanCommand,
    _build_course_agent,
    apply_course_plan_command,
    build_provider_model,
)
from course_harness.course_plan import read_course_plan, write_course_plan
from course_harness.providers import (
    ContextWindowUnknownError,
    ModelSuggestion,
    ProviderAccountRequest,
    ProviderCapabilities,
    ProviderConfigurationRequest,
    ProviderValidationError,
    list_provider_models,
    save_provider_account,
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "model", "base_url"),
    [
        ("openrouter", "openai/gpt-oss-20b:free", "https://openrouter.ai/api/v1"),
        ("anthropic", "claude-sonnet", "https://api.anthropic.com"),
        ("openai-compatible", "local-model", "http://127.0.0.1:11434/v1"),
    ],
)
async def test_provider_models_own_clients_that_ignore_ambient_proxy_settings(
    kind: str, model: str, base_url: str
) -> None:
    configured = {
        "kind": kind,
        "model": model,
        "base_url": base_url,
        "capabilities": {
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "context_window": 131_072,
            "vision": False,
        },
    }

    provider_model = build_provider_model(configured, "explicit-product-key")
    provider = provider_model._provider  # type: ignore[unresolved-attribute]
    assert provider._own_http_client is not None
    assert provider._http_client_factory is not None
    client = provider._own_http_client
    replacement = provider._http_client_factory()
    try:
        assert client._trust_env is False
        assert replacement._trust_env is False
        assert client.timeout.connect == 5
        assert client.timeout.read == 600
        assert client.timeout.write == 600
        assert client.timeout.pool == 600
        assert replacement.timeout.connect == 5
        assert replacement.timeout.read == 600
        assert replacement.timeout.write == 600
        assert replacement.timeout.pool == 600
    finally:
        await client.aclose()
        await replacement.aclose()


def _plan_command(**changes: object) -> ReplaceCoursePlanCommand:
    values: dict[str, object] = {
        "title": "Causal Inference",
        "audience": "Applied researchers",
        "lectures": [{"title": "Foundations"}],
    }
    values.update(changes)
    return ReplaceCoursePlanCommand.model_validate(values)


def test_course_plan_revisions_cannot_silently_replace_lecture_identity(tmp_path: Path) -> None:
    from course_harness.course_plan import write_course_plan

    workspace = tmp_path / "course"
    workspace.mkdir()
    original = apply_course_plan_command(workspace, _plan_command())
    original = original.model_copy(
        update={
            "template_profile_id": "tpl-000000000001",
            "template_profile_version": 3,
        }
    )
    write_course_plan(workspace, original)

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
    assert updated.template_profile_id == original.template_profile_id
    assert updated.template_profile_version == original.template_profile_version


def test_agent_course_command_preserves_a_same_path_external_edit_during_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    original = apply_course_plan_command(workspace, _plan_command())
    expected = canonical_mutation.capture_canonical_file(workspace, "course.yaml")

    def external_edit(path: Path, _expected: object) -> None:
        monkeypatch.setattr(canonical_mutation, "after_precondition_check", None)
        write_course_plan(
            path,
            original.model_copy(update={"title": "External edit"}),
        )

    monkeypatch.setattr(canonical_mutation, "after_precondition_check", external_edit)
    with pytest.raises(canonical_mutation.CanonicalMutationConflict):
        apply_course_plan_command(
            workspace,
            _plan_command(
                lectures=[
                    CoursePlanLectureCommand(id=original.lectures[0].id, title="Application edit")
                ]
            ),
            expected=expected,
        )

    current = read_course_plan(workspace)
    assert current is not None
    assert current.title == "External edit"


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


def _stream_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _make_cross_file_drift(workspace: Path) -> str:
    """Point a valid Course Plan at a missing Source without breaking either YAML file."""
    original = (workspace / "course.yaml").read_text(encoding="utf-8")
    plan = read_course_plan(workspace)
    assert plan is not None
    lecture = plan.lectures[0].model_copy(update={"source_focus": ["source-missing"]})
    write_course_plan(workspace, plan.model_copy(update={"lectures": [lecture]}))
    return original


@pytest.mark.anyio
async def test_validation_errors_do_not_echo_provider_secrets(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    secret = "rejected-provider-secret-that-must-not-leak"
    transport = httpx2.ASGITransport(
        app=create_app(workspace, provider_store_path=tmp_path / "provider")
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/provider-accounts",
            json={"name": "", "kind": "invalid-provider", "api_key": secret},
        )

    assert response.status_code == 422
    assert secret not in response.text
    assert secret not in caplog.text


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
async def test_remote_http_provider_requires_explicit_opt_in(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=tmp_path / "provider",
            provider_validator=_verified_capabilities,
            provider_account_validator=_verified_account,
        )
    )
    request = {
        "name": "Blaze",
        "kind": "openai-compatible",
        "api_key": "local-secret",
        "base_url": "http://blaze.home:8081/v1",
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post("/api/provider-accounts", json=request)
        accepted = await client.post(
            "/api/provider-accounts", json={**request, "allow_insecure_http": True}
        )
        rotated = await client.patch(
            f"/api/provider-accounts/{accepted.json()['id']}/credential",
            json={"api_key": "new-local-secret"},
        )
        preset = await client.post(
            "/api/models",
            json={
                "name": "Local model",
                "provider_account_id": accepted.json()["id"],
                "model": "local-model",
            },
        )

    assert rejected.status_code == 422
    assert "HTTP" in rejected.text
    assert accepted.status_code == 201
    assert accepted.json()["base_url"] == request["base_url"]
    assert rotated.status_code == 200
    assert preset.status_code == 201


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
async def test_provider_account_credential_can_be_rotated_without_replacing_the_account(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "providers"
    validated_keys: list[str] = []

    async def validate_account(request: ProviderAccountRequest) -> None:
        key = request.secret()
        assert key is not None
        validated_keys.append(key)
        if key == "rejected-secret":
            raise ProviderValidationError("The replacement credential was rejected.")

    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=_verified_capabilities,
            provider_account_validator=validate_account,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        account = await client.post(
            "/api/provider-accounts",
            json={
                "name": "My OpenRouter",
                "kind": "openrouter",
                "api_key": "original-secret",
            },
        )
        account_id = account.json()["id"]
        preset = await client.post(
            "/api/models",
            json={
                "name": "Planning",
                "provider_account_id": account_id,
                "model": "openai/gpt-oss-20b:free",
            },
        )
        rejected = await client.patch(
            f"/api/provider-accounts/{account_id}/credential",
            json={"api_key": "rejected-secret"},
        )
        rotated = await client.patch(
            f"/api/provider-accounts/{account_id}/credential",
            json={"api_key": "replacement-secret"},
        )
        catalog = await client.get("/api/models")

    assert account.status_code == 201
    assert preset.status_code == 201
    assert rejected.status_code == 422
    assert rotated.status_code == 200
    assert rotated.json() == account.json()
    assert catalog.json()["model_presets"][0]["provider_account_id"] == account_id
    assert validated_keys == ["original-secret", "rejected-secret", "replacement-secret"]
    credentials = (provider_store / "credentials.json").read_text(encoding="utf-8")
    assert "replacement-secret" in credentials
    assert "original-secret" not in credentials
    assert "rejected-secret" not in credentials


@pytest.mark.anyio
async def test_provider_account_deletion_requires_explicit_preset_cleanup(tmp_path: Path) -> None:
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
        first_account = await client.post(
            "/api/provider-accounts",
            json={"name": "Expired", "kind": "openrouter", "api_key": "expired-secret"},
        )
        second_account = await client.post(
            "/api/provider-accounts",
            json={"name": "Current", "kind": "openrouter", "api_key": "current-secret"},
        )
        first_preset = await client.post(
            "/api/models",
            json={
                "name": "Old model",
                "provider_account_id": first_account.json()["id"],
                "model": "old/model",
            },
        )
        second_preset = await client.post(
            "/api/models",
            json={
                "name": "Current model",
                "provider_account_id": second_account.json()["id"],
                "model": "current/model",
            },
        )
        selected = await client.put(
            "/api/models/selected", json={"model_id": first_preset.json()["id"]}
        )
        guarded = await client.delete(f"/api/provider-accounts/{first_account.json()['id']}")
        deleted = await client.delete(
            f"/api/provider-accounts/{first_account.json()['id']}?delete_model_presets=true"
        )

    assert first_preset.status_code == 201
    assert second_preset.status_code == 201
    assert selected.status_code == 200
    assert guarded.status_code == 409
    assert "1 Model Preset" in guarded.json()["detail"]
    assert deleted.status_code == 200
    assert deleted.json()["provider_accounts"] == [second_account.json()]
    assert deleted.json()["model_presets"] == [second_preset.json()]
    assert deleted.json()["selected_model_id"] == second_preset.json()["id"]
    credentials = (provider_store / "credentials.json").read_text(encoding="utf-8")
    assert "current-secret" in credentials
    assert "expired-secret" not in credentials


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
async def test_credential_storage_failures_are_actionable_and_do_not_echo_the_secret(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"
    provider_store.mkdir(parents=True)
    (provider_store / "credential-modes.json").write_text("not json\n", encoding="utf-8")
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=_verified_capabilities,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/provider", json=_provider_request())

    assert response.status_code == 503
    assert "credential storage" in response.json()["detail"].lower()
    assert "openrouter-secret" not in response.text


@pytest.mark.anyio
async def test_provider_configuration_disk_failures_are_safe_and_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_store = tmp_path / "user-data" / "provider"

    def disk_full(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(providers_module, "_write_private_json", disk_full)
    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=_verified_capabilities,
        )
    )

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/provider", json=_provider_request())

    assert response.status_code == 503
    assert "credential storage" in response.json()["detail"].lower()
    assert "openrouter-secret" not in response.text


@pytest.mark.anyio
async def test_legacy_catalog_migration_disk_failures_are_safe_and_actionable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
        configured = await client.put("/api/provider", json=_provider_request())
        (provider_store / "catalog.json").unlink()

        def disk_full(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(providers_module, "_write_catalog", disk_full)
        response = await client.get("/api/models")

    assert configured.status_code == 200
    assert response.status_code == 503
    assert "credential storage" in response.json()["detail"].lower()
    assert "openrouter-secret" not in response.text


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
async def test_llamacpp_model_metadata_and_tool_call_are_verified() -> None:
    calls: list[str] = []

    def local_provider(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx2.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "qwen3.8-27b",
                            "owned_by": "llamacpp",
                            "meta": {"n_ctx": 140_032},
                        }
                    ]
                },
            )
        if request.url.path == "/props":
            return httpx2.Response(200, json={"modalities": {"vision": False}})
        assert request.url.path == "/v1/chat/completions"
        return httpx2.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "add_numbers",
                                        "arguments": '{"a":1,"b":2}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            },
        )

    request = ProviderConfigurationRequest.model_validate(
        {
            "kind": "openai-compatible",
            "model": "qwen3.8-27b",
            "api_key": "local-secret",
            "base_url": "http://127.0.0.1:8081/v1",
        }
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(local_provider)) as client:
        capabilities = await validate_provider_capabilities(request, http_client=client)

    assert calls == ["/v1/models", "/props", "/v1/chat/completions"]
    assert capabilities.tool_calling is True
    assert capabilities.context_window == 140_032
    assert capabilities.vision is False


def _llamacpp_provider(
    *, listing_capabilities: list[str], props_vision: bool | None
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/models":
            return httpx2.Response(
                200,
                json={
                    "models": [{"name": "qwen3.8-27b", "capabilities": listing_capabilities}],
                    "data": [{"id": "qwen3.8-27b", "meta": {"n_ctx": 140_032}}],
                },
            )
        if request.url.path == "/props":
            if props_vision is None:
                return httpx2.Response(404)
            assert request.url.params["model"] == "qwen3.8-27b"
            return httpx2.Response(200, json={"modalities": {"vision": props_vision}})
        tool_call = {"type": "function", "function": {"name": "add_numbers", "arguments": "{}"}}
        return httpx2.Response(200, json={"choices": [{"message": {"tool_calls": [tool_call]}}]})

    return httpx2.MockTransport(handler)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("listing_capabilities", "props_vision", "vision"),
    [
        (["completion", "multimodal"], None, True),
        (["completion"], True, True),
        (["completion"], None, False),
    ],
)
async def test_llamacpp_vision_is_read_from_its_model_listing_or_server_props(
    listing_capabilities: list[str], props_vision: bool | None, vision: bool
) -> None:
    request = ProviderConfigurationRequest.model_validate(
        {
            "kind": "openai-compatible",
            "model": "qwen3.8-27b",
            "api_key": "local-secret",
            "base_url": "http://127.0.0.1:8081/v1",
            "allow_insecure_http": True,
        }
    )
    transport = _llamacpp_provider(
        listing_capabilities=listing_capabilities, props_vision=props_vision
    )
    async with httpx2.AsyncClient(transport=transport) as client:
        capabilities = await validate_provider_capabilities(request, http_client=client)

    assert capabilities.vision is vision


def _vllm_provider(*, max_model_len: int | None) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/models":
            entry: dict[str, object] = {"id": "Qwen/Qwen3-32B", "owned_by": "vllm"}
            if max_model_len is not None:
                entry["max_model_len"] = max_model_len
            return httpx2.Response(200, json={"object": "list", "data": [entry]})
        if request.url.path == "/props":
            return httpx2.Response(404)
        tool_call = {"type": "function", "function": {"name": "add_numbers", "arguments": "{}"}}
        return httpx2.Response(200, json={"choices": [{"message": {"tool_calls": [tool_call]}}]})

    return httpx2.MockTransport(handler)


def _vllm_request(context_window: int | None = None) -> ProviderConfigurationRequest:
    return ProviderConfigurationRequest(
        kind="openai-compatible",
        model="Qwen/Qwen3-32B",
        api_key=SecretStr("local-secret"),
        base_url="https://vllm.example/v1",
        context_window=context_window,
    )


@pytest.mark.anyio
async def test_vllm_reports_its_context_window_as_max_model_len() -> None:
    async with httpx2.AsyncClient(transport=_vllm_provider(max_model_len=40_960)) as client:
        capabilities = await validate_provider_capabilities(_vllm_request(), http_client=client)

    assert capabilities.context_window == 40_960


@pytest.mark.anyio
async def test_an_entered_context_window_fills_in_when_the_provider_reports_none() -> None:
    async with httpx2.AsyncClient(transport=_vllm_provider(max_model_len=None)) as client:
        with pytest.raises(ContextWindowUnknownError):
            await validate_provider_capabilities(_vllm_request(), http_client=client)
        capabilities = await validate_provider_capabilities(
            _vllm_request(context_window=32_768), http_client=client
        )

    assert capabilities.context_window == 32_768


@pytest.mark.anyio
async def test_a_reported_context_window_wins_over_an_entered_one() -> None:
    async with httpx2.AsyncClient(transport=_vllm_provider(max_model_len=40_960)) as client:
        capabilities = await validate_provider_capabilities(
            _vllm_request(context_window=32_768), http_client=client
        )

    assert capabilities.context_window == 40_960


@pytest.mark.anyio
async def test_a_model_preset_asks_for_and_keeps_an_unreported_context_window(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_path = tmp_path / "provider"
    account = save_provider_account(
        provider_path,
        ProviderAccountRequest(
            name="Team vLLM",
            kind="openai-compatible",
            api_key=SecretStr("local-secret"),
            base_url="https://vllm.example/v1",
        ),
    )
    entered: list[int | None] = []

    async def validator(request: ProviderConfigurationRequest) -> ProviderCapabilities:
        entered.append(request.context_window)
        async with httpx2.AsyncClient(transport=_vllm_provider(max_model_len=None)) as client:
            return await validate_provider_capabilities(request, http_client=client)

    app = create_app(workspace, provider_store_path=provider_path, provider_validator=validator)
    preset = {"name": "Qwen", "provider_account_id": account.id, "model": "Qwen/Qwen3-32B"}
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        unknown = await client.post("/api/models", json=preset)
        too_small = await client.post("/api/models", json=preset | {"context_window": 8_192})
        saved = await client.post("/api/models", json=preset | {"context_window": 32_768})
        verified = await client.post(f"/api/models/{saved.json()['id']}/verify")

    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "context_window_unknown"
    assert too_small.status_code == 422
    assert "16,384" in too_small.json()["detail"]
    assert saved.status_code == 201
    assert saved.json()["capabilities"]["context_window"] == 32_768
    assert saved.json()["entered_context_window"] == 32_768
    assert verified.status_code == 200
    assert entered == [None, 8_192, 32_768, 32_768]


@pytest.mark.anyio
async def test_openai_compatible_accounts_suggest_their_reported_models(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_path = tmp_path / "provider"
    listing = _llamacpp_provider(listing_capabilities=["multimodal"], props_vision=None)

    async def list_models(store_path: Path, account_id: str) -> list[ModelSuggestion]:
        async with httpx2.AsyncClient(transport=listing) as client:
            return await list_provider_models(store_path, account_id, http_client=client)

    account = save_provider_account(
        provider_path,
        ProviderAccountRequest(
            name="Home llama.cpp",
            kind="openai-compatible",
            api_key=SecretStr("local-secret"),
            base_url="http://blaze.home:8081/v1",
            allow_insecure_http=True,
        ),
    )
    app = create_app(
        workspace, provider_store_path=provider_path, provider_model_lister=list_models
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        suggestions = await client.get(f"/api/provider-accounts/{account.id}/models")
        missing = await client.get("/api/provider-accounts/provider-000000000000/models")

    assert suggestions.json() == [{"id": "qwen3.8-27b", "vision": True, "context_window": 140_032}]
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_local_model_without_tool_call_is_rejected() -> None:
    def local_provider(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v1/models":
            return httpx2.Response(
                200,
                json={"data": [{"id": "plain-model", "meta": {"n_ctx": 32_768}}]},
            )
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": "I cannot call tools."}}]}
        )

    request = ProviderConfigurationRequest.model_validate(
        {
            "kind": "openai-compatible",
            "model": "plain-model",
            "api_key": "local-secret",
            "base_url": "http://127.0.0.1:8081/v1",
        }
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(local_provider)) as client:
        with pytest.raises(ProviderValidationError, match="did not return a tool call"):
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
        "forwardedProps": {"mode": "guided"},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        before_approval = await client.get("/api/course")
        history_before_approval = (workspace / ".git").exists()
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
    assert history_before_approval is False
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
    assert not list((workspace / ".git" / "refs" / "course-harness" / "recovery").glob("*"))
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
        clear_response = await client.delete("/api/chat")
        cleared_chat = await client.get("/api/chat")

    assert reopened_chat.json() == chat_response.json()
    assert reopened_course.json() == plan
    assert clear_response.status_code == 204
    assert cleared_chat.json() == {"approval": None, "messages": []}


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
        chat_store_path=tmp_path / "chat",
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
async def test_agent_setup_failure_releases_the_workspace_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(
            lambda _messages, _info: ModelResponse(parts=[TextPart(content="unused")])
        ),
        provider_validator=_verified_capabilities,
    )
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    run_input = {
        "threadId": "course-agent",
        "runId": "invalid-mode-run",
        "state": {},
        "messages": [{"id": "user-1", "role": "user", "content": "Plan a Course."}],
        "tools": [],
        "context": [],
        "forwardedProps": {"mode": "not-a-mode"},
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        failed = await client.post("/api/agent", json=run_input)
        next_mutation = await client.post(
            "/api/course",
            json={
                "title": "Lock released",
                "audience": "Authors",
                "lectures": [{"title": "One"}],
            },
        )

    assert failed.status_code == 500
    assert next_mutation.status_code == 201


@pytest.mark.anyio
async def test_autonomous_agent_can_create_a_semantic_revision_without_deadlock(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    calls = 0

    async def revising_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {
                0: DeltaToolCall(
                    name="replace_course_plan",
                    json_args=json.dumps(
                        {
                            "command": {
                                "title": "Revision Course",
                                "audience": "Authors",
                                "lectures": [{"title": "One"}],
                            }
                        }
                    ),
                    tool_call_id="course-plan-1",
                )
            }
        elif calls == 2:
            yield {
                0: DeltaToolCall(
                    name="create_course_revision",
                    json_args=json.dumps({"summary": "Create the initial Course Plan"}),
                    tool_call_id="revision-1",
                )
            }
        else:
            yield "The Course Plan and Revision are ready."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(stream_function=revising_model),
        provider_validator=_verified_capabilities,
    )
    transport = httpx2.ASGITransport(app=app)
    run_input = {
        "threadId": "course-agent",
        "runId": "revision-run",
        "state": {},
        "messages": [{"id": "user-1", "role": "user", "content": "Create the Course."}],
        "tools": [],
        "context": [],
        "forwardedProps": {"mode": "autonomous"},
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        stream_status, _ = await _post_stream(app, "/api/agent", run_input)
        revisions = await client.get("/api/workspace/revisions")

    assert stream_status == 200
    assert [revision["summary"] for revision in revisions.json()] == [
        "Create the initial Course Plan"
    ]


@pytest.mark.anyio
async def test_guided_agent_approval_creates_a_semantic_revision(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()

    async def revising_model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if tool_has_returned:
            yield "The Course Revision is ready."
            return
        yield {
            0: DeltaToolCall(
                name="create_course_revision",
                json_args=json.dumps({"summary": "Capture the initial Course Plan"}),
                tool_call_id="semantic-revision-1",
            )
        }

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(stream_function=revising_model),
        provider_validator=_verified_capabilities,
    )
    run_input = {
        "threadId": "course-agent",
        "runId": "guided-revision-proposal",
        "state": {},
        "messages": [{"id": "user-1", "role": "user", "content": "Save this milestone."}],
        "tools": [],
        "context": [],
        "forwardedProps": {"mode": "guided"},
    }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        created_course = await client.post(
            "/api/course",
            json={
                "title": "Guided Revision Course",
                "audience": "Course Authors",
                "lectures": [{"title": "One"}],
            },
        )
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        proposal_events = [
            json.loads(line.removeprefix("data: "))
            for line in proposal_body.splitlines()
            if line.startswith("data: ")
        ]
        interrupt = proposal_events[-1]["outcome"]["interrupts"][0]
        revisions_before_approval = await client.get("/api/workspace/revisions")
        approval_status, _ = await _post_stream(
            app,
            "/api/agent",
            {
                **run_input,
                "runId": "guided-revision-approval",
                "messages": [],
                "resume": [
                    {
                        "interruptId": interrupt["id"],
                        "status": "resolved",
                        "payload": {"approved": True},
                    }
                ],
            },
        )
        revisions = await client.get("/api/workspace/revisions")

    assert created_course.status_code == 201
    assert proposal_status == 200
    assert interrupt["id"] == "int-semantic-revision-1"
    assert revisions_before_approval.json() == []
    assert approval_status == 200
    assert [revision["summary"] for revision in revisions.json()] == [
        "Capture the initial Course Plan"
    ]


def _reconciliation_model(repaired_course: str) -> FunctionModel:
    async def propose_repair(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if tool_has_returned:
            yield "The reviewed reconciliation is complete."
            return
        yield {
            0: DeltaToolCall(
                name="apply_reconciliation_patch",
                json_args=json.dumps(
                    {
                        "summary": "Repair the missing Source reference",
                        "entries": [{"path": "course.yaml", "content": repaired_course}],
                    }
                ),
                tool_call_id="reconciliation-1",
            )
        }

    return FunctionModel(stream_function=propose_repair)


async def _create_reconciliation_app(
    tmp_path: Path,
) -> tuple[Path, Any, str, dict[str, object]]:
    workspace = tmp_path / "reconciliation-course"
    workspace.mkdir()
    bootstrap = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        provider_validator=_verified_capabilities,
    )
    transport = httpx2.ASGITransport(app=bootstrap)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/course",
            json={
                "title": "Reconciliation Course",
                "audience": "Course Authors",
                "lectures": [{"title": "Cross-file references"}],
            },
        )
    assert created.status_code == 201
    repaired_course = _make_cross_file_drift(workspace)
    context = history.capture_reconciliation_context(workspace)
    assert context.findings
    run_input: dict[str, object] = {
        "threadId": "course-agent",
        "runId": "reconciliation-proposal",
        "state": {},
        "messages": [
            {
                "id": "user-1",
                "role": "user",
                "content": "Reconcile the inconsistent Workspace Drift.",
            }
        ],
        "tools": [],
        "context": [],
        "forwardedProps": {
            "mode": "autonomous",
            "reconciliationDriftId": context.drift_id,
        },
    }
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=_reconciliation_model(repaired_course),
        provider_validator=_verified_capabilities,
    )
    return workspace, app, context.drift_id, run_input


@pytest.mark.anyio
async def test_reconciliation_proposal_can_be_declined_without_changing_invalid_drift(
    tmp_path: Path,
) -> None:
    workspace, app, _drift_id, run_input = await _create_reconciliation_app(tmp_path)
    before = (workspace / "course.yaml").read_bytes()
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        interrupt = _stream_events(proposal_body)[-1]["outcome"]["interrupts"][0]
        decline_status, _ = await _post_stream(
            app,
            "/api/agent",
            {
                **run_input,
                "runId": "reconciliation-decline",
                "messages": [],
                "resume": [
                    {
                        "interruptId": interrupt["id"],
                        "status": "resolved",
                        "payload": {"approved": False},
                    }
                ],
            },
        )
        revisions = await client.get("/api/workspace/revisions")

    assert proposal_status == 200
    assert decline_status == 200
    assert (workspace / "course.yaml").read_bytes() == before
    assert not history.read_current_state(workspace).validation.valid
    assert revisions.json() == []


@pytest.mark.anyio
async def test_reconciliation_approval_repairs_cross_file_drift_and_records_revision(
    tmp_path: Path,
) -> None:
    workspace, app, _drift_id, run_input = await _create_reconciliation_app(tmp_path)
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        interrupt = _stream_events(proposal_body)[-1]["outcome"]["interrupts"][0]
        approval_status, _ = await _post_stream(
            app,
            "/api/agent",
            {
                **run_input,
                "runId": "reconciliation-approval",
                "messages": [],
                "resume": [
                    {
                        "interruptId": interrupt["id"],
                        "status": "resolved",
                        "payload": {"approved": True},
                    }
                ],
            },
        )
        revisions = await client.get("/api/workspace/revisions")

    state = history.read_current_state(workspace)
    assert proposal_status == 200
    assert approval_status == 200
    assert state.validation.valid
    assert state.drift == "clean"
    assert [revision["summary"] for revision in revisions.json()] == [
        "Repair the missing Source reference"
    ]


@pytest.mark.anyio
async def test_reconciliation_resume_rejects_a_stale_drift_id(tmp_path: Path) -> None:
    workspace, app, _drift_id, run_input = await _create_reconciliation_app(tmp_path)
    transport = httpx2.ASGITransport(app=app)

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        proposal_status, proposal_body = await _post_stream(app, "/api/agent", run_input)
        interrupt = _stream_events(proposal_body)[-1]["outcome"]["interrupts"][0]
        plan = read_course_plan(workspace)
        assert plan is not None
        write_course_plan(workspace, plan.model_copy(update={"title": "Changed after proposal"}))
        stale_status, stale_body = await _post_stream(
            app,
            "/api/agent",
            {
                **run_input,
                "runId": "reconciliation-stale-resume",
                "messages": [],
                "resume": [
                    {
                        "interruptId": interrupt["id"],
                        "status": "resolved",
                        "payload": {"approved": True},
                    }
                ],
            },
        )
        revisions = await client.get("/api/workspace/revisions")

    assert proposal_status == 200
    assert stale_status == 409
    assert "Workspace Drift changed" in stale_body
    assert revisions.json() == []


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
async def test_completed_agent_edits_are_trusted_before_the_run_ends(tmp_path: Path) -> None:
    """An interrupted run must not leave its finished edits looking like outside changes."""
    workspace = tmp_path / "checkpoint-course"
    workspace.mkdir()
    trusted_mid_run: list[dict[str, dict[str, str]] | None] = []

    async def planning_then_interrupted(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_has_returned = any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        )
        if not tool_has_returned:
            command = {
                "title": "Checkpointed",
                "audience": "Engineers",
                "lectures": [{"title": "Start"}],
            }
            yield {
                0: DeltaToolCall(
                    name="replace_course_plan",
                    json_args=json.dumps({"command": command}),
                    tool_call_id="checkpoint-plan",
                )
            }
            return
        trusted_mid_run.append(history._read_provenance(workspace))
        yield "Almost done"

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "user-data" / "provider",
        chat_store_path=tmp_path / "user-data" / "chat",
        agent_model=FunctionModel(stream_function=planning_then_interrupted),
        provider_validator=_verified_capabilities,
    )
    run_input = {
        "threadId": "course-agent",
        "runId": "checkpoint-run",
        "state": {},
        "messages": [{"id": "checkpoint-user", "role": "user", "content": "Plan a Course."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        stream_status, _body = await _post_stream(app, "/api/agent", run_input)

    assert stream_status == 200
    written = (workspace / "course.yaml").read_bytes()
    assert trusted_mid_run and trusted_mid_run[0] is not None
    assert trusted_mid_run[0]["course.yaml"]["sha256"] == hashlib.sha256(written).hexdigest()


@pytest.mark.anyio
async def test_agent_defaults_to_autonomous_changes_without_interrupt(
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
        "forwardedProps": {},
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

    async def long_running_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        server_started.set()
        await asyncio.Event().wait()
        yield "The Course Agent thought about your request but made no changes."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
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
        stream_status, _ = await asyncio.wait_for(running, timeout=2)
        course_response = await client.get("/api/course")
        next_mutation = await client.post(
            "/api/course",
            json={
                "title": "After cancellation",
                "audience": "Authors",
                "lectures": [{"title": "One"}],
            },
        )

    assert cancel_response.status_code == 204
    assert course_response.status_code == 404
    assert next_mutation.status_code == 201


@pytest.mark.anyio
async def test_late_stream_termination_releases_run_boundary_and_workspace_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "uncancellable-course"
    workspace.mkdir()
    stream_started = asyncio.Event()
    cancellation_suppressed = asyncio.Event()
    release_stream = asyncio.Event()

    class UnclosableIterator:
        def __aiter__(self) -> UnclosableIterator:
            return self

        async def __anext__(self) -> bytes:
            stream_started.set()
            while not release_stream.is_set():
                try:
                    await release_stream.wait()
                except asyncio.CancelledError:
                    cancellation_suppressed.set()
            return b"released"

    async def dispatch_request(*_args: object, **_kwargs: object) -> StreamingResponse:
        return StreamingResponse(UnclosableIterator(), media_type="text/event-stream")

    monkeypatch.setattr(
        "course_harness.app.AGUIAdapter.dispatch_request",
        dispatch_request,
    )
    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(
            lambda _messages, _info: ModelResponse(parts=[TextPart(content="unused")])
        ),
        provider_validator=_verified_capabilities,
    )
    transport = httpx2.ASGITransport(app=app)
    run_input = {
        "threadId": "course-agent",
        "runId": "uncancellable-run",
        "state": {},
        "messages": [{"id": "user-uncancellable", "role": "user", "content": "Wait."}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }

    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        created = await client.post(
            "/api/course",
            json={
                "title": "Existing Course",
                "audience": "Authors",
                "lectures": [{"title": "One"}],
            },
        )
        assert created.status_code == 201
        running = asyncio.create_task(_post_stream(app, "/api/agent", run_input))
        await stream_started.wait()
        cancelling = asyncio.create_task(client.post("/api/agent/cancel"))
        stream_status, _ = await asyncio.wait_for(running, timeout=2)
        assert cancellation_suppressed.is_set()
        assert not cancelling.done()
        competing = await client.patch(
            f"/api/course/lectures/{created.json()['lectures'][0]['id']}",
            json={"title": "Blocked while the stream is unconfirmed"},
        )
        release_stream.set()
        cancel_response = await asyncio.wait_for(cancelling, timeout=2)
        for _ in range(20):
            after_termination = await client.patch(
                f"/api/course/lectures/{created.json()['lectures'][0]['id']}",
                json={"title": "Allowed after the stream terminates"},
            )
            if after_termination.status_code != 409:
                break
            await asyncio.sleep(0.05)

    assert cancel_response.status_code == 204
    assert stream_status == 200
    assert competing.status_code == 409
    assert after_termination.status_code == 200
    assert not (workspace / ".git" / history.RUN_BOUNDARY_FILE).exists()


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
        write_course_plan,
    )
    from course_harness.presentation import (
        BulletsSlide,
        Presentation,
        TitleSlide,
        write_presentation,
    )
    from course_harness.workspace_history import record_app_authored_state

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
    plan = plan.model_copy(
        update={
            "lectures": [
                lecture.model_copy(update={"presentation_id": pres.id}) for lecture in plan.lectures
            ]
        }
    )
    write_course_plan(workspace, plan)
    record_app_authored_state(workspace)

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
        chat_store_path=tmp_path / "chat",
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
        write_course_plan,
    )
    from course_harness.presentation import (
        Presentation,
        TitleSlide,
        write_presentation,
    )
    from course_harness.workspace_history import record_app_authored_state

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
    plan = plan.model_copy(
        update={
            "lectures": [
                lecture.model_copy(update={"presentation_id": pres.id}) for lecture in plan.lectures
            ]
        }
    )
    write_course_plan(workspace, plan)
    record_app_authored_state(workspace)

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
        chat_store_path=tmp_path / "user-data" / "chat",
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
                "forwardedProps": {"mode": "guided"},
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
            "forwardedProps": {"mode": "guided"},
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
    from course_harness.workspace_history import record_app_authored_state

    plan = create_course_plan(
        CoursePlanInput(
            title="Preview test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    record_app_authored_state(workspace)

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
        chat_store_path=tmp_path / "user-data" / "chat",
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
                "forwardedProps": {"mode": "guided"},
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
    from course_harness.workspace_history import record_app_authored_state

    plan = create_course_plan(
        CoursePlanInput(
            title="Canvas test", audience="Test", lectures=[LectureInput(title="Lecture 1")]
        )
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    record_app_authored_state(workspace)
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
        chat_store_path=tmp_path / "user-data" / "chat",
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
                "forwardedProps": {"mode": "guided"},
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
            "forwardedProps": {"mode": "guided"},
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


@pytest.mark.anyio
async def test_agent_runs_use_the_active_conversation_history(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    seen_histories: list[str] = []

    async def responding_model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str]:
        seen_histories.append(" ".join(str(message) for message in messages))
        yield "Response saved."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=tmp_path / "chat",
        agent_model=FunctionModel(stream_function=responding_model),
        provider_validator=_verified_capabilities,
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        first = (await client.get("/api/conversations")).json()["active_id"]
        run = {
            "threadId": first,
            "runId": "first-run",
            "state": {},
            "messages": [{"id": "first-message", "role": "user", "content": "First thread topic"}],
            "tools": [],
            "context": [],
            "forwardedProps": {"mode": "guided"},
        }
        status, _ = await _post_stream(app, "/api/agent", run)
        assert status == 200
        second = (await client.post("/api/conversations", json={"title": "Second"})).json()[
            "active_id"
        ]
        stale = await client.post("/api/agent", json={**run, "runId": "stale-run"})
        assert stale.status_code == 409
        legacy_stale = await client.post(
            "/api/agent", json={**run, "threadId": "course-agent", "runId": "legacy-stale"}
        )
        assert legacy_stale.status_code == 409
        status, _ = await _post_stream(
            app,
            "/api/agent",
            {
                **run,
                "threadId": second,
                "runId": "second-run",
                "messages": [
                    {"id": "second-message", "role": "user", "content": "Second thread topic"}
                ],
            },
        )
        assert status == 200
        assert "First thread topic" not in seen_histories[-1]
        assert "Second thread topic" in seen_histories[-1]
        assert (await client.get(f"/api/conversations/{first}")).json()["messages"][0][
            "content"
        ] == "First thread topic"
        assert (await client.get("/api/chat")).json()["messages"][0][
            "content"
        ] == "Second thread topic"


@pytest.mark.anyio
async def test_a_saved_model_preset_can_be_checked_again(tmp_path: Path) -> None:
    """Presets saved before vision was detected keep their stale capabilities until rechecked."""
    workspace = tmp_path / "course"
    workspace.mkdir()
    provider_path = tmp_path / "provider"
    account = save_provider_account(
        provider_path,
        ProviderAccountRequest(
            name="Home llama.cpp",
            kind="openai-compatible",
            api_key=SecretStr("local-secret"),
            base_url="http://blaze.home:8081/v1",
            allow_insecure_http=True,
        ),
    )
    stale = await _verified_capabilities(None)
    preset = providers_module.save_model_preset(
        provider_path,
        providers_module.ModelPresetRequest(
            name="Qwen", provider_account_id=account.id, model="qwen3.8-27b"
        ),
        stale,
    )
    checked: list[str] = []

    async def vision_capabilities(request: ProviderConfigurationRequest) -> ProviderCapabilities:
        checked.append(request.model)
        return stale.model_copy(update={"vision": True})

    app = create_app(
        workspace, provider_store_path=provider_path, provider_validator=vision_capabilities
    )
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        verified = await client.post(f"/api/models/{preset.id}/verify")
        missing = await client.post("/api/models/model-000000000000/verify")

    assert checked == ["qwen3.8-27b"]
    rechecked = verified.json()["model_presets"][0]
    assert rechecked["capabilities"]["vision"] is True
    assert not any("Vision" in message for message in rechecked["diagnostics"])
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_revision_tools_tell_the_model_the_summary_length_limit(tmp_path: Path) -> None:
    """The model learned the 240-character limit only by failing twice."""
    schemas: dict[str, dict[str, Any]] = {}

    async def stream(
        _messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        schemas.update({tool.name: tool.parameters_json_schema for tool in info.function_tools})
        yield "Done."

    agent = _build_course_agent(requires_approval=False)
    async with agent.run_stream(
        "Hello",
        deps=CourseAgentDeps(
            course_state=CourseAgentState(),
            workspace=tmp_path,
            data_dir=tmp_path / "data",
            cache_dir=tmp_path / "cache",
        ),
        model=FunctionModel(stream_function=stream),
    ) as streamed:
        await streamed.get_output()

    summary = schemas["create_course_revision"]["properties"]["summary"]
    assert summary["maxLength"] == history.MAX_REVISION_SUMMARY_CHARACTERS
    assert "at most 240 characters" in summary["description"]


def _sent_history(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    """What a provider receives for earlier messages; only the latest instructions are sent."""
    dumped = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    for message in dumped:
        message.pop("instructions", None)
    return dumped


@pytest.mark.anyio
async def test_course_state_changes_keep_earlier_requests_reusable_from_the_prompt_cache(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "cached-course"
    workspace.mkdir()
    chat_store = tmp_path / "user-data" / "chat"
    requests: list[tuple[str | None, list[dict[str, Any]]]] = []

    async def planning_model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        requests.append((info.instructions, _sent_history(messages)))
        latest = messages[-1]
        if len(requests) == 1:
            command = {
                "title": "Cache-friendly Course",
                "audience": "Local model users",
                "lectures": [{"title": "Prefixes"}, {"title": "Reuse"}],
            }
            yield {
                0: DeltaToolCall(
                    name="replace_course_plan",
                    json_args=json.dumps({"command": command}),
                    tool_call_id="cached-plan-1",
                )
            }
        elif isinstance(latest, ModelRequest) and any(
            isinstance(part, ToolReturnPart) for part in latest.parts
        ):
            yield "I created the Course Plan."
        else:
            yield "It has two Lectures."

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "user-data" / "provider",
        chat_store_path=chat_store,
        agent_model=FunctionModel(stream_function=planning_model),
        provider_validator=_verified_capabilities,
    )

    def turn(run_id: str, text: str) -> dict[str, object]:
        return {
            "threadId": "course-agent",
            "runId": run_id,
            "state": {},
            "messages": [{"id": f"user-{run_id}", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        first_status, _ = await _post_stream(app, "/api/agent", turn("run-1", "Plan a Course."))
        second_status, _ = await _post_stream(app, "/api/agent", turn("run-2", "How long?"))
        chat = (await client.get("/api/chat")).json()
        await client.post("/api/workspace/close")

    assert (first_status, second_status) == (200, 200)
    assert len(requests) == 3
    instructions = {sent_instructions for sent_instructions, _ in requests}
    assert len(instructions) == 1
    assert "Cache-friendly Course" not in (instructions.pop() or "")
    for (_, earlier), (_, later) in itertools.pairwise(requests):
        assert later[: len(earlier)] == earlier

    def snapshots(history: list[dict[str, Any]]) -> list[str]:
        return [
            part["content"]
            for message in history
            if message["kind"] == "request"
            for part in message["parts"]
            if part["part_kind"] == "user-prompt"
            and part["content"].startswith(COURSE_STATE_HEADING)
        ]

    first_run_snapshots = snapshots(requests[1][1])
    assert len(first_run_snapshots) == 1
    assert "does not have a Course Plan yet" in first_run_snapshots[0]
    second_run_snapshots = snapshots(requests[2][1])
    assert len(second_run_snapshots) == 2
    assert "Cache-friendly Course" in second_run_snapshots[1]
    assert [message["content"] for message in chat["messages"]] == [
        "Plan a Course.",
        "I created the Course Plan.",
        "How long?",
        "It has two Lectures.",
    ]

    trace_files = list(chat_store.glob("*/traces/*.jsonl"))
    assert len(trace_files) == 1
    records = [json.loads(line) for line in trace_files[0].read_text().splitlines()]
    assert [record["event"] for record in records] == [
        "run_start",
        "model_request",
        "model_response",
        "tool_call",
        "model_request",
        "model_response",
        "run_end",
        "run_start",
        "model_request",
        "model_response",
        "run_end",
    ]
    model_requests = [record for record in records if record["event"] == "model_request"]
    assert model_requests[0]["instructions"] == requests[0][0]
    for record in model_requests[1:]:
        assert record["reused_messages"] > 0
        assert record["history_rewritten"] is False
        assert record["instructions_changed"] is False
        assert record["tools_changed"] is False
        assert "instructions" not in record
    assert records[3]["tool"] == "replace_course_plan"
    responses = [record for record in records if record["event"] == "model_response"]
    assert all(record["usage"]["output_tokens"] > 0 for record in responses)
    assert {record["status"] for record in records if record["event"] == "run_end"} == {"completed"}


@pytest.mark.anyio
async def test_stopping_a_run_keeps_the_conversation_up_to_the_latest_model_request(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "stopped-course"
    workspace.mkdir()
    chat_store = tmp_path / "chat"
    waiting = asyncio.Event()
    seen: list[list[ModelMessage]] = []

    async def researching_model(
        messages: list[ModelMessage], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        seen.append(messages)
        if any(
            isinstance(message, ModelRequest)
            and any(isinstance(part, ToolReturnPart) for part in message.parts)
            for message in messages
        ):
            if len(seen) == 2:
                waiting.set()
                await asyncio.Event().wait()
            yield "Here is what I found before you stopped me."
            return
        yield {0: DeltaToolCall(name="list_sources", json_args="{}", tool_call_id="sources-1")}

    app = create_app(
        workspace,
        provider_store_path=tmp_path / "provider",
        chat_store_path=chat_store,
        agent_model=FunctionModel(stream_function=researching_model),
        provider_validator=_verified_capabilities,
    )

    def turn(run_id: str, text: str) -> dict[str, object]:
        return {
            "threadId": "course-agent",
            "runId": run_id,
            "state": {},
            "messages": [{"id": f"user-{run_id}", "role": "user", "content": text}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }

    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put("/api/provider", json=_provider_request())
        running = asyncio.create_task(_post_stream(app, "/api/agent", turn("run-1", "Research.")))
        await asyncio.wait_for(waiting.wait(), timeout=5)
        assert (await client.post("/api/agent/cancel")).status_code == 204
        await asyncio.wait_for(running, timeout=5)
        stopped_chat = (await client.get("/api/chat")).json()
        status, _ = await _post_stream(app, "/api/agent", turn("run-2", "Go on."))

    assert stopped_chat == {
        "approval": None,
        "messages": [
            {"id": stopped_chat["messages"][0]["id"], "role": "user", "content": "Research."}
        ],
    }
    assert status == 200
    resumed = seen[-1]
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_call_id == "sources-1"
        for message in resumed
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def test_a_saved_unfinished_tool_call_is_closed_rather_than_awaiting_approval(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    conversation_id = active_conversation_id(tmp_path / "chat", workspace)
    history: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart(content="Admit it.")]),
        ModelResponse(
            parts=[ToolCallPart(tool_name="admit_source", args="{}", tool_call_id="admit-1")]
        ),
    ]

    save_chat_history(
        tmp_path / "chat", workspace, close_unfinished_tool_calls(history), conversation_id
    )

    saved = read_chat_history(tmp_path / "chat", workspace, conversation_id)
    assert read_chat_transcript(tmp_path / "chat", workspace).approval is None
    closing = saved[-1]
    assert isinstance(closing, ModelRequest)
    assert [
        (part.tool_call_id, part.content)
        for part in closing.parts
        if isinstance(part, ToolReturnPart)
    ] == [("admit-1", UNFINISHED_TOOL_RESULT)]
    assert len(closing.parts) == 1
