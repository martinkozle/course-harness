import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

ProviderKind = Literal["openrouter", "openai-compatible", "anthropic"]


class ProviderCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_calling: bool
    structured_output: bool
    streaming: bool
    context_window: int = Field(ge=1)
    vision: bool


class ProviderConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ProviderKind
    model: str = Field(min_length=1, max_length=300)
    base_url: str
    capabilities: ProviderCapabilities


class ProviderConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: ProviderKind
    model: str = Field(min_length=1, max_length=300)
    api_key: SecretStr = Field(min_length=1)
    base_url: str | None = None

    @model_validator(mode="after")
    def endpoint_matches_provider(self) -> ProviderConfigurationRequest:
        if self.kind == "openrouter" and self.base_url is not None:
            raise ValueError("OpenRouter uses its fixed API endpoint; omit base_url")
        if self.kind == "anthropic" and self.base_url is not None:
            raise ValueError("Anthropic uses its direct API endpoint; omit base_url")
        if self.kind == "openai-compatible" and self.base_url is None:
            raise ValueError("An OpenAI-compatible provider requires base_url")
        if self.base_url is not None:
            validate_provider_url(self.base_url)
        return self

    def configuration(self, capabilities: ProviderCapabilities) -> ProviderConfiguration:
        if self.kind == "openrouter":
            base_url = "https://openrouter.ai/api/v1"
        elif self.kind == "anthropic":
            base_url = "https://api.anthropic.com"
        else:
            assert self.base_url is not None
            base_url = self.base_url.rstrip("/")
        return ProviderConfiguration(
            kind=self.kind,
            model=self.model,
            base_url=base_url,
            capabilities=capabilities,
        )


class ProviderStatus(BaseModel):
    configured: bool
    kind: ProviderKind | None = None
    model: str | None = None
    base_url: str | None = None
    capabilities: ProviderCapabilities | None = None
    diagnostics: list[str] | None = None


class ProviderCapabilityError(ValueError):
    """The selected model cannot run the Course planning agent."""


class ProviderValidationError(ValueError):
    """The provider or model could not be verified."""


ProviderCapabilityValidator = Callable[
    [ProviderConfigurationRequest], Awaitable[ProviderCapabilities]
]


def validate_provider_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Provider base_url cannot contain credentials")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise ValueError("Provider base_url must use HTTPS, or HTTP on a loopback address")


def _capability_findings(capabilities: ProviderCapabilities) -> list[tuple[str, bool]]:
    return [
        (
            "Tool calling is required to apply validated Course Plan changes.",
            not capabilities.tool_calling,
        ),
        (
            "Structured output is required for typed Course Plan commands.",
            not capabilities.structured_output,
        ),
        (
            "Streaming is required to show Course Agent progress.",
            not capabilities.streaming,
        ),
        (
            "A context window of at least 16,384 tokens is required for Course planning.",
            capabilities.context_window < 16_384,
        ),
        (
            "Vision input is unavailable; image attachments cannot be used with this model.",
            False,
        ),
    ]


def provider_diagnostics(capabilities: ProviderCapabilities) -> list[str]:
    findings = [message for message, blocking in _capability_findings(capabilities) if blocking]
    if not capabilities.vision:
        findings.append(
            "Vision input is unavailable; image attachments cannot be used with this model."
        )
    return findings


def require_planning_capabilities(capabilities: ProviderCapabilities) -> list[str]:
    diagnostics = provider_diagnostics(capabilities)
    blocking = [message for message, failed in _capability_findings(capabilities) if failed]
    if blocking:
        raise ProviderCapabilityError(" ".join(blocking))
    return diagnostics


def default_provider_store_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "Course Harness" / "provider"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Course Harness" / "provider"
    root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "course-harness" / "provider"


def save_provider_configuration(
    store_path: Path,
    request: ProviderConfigurationRequest,
    capabilities: ProviderCapabilities,
) -> ProviderConfiguration:
    configuration = request.configuration(capabilities)
    store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store_path, 0o700)
    _write_private_json(
        store_path / "provider.json", configuration.model_dump(mode="json"), mode=0o600
    )
    _write_private_json(
        store_path / "credentials.json",
        {"api_key": request.api_key.get_secret_value()},
        mode=0o600,
    )
    return configuration


