"""Amazon Bedrock, OpenAI, Detected Credentials and prompt caching."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
from botocore.stub import Stubber
from pydantic import SecretStr, ValidationError

from course_harness import bedrock
from course_harness.app import create_app
from course_harness.bedrock import BedrockAccess, BedrockError
from course_harness.course_agent import build_provider_model
from course_harness.provider_detection import detect_credentials
from course_harness.providers import (
    ContextWindowUnknownError,
    PromptCachingUnknownError,
    ProviderAccountRequest,
    ProviderCapabilities,
    ProviderConfigurationRequest,
    ProviderValidationError,
    validate_provider_capabilities,
)


def _capabilities(**changes: object) -> dict[str, object]:
    return {
        "tool_calling": True,
        "structured_output": True,
        "streaming": True,
        "context_window": 200_000,
        "vision": True,
        **changes,
    }


def _bedrock_configuration(model: str, **capabilities: object) -> dict[str, object]:
    return {
        "kind": "bedrock",
        "model": model,
        "base_url": bedrock.bedrock_endpoint("eu-central-1"),
        "credential_source": "stored-key",
        "region": "eu-central-1",
        "capabilities": _capabilities(**capabilities),
    }


def test_provider_access_rejects_mismatched_credentials() -> None:
    with pytest.raises(ValidationError, match="requires an AWS region"):
        ProviderAccountRequest(name="Bedrock", kind="bedrock", api_key=SecretStr("key"))
    with pytest.raises(ValidationError, match="omit api_key"):
        ProviderAccountRequest(
            name="Bedrock",
            kind="bedrock",
            region="us-east-1",
            credential_source="aws-profile",
            aws_profile="work",
            api_key=SecretStr("key"),
        )
    with pytest.raises(ValidationError, match="Only Amazon Bedrock"):
        ProviderAccountRequest(name="OpenAI", kind="openai", credential_source="aws-environment")
    with pytest.raises(ValidationError, match="fixed API endpoint"):
        ProviderAccountRequest(
            name="OpenAI",
            kind="openai",
            api_key=SecretStr("key"),
            base_url="https://api.openai.com/v1",
        )

    account = ProviderAccountRequest(
        name="Work AWS",
        kind="bedrock",
        region="eu-central-1",
        credential_source="aws-profile",
        aws_profile="work",
    )
    assert account.resolved_base_url() == "https://bedrock-runtime.eu-central-1.amazonaws.com"
    assert ProviderAccountRequest(
        name="OpenAI", kind="openai", api_key=SecretStr("key")
    ).resolved_base_url() == ("https://api.openai.com/v1")


def test_foundation_model_ids_and_known_context_windows() -> None:
    assert (
        bedrock.foundation_model_id("us.anthropic.claude-sonnet-4-5-20250929-v1:0")
        == "anthropic.claude-sonnet-4-5-20250929-v1:0"
    )
    assert bedrock.foundation_model_id("amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"
    assert bedrock.foundation_model_id("arn:aws:bedrock:us-east-1:1:x/y") is None
    assert bedrock.known_context_window("global.anthropic.claude-haiku-4-5-v1:0") == 200_000
    assert bedrock.known_context_window("mistral.mistral-large-2407-v1:0") is None


def test_bedrock_clients_pin_their_signature_and_ignore_endpoint_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "ambient-bearer-token")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://attacker.example")
    environ = {
        "AWS_ACCESS_KEY_ID": "AKIDSESSION",
        "AWS_SECRET_ACCESS_KEY": "session-secret",
        "AWS_SESSION_TOKEN": "session-token",
    }
    access = BedrockAccess(region="us-west-2", credential_source="aws-environment", environ=environ)

    client = access.client("bedrock-runtime")

    assert client.meta.config.signature_version == "v4"
    assert client.meta.endpoint_url == "https://bedrock-runtime.us-west-2.amazonaws.com"
    credentials = access.session().get_credentials()
    assert credentials is not None
    assert credentials.access_key == "AKIDSESSION"
    assert credentials.token == "session-token"

    keyed = BedrockAccess(region="us-west-2", credential_source="stored-key", api_key="stored")
    assert keyed.client("bedrock-runtime").meta.config.signature_version == "bearer"


def test_missing_environment_credentials_explain_how_to_recover() -> None:
    access = BedrockAccess(region="us-east-1", credential_source="aws-environment", environ={})

    with pytest.raises(BedrockError, match="environment Course Harness was started from"):
        access.session()


def _stubbed(stubbers: dict[str, Stubber]) -> Any:
    def client(_access: BedrockAccess, service: str, **_kwargs: object) -> Any:
        return stubbers[service].client

    return client


def test_bedrock_tool_probe_reads_a_converse_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    access = BedrockAccess(region="us-east-1", credential_source="stored-key", api_key="k")
    runtime = Stubber(BedrockAccess.client(access, "bedrock-runtime"))
    runtime.add_response(
        "converse",
        {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {"toolUse": {"toolUseId": "1", "name": "add_numbers", "input": {}}}
                    ],
                }
            },
            "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "metrics": {"latencyMs": 1},
        },
    )
    runtime.add_client_error(
        "converse",
        service_error_code="ExpiredTokenException",
        service_message="The security token included in the request is expired",
    )
    monkeypatch.setattr(BedrockAccess, "client", _stubbed({"bedrock-runtime": runtime}))

    with runtime:
        assert bedrock.probe_tool_calling(access, "us.anthropic.claude-haiku-4-5-v1:0") is True
        with pytest.raises(BedrockError, match="expired"):
            bedrock.probe_tool_calling(access, "us.anthropic.claude-haiku-4-5-v1:0")


def test_bedrock_model_list_prefers_inference_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    access = BedrockAccess(region="us-east-1", credential_source="stored-key", api_key="k")
    control = Stubber(BedrockAccess.client(access, "bedrock"))
    model_arn = "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-5-v1:0"
    control.add_response(
        "list_foundation_models",
        {
            "modelSummaries": [
                {
                    "modelArn": model_arn,
                    "modelId": "anthropic.claude-sonnet-4-5-v1:0",
                    "inputModalities": ["TEXT", "IMAGE"],
                    "inferenceTypesSupported": ["INFERENCE_PROFILE"],
                    "modelLifecycle": {"status": "ACTIVE"},
                },
                {
                    "modelArn": model_arn.replace("anthropic.claude-sonnet-4-5", "amazon.nova"),
                    "modelId": "amazon.nova-micro-v1:0",
                    "inputModalities": ["TEXT"],
                    "inferenceTypesSupported": ["ON_DEMAND"],
                    "modelLifecycle": {"status": "ACTIVE"},
                },
            ]
        },
        {"byOutputModality": "TEXT"},
    )
    control.add_response(
        "list_inference_profiles",
        {
            "inferenceProfileSummaries": [
                {
                    "inferenceProfileName": "US Claude Sonnet 4.5",
                    "inferenceProfileArn": "arn:aws:bedrock:us-east-1:1:inference-profile/x",
                    "inferenceProfileId": "us.anthropic.claude-sonnet-4-5-v1:0",
                    "models": [{"modelArn": model_arn}],
                    "status": "ACTIVE",
                    "type": "SYSTEM_DEFINED",
                }
            ]
        },
        {"typeEquals": "SYSTEM_DEFINED"},
    )
    monkeypatch.setattr(BedrockAccess, "client", _stubbed({"bedrock": control}))

    with control:
        models = bedrock.list_models(access, limit=10)

    assert [(model.id, model.vision) for model in models] == [
        ("us.anthropic.claude-sonnet-4-5-v1:0", True),
        ("amazon.nova-micro-v1:0", False),
    ]


def _bedrock_request(model: str, **changes: object) -> ProviderConfigurationRequest:
    return ProviderConfigurationRequest.model_validate(
        {
            "kind": "bedrock",
            "model": model,
            "region": "us-east-1",
            "credential_source": "aws-profile",
            "aws_profile": "work",
            **changes,
        }
    )


@pytest.mark.anyio
async def test_bedrock_capabilities_ask_only_what_the_model_id_cannot_tell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[str] = []

    def probe(_access: BedrockAccess, model: str) -> bool:
        probed.append(model)
        return True

    monkeypatch.setattr(bedrock, "probe_tool_calling", probe)
    monkeypatch.setattr(
        bedrock,
        "describe_model",
        lambda _access, model: bedrock.BedrockModelInfo(id=model, vision=True, streaming=True),
    )
    claude = await validate_provider_capabilities(
        _bedrock_request("us.anthropic.claude-haiku-4-5-20251001-v1:0")
    )
    assert claude.prompt_caching is True
    assert claude.context_window == 200_000
    assert claude.vision is True

    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc"
    with pytest.raises(ContextWindowUnknownError):
        await validate_provider_capabilities(_bedrock_request(arn))
    with pytest.raises(PromptCachingUnknownError):
        await validate_provider_capabilities(_bedrock_request(arn, context_window=200_000))
    confirmed = await validate_provider_capabilities(
        _bedrock_request(arn, context_window=200_000, prompt_caching=True)
    )
    assert confirmed.prompt_caching is True
    assert probed == ["us.anthropic.claude-haiku-4-5-20251001-v1:0", arn]


@pytest.mark.anyio
async def test_openai_needs_no_base_url_and_knows_its_model_families() -> None:
    requested: list[str] = []

    def openai(request: httpx2.Request) -> httpx2.Response:
        requested.append(f"{request.url.host}{request.url.path}")
        assert request.headers["authorization"] == "Bearer openai-secret"
        return httpx2.Response(200, json={"id": request.url.path.rsplit("/", 1)[-1]})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(openai)) as client:
        known = await validate_provider_capabilities(
            ProviderConfigurationRequest(
                kind="openai", model="gpt-4.1-mini", api_key=SecretStr("openai-secret")
            ),
            http_client=client,
        )
        with pytest.raises(ContextWindowUnknownError):
            await validate_provider_capabilities(
                ProviderConfigurationRequest(
                    kind="openai", model="future-model", api_key=SecretStr("openai-secret")
                ),
                http_client=client,
            )

    assert requested == [
        "api.openai.com/v1/models/gpt-4.1-mini",
        "api.openai.com/v1/models/future-model",
    ]
    assert known.context_window == 1_047_576
    assert known.tool_calling is True
    assert known.vision is True


def test_models_request_prompt_caching_where_it_is_not_automatic() -> None:
    claude = build_provider_model(
        _bedrock_configuration("us.anthropic.claude-haiku-4-5-v1:0", prompt_caching=True), "key"
    )
    assert claude.settings == {
        "bedrock_cache_instructions": True,
        "bedrock_cache_tool_definitions": True,
        "bedrock_cache_messages": True,
    }

    arn = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/abc"
    confirmed = build_provider_model(_bedrock_configuration(arn, prompt_caching=True), "key")
    assert confirmed.profile.get("bedrock_supports_prompt_caching") is True
    declined = build_provider_model(_bedrock_configuration(arn, prompt_caching=False), "key")
    assert declined.settings is None

    anthropic = build_provider_model(
        {
            "kind": "anthropic",
            "model": "claude-sonnet-4-5",
            "base_url": "https://api.anthropic.com",
            "capabilities": _capabilities(),
        },
        "key",
    )
    assert anthropic.settings is not None
    assert anthropic.settings.get("anthropic_cache") is True


def _write_aws_config(home: Path) -> None:
    (home / ".aws").mkdir(parents=True)
    (home / ".aws" / "config").write_text(
        "[default]\nregion = eu-west-1\n\n[profile work]\nregion = eu-central-1\n"
        "sso_session = corp\n\n[sso-session corp]\nsso_region = eu-west-1\n",
        encoding="utf-8",
    )
    (home / ".aws" / "credentials").write_text(
        "[legacy]\naws_access_key_id = AKIDFILE\naws_secret_access_key = file-secret\n",
        encoding="utf-8",
    )


def test_detected_credentials_name_their_origin_but_never_their_values(tmp_path: Path) -> None:
    _write_aws_config(tmp_path)
    environ = {
        "HOME": str(tmp_path),
        "OPENAI_API_KEY": "openai-secret",
        "ANTHROPIC_API_KEY": "anthropic-secret",
        # A key for another Anthropic-style endpoint is not an Anthropic account.
        "ANTHROPIC_BASE_URL": "https://gateway.example",
        "AWS_ACCESS_KEY_ID": "AKIDSESSION",
        "AWS_SECRET_ACCESS_KEY": "session-secret",
        "AWS_SESSION_TOKEN": "session-token",
        "AWSUME_PROFILE": "work-admin",
        "AWS_REGION": "eu-north-1",
    }

    detected = detect_credentials(environ, [])

    assert [item.id for item in detected] == [
        "openai-environment",
        "aws-environment",
        "aws-profile",
    ]
    session = detected[1]
    assert session.origin.endswith("(awsume: work-admin)")
    assert session.region == "eu-north-1"
    assert [(p.name, p.region) for p in detected[2].aws_profiles] == [
        ("default", "eu-west-1"),
        ("legacy", None),
        ("work", "eu-central-1"),
    ]
    serialized = json.dumps([item.model_dump() for item in detected])
    for secret in ("openai-secret", "session-secret", "session-token", "file-secret", "AKID"):
        assert secret not in serialized


@pytest.mark.anyio
async def test_a_detected_credential_is_added_with_one_confirmation(tmp_path: Path) -> None:
    workspace = tmp_path / "course"
    workspace.mkdir()
    home = tmp_path / "home"
    _write_aws_config(home)
    provider_store = tmp_path / "user-data" / "providers"
    environ = {"HOME": str(home), "OPENAI_API_KEY": "openai-secret"}
    validated: list[ProviderAccountRequest] = []

    async def validate_account(request: ProviderAccountRequest) -> None:
        validated.append(request)

    async def verified(_request: object) -> ProviderCapabilities:
        return ProviderCapabilities.model_validate(_capabilities())

    transport = httpx2.ASGITransport(
        app=create_app(
            workspace,
            provider_store_path=provider_store,
            provider_validator=verified,
            provider_account_validator=validate_account,
            credential_environ=environ,
        )
    )
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        before = await client.get("/api/provider-detections")
        openai = await client.post(
            "/api/provider-detections/openai-environment", json={"name": "OpenAI"}
        )
        unknown_profile = await client.post(
            "/api/provider-detections/aws-profile",
            json={"name": "Elsewhere", "aws_profile": "not-configured"},
        )
        work = await client.post(
            "/api/provider-detections/aws-profile",
            json={"name": "Work AWS", "aws_profile": "work"},
        )
        after = await client.get("/api/provider-detections")
        rotate = await client.patch(
            f"/api/provider-accounts/{work.json()['id']}/credential",
            json={"api_key": "anything"},
        )
        preset = await client.post(
            "/api/models",
            json={
                "name": "Claude on Bedrock",
                "provider_account_id": work.json()["id"],
                "model": "eu.anthropic.claude-sonnet-4-5-v1:0",
            },
        )
        deleted = await client.delete(
            f"/api/provider-accounts/{work.json()['id']}?delete_model_presets=true"
        )

    assert [item["id"] for item in before.json()] == ["openai-environment", "aws-profile"]
    assert openai.status_code == 201
    assert openai.json()["kind"] == "openai"
    assert openai.json()["detected_from"] == "openai-environment"
    assert unknown_profile.status_code == 404
    assert work.status_code == 201
    assert work.json()["credential_source"] == "aws-profile"
    assert work.json()["region"] == "eu-central-1"
    assert [item["id"] for item in after.json()] == ["aws-profile"]
    assert [p["name"] for p in after.json()[0]["aws_profiles"]] == ["default", "legacy"]
    assert rotate.status_code == 422
    assert preset.status_code == 201
    assert deleted.status_code == 200
    assert [request.secret() for request in validated] == ["openai-secret", None]
    credentials = (provider_store / "credentials.json").read_text(encoding="utf-8")
    assert "openai-secret" in credentials


@pytest.mark.anyio
async def test_expired_detected_environment_credentials_are_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(_access: BedrockAccess) -> None:
        raise BedrockError("The AWS session credentials have expired.")

    monkeypatch.setattr(bedrock, "verify_access", reject)
    from course_harness.providers import validate_provider_account

    with pytest.raises(ProviderValidationError, match="expired"):
        await validate_provider_account(
            ProviderAccountRequest(
                name="Session",
                kind="bedrock",
                region="us-east-1",
                credential_source="aws-environment",
            )
        )