async def validate_provider_capabilities(
    request: ProviderConfigurationRequest,
) -> ProviderCapabilities:
    """Verify model metadata through the configured provider adapter."""
    api_key = request.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx2.AsyncClient(timeout=15) as client:
            if request.kind == "openrouter":
                response = await client.get(
                    f"https://openrouter.ai/api/v1/model/{request.model}", headers=headers
                )
                response.raise_for_status()
                metadata = response.json()["data"]
                return _capabilities_from_metadata(metadata)

            if request.kind == "anthropic":
                response = await client.get(
                    f"https://api.anthropic.com/v1/models/{request.model}",
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                )
                response.raise_for_status()
                from course_harness.course_agent import build_provider_model

                model = build_provider_model(
                    request.configuration(
                        ProviderCapabilities(
                            tool_calling=True,
                            structured_output=True,
                            streaming=True,
                            context_window=200_000,
                            vision=True,
                        )
                    ),
                    api_key,
                )
                profile = model.profile
                return ProviderCapabilities(
                    tool_calling=bool(profile.get("supports_tools")),
                    structured_output=bool(profile.get("supports_json_schema_output")),
                    streaming=True,
                    context_window=200_000,
                    vision=request.model.startswith("claude-"),
                )

            assert request.base_url is not None
            response = await client.get(f"{request.base_url.rstrip('/')}/models", headers=headers)
            response.raise_for_status()
            models = response.json().get("data", [])
            metadata = next(
                (
                    item
                    for item in models
                    if isinstance(item, dict) and item.get("id") == request.model
                ),
                None,
            )
            if metadata is None:
                raise ProviderValidationError(
                    "The OpenAI-compatible endpoint did not report the selected model."
                )
            return _capabilities_from_metadata(metadata)
    except ProviderValidationError:
        raise
    except (httpx2.HTTPError, KeyError, TypeError, ValueError) as error:
        raise ProviderValidationError(
            "Course Harness could not authenticate and verify that model's capabilities."
        ) from error


def _capabilities_from_metadata(metadata: object) -> ProviderCapabilities:
    if not isinstance(metadata, dict):
        raise ProviderValidationError("The provider returned invalid model metadata.")
    parameters = metadata.get("supported_parameters", [])
    architecture = metadata.get("architecture", {})
    raw_modalities = (
        architecture.get("input_modalities", []) if isinstance(architecture, dict) else []
    )
    modalities = raw_modalities if isinstance(raw_modalities, list) else []
    capabilities = metadata.get("capabilities", {})
    if not isinstance(parameters, list):
        parameters = []
    if not isinstance(capabilities, dict):
        capabilities = {}
    context_window = metadata.get("context_length") or metadata.get("context_window") or 0
    return ProviderCapabilities(
        tool_calling="tools" in parameters or capabilities.get("tools") is True,
        structured_output=(
            "structured_outputs" in parameters
            or "response_format" in parameters
            or capabilities.get("structured_output") is True
        ),
        streaming=capabilities.get("streaming", True) is True,
        context_window=context_window if isinstance(context_window, int) else 0,
        vision="image" in modalities or capabilities.get("vision") is True,
    )


def _write_private_json(path: Path, payload: object, *, mode: int) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def read_provider_configuration(store_path: Path) -> ProviderConfiguration | None:
    path = store_path / "provider.json"
    if not path.is_file():
        return None
    try:
        return ProviderConfiguration.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def read_provider_api_key(store_path: Path) -> str | None:
    path = store_path / "credentials.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        api_key = payload.get("api_key")
        return api_key if isinstance(api_key, str) and api_key else None
    except OSError, json.JSONDecodeError, AttributeError:
        return None


def provider_status(store_path: Path) -> ProviderStatus:
    configuration = read_provider_configuration(store_path)
    if configuration is None or read_provider_api_key(store_path) is None:
        return ProviderStatus(configured=False)
    return ProviderStatus(
        configured=True,
        kind=configuration.kind,
        model=configuration.model,
        base_url=configuration.base_url,
        capabilities=configuration.capabilities,
        diagnostics=provider_diagnostics(configuration.capabilities),
    )
