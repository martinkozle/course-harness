from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from course_harness.credential_store import CredentialStore, CredentialStoreError
from course_harness.product_connectors import product_connector_client
from course_harness.runtime_paths import RuntimePaths

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
    allow_insecure_http: bool = False

    @model_validator(mode="after")
    def endpoint_matches_provider(self) -> ProviderConfigurationRequest:
        if self.kind == "openrouter" and self.base_url is not None:
            raise ValueError("OpenRouter uses its fixed API endpoint; omit base_url")
        if self.kind == "anthropic" and self.base_url is not None:
            raise ValueError("Anthropic uses its direct API endpoint; omit base_url")
        if self.kind == "openai-compatible" and self.base_url is None:
            raise ValueError("An OpenAI-compatible provider requires base_url")
        if self.base_url is not None:
            validate_provider_url(self.base_url, allow_insecure_http=self.allow_insecure_http)
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


class ProviderAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    api_key: SecretStr = Field(min_length=1)
    base_url: str | None = None
    allow_insecure_http: bool = False

    @model_validator(mode="after")
    def endpoint_matches_provider(self) -> ProviderAccountRequest:
        if self.kind in {"openrouter", "anthropic"} and self.base_url is not None:
            raise ValueError(f"{self.kind} uses its fixed API endpoint; omit base_url")
        if self.kind == "openai-compatible" and self.base_url is None:
            raise ValueError("An OpenAI-compatible provider requires base_url")
        if self.base_url is not None:
            validate_provider_url(self.base_url, allow_insecure_http=self.allow_insecure_http)
        return self

    def resolved_base_url(self) -> str:
        if self.kind == "openrouter":
            return "https://openrouter.ai/api/v1"
        if self.kind == "anthropic":
            return "https://api.anthropic.com"
        assert self.base_url is not None
        return self.base_url.rstrip("/")


class ProviderAccountCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)


class ProviderAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: ProviderKind
    base_url: str


class ModelPresetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    provider_account_id: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=300)


class ModelPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    provider_account_id: str
    model: str
    capabilities: ProviderCapabilities
    diagnostics: list[str]


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_accounts: list[ProviderAccount] = Field(default_factory=list)
    model_presets: list[ModelPreset] = Field(default_factory=list)
    selected_model_id: str | None = None


class ProviderStatus(BaseModel):
    configured: bool
    kind: ProviderKind | None = None
    model: str | None = None
    base_url: str | None = None
    capabilities: ProviderCapabilities | None = None
    diagnostics: list[str] | None = None


class CredentialStorageStatus(BaseModel):
    """Non-secret credential storage information for runtime diagnostics."""

    mode: Literal["os-keyring", "private-json-file", "unavailable", "unconfigured"]
    location: str


class RuntimeProviderStatus(BaseModel):
    """Provider state suitable for a read-only local diagnostics surface."""

    configured: bool
    selected_model_id: str | None = None
    provider: ProviderStatus
    credential_storage: CredentialStorageStatus
    remediation: str | None = None


class ProviderCapabilityError(ValueError):
    """The selected model cannot run the Course planning agent."""


class ProviderValidationError(ValueError):
    """The provider or model could not be verified."""


class ProviderAccountInUseError(ValueError):
    """A Provider Account cannot be deleted without handling its Model Presets."""


ProviderCapabilityValidator = Callable[
    [ProviderConfigurationRequest], Awaitable[ProviderCapabilities]
]
ProviderAccountValidator = Callable[[ProviderAccountRequest], Awaitable[None]]


def validate_provider_url(value: str, *, allow_insecure_http: bool = False) -> None:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Provider base_url cannot contain credentials")
    if parsed.scheme == "https" and parsed.hostname:
        return
    if (
        parsed.scheme == "http"
        and parsed.hostname
        and (parsed.hostname in {"127.0.0.1", "localhost", "::1"} or allow_insecure_http)
    ):
        return
    raise ValueError(
        "Provider base_url must use HTTPS, or HTTP on a loopback address; "
        "set allow_insecure_http to use HTTP on another host"
    )


def _capability_findings(capabilities: ProviderCapabilities) -> list[tuple[str, bool]]:
    return [
        (
            "Tool calling is required to apply validated Course Plan changes.",
            not capabilities.tool_calling,
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
    if not capabilities.structured_output:
        findings.append(
            "Native structured output is unavailable; Course Plan commands remain validated "
            "through typed tool calls."
        )
    return findings


def require_planning_capabilities(capabilities: ProviderCapabilities) -> list[str]:
    diagnostics = provider_diagnostics(capabilities)
    blocking = [message for message, failed in _capability_findings(capabilities) if failed]
    if blocking:
        raise ProviderCapabilityError(" ".join(blocking))
    return diagnostics


def default_provider_store_path() -> Path:
    return RuntimePaths.platform().provider_store_path


def _credential_store(
    store_path: Path, credential_store: CredentialStore | None = None
) -> CredentialStore:
    if credential_store is not None:
        return credential_store
    # An explicitly injected path is used by tests and host integrations.  Do
    # not let it reach into a user's keyring; the platform default is the only
    # production path that opportunistically opts in.
    return CredentialStore.for_provider_store(
        store_path,
        use_os_keyring=store_path == default_provider_store_path(),
    )


def save_provider_configuration(
    store_path: Path,
    request: ProviderConfigurationRequest,
    capabilities: ProviderCapabilities,
    *,
    credential_store: CredentialStore | None = None,
) -> ProviderConfiguration:
    configuration = request.configuration(capabilities)
    configuration_path = store_path / "provider.json"
    previous_configuration: bytes | None = None
    try:
        store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(store_path, 0o700)
        previous_configuration = _snapshot_private_file(configuration_path)
        _write_private_json(
            configuration_path,
            configuration.model_dump(mode="json"),
            mode=0o600,
        )
        _credential_store(store_path, credential_store).write(
            "provider-legacy", request.api_key.get_secret_value()
        )
    except (CredentialStoreError, OSError) as error:
        try:
            _restore_private_file(configuration_path, previous_configuration)
        except OSError as rollback_error:
            raise CredentialStoreError(
                "Provider configuration could not be restored safely."
            ) from rollback_error
        if isinstance(error, CredentialStoreError):
            raise
        raise CredentialStoreError("Provider configuration could not be saved safely.") from error
    return configuration


async def validate_provider_account(
    request: ProviderAccountRequest,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> None:
    if http_client is not None:
        await _validate_provider_account(request, http_client)
        return
    async with product_connector_client(timeout=15) as client:
        await _validate_provider_account(request, client)


async def _validate_provider_account(
    request: ProviderAccountRequest, client: httpx2.AsyncClient
) -> None:
    api_key = request.api_key.get_secret_value()
    try:
        if request.kind == "openrouter":
            response = await client.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        elif request.kind == "anthropic":
            response = await client.get(
                "https://api.anthropic.com/v1/models?limit=1",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            )
        else:
            response = await client.get(
                f"{request.resolved_base_url()}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if response.status_code == 401:
            raise ProviderValidationError(
                f"The {request.name} credential was rejected. Check the API key and try again."
            )
        response.raise_for_status()
    except ProviderValidationError:
        raise
    except httpx2.HTTPError as error:
        raise ProviderValidationError(
            f"Course Harness could not authenticate with {request.name}."
        ) from error


async def validate_provider_capabilities(
    request: ProviderConfigurationRequest,
    *,
    http_client: httpx2.AsyncClient | None = None,
) -> ProviderCapabilities:
    """Verify model metadata through the configured provider adapter."""
    if http_client is not None:
        return await _validate_provider_capabilities(request, http_client)
    async with product_connector_client(timeout=15) as client:
        return await _validate_provider_capabilities(request, client)


async def _validate_provider_capabilities(
    request: ProviderConfigurationRequest,
    client: httpx2.AsyncClient,
) -> ProviderCapabilities:
    api_key = request.api_key.get_secret_value()
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if request.kind == "openrouter":
            authentication = await client.get("https://openrouter.ai/api/v1/key", headers=headers)
            if authentication.status_code == 401:
                raise ProviderValidationError(
                    "The OpenRouter API key was rejected. Create or copy an OpenRouter key and "
                    "try again."
                )
            authentication.raise_for_status()
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
            (item for item in models if isinstance(item, dict) and item.get("id") == request.model),
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


def read_model_catalog(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> ModelCatalog:
    path = store_path / "catalog.json"
    if path.is_file():
        try:
            return ModelCatalog.model_validate_json(path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return ModelCatalog()
    return _migrate_legacy_catalog(store_path, credential_store=credential_store)


def save_provider_account(
    store_path: Path,
    request: ProviderAccountRequest,
    *,
    credential_store: CredentialStore | None = None,
) -> ProviderAccount:
    store = _credential_store(store_path, credential_store)
    catalog = read_model_catalog(store_path, credential_store=store)
    account = ProviderAccount(
        id=f"provider-{uuid4().hex[:12]}",
        name=request.name,
        kind=request.kind,
        base_url=request.resolved_base_url(),
    )
    catalog.provider_accounts.append(account)
    store.write(account.id, request.api_key.get_secret_value())
    try:
        _write_catalog(store_path, catalog)
    except OSError as error:
        try:
            store.delete(account.id)
        except CredentialStoreError as rollback_error:
            raise CredentialStoreError(
                "The new Provider Account could not be saved and its credential could not be "
                "removed safely."
            ) from rollback_error
        raise CredentialStoreError("The new Provider Account could not be saved safely.") from error
    return account


def provider_request_for_credential_rotation(
    store_path: Path,
    account_id: str,
    request: ProviderAccountCredentialRequest,
    *,
    credential_store: CredentialStore | None = None,
) -> ProviderAccountRequest:
    account = next(
        (
            candidate
            for candidate in read_model_catalog(
                store_path, credential_store=credential_store
            ).provider_accounts
            if candidate.id == account_id
        ),
        None,
    )
    if account is None:
        raise KeyError(account_id)
    return ProviderAccountRequest(
        name=account.name,
        kind=account.kind,
        api_key=request.api_key,
        base_url=account.base_url if account.kind == "openai-compatible" else None,
        allow_insecure_http=account.base_url.startswith("http://"),
    )


def replace_provider_account_credential(
    store_path: Path,
    account_id: str,
    request: ProviderAccountCredentialRequest,
    *,
    credential_store: CredentialStore | None = None,
) -> ProviderAccount:
    store = _credential_store(store_path, credential_store)
    catalog = read_model_catalog(store_path, credential_store=store)
    account = next(
        (candidate for candidate in catalog.provider_accounts if candidate.id == account_id), None
    )
    if account is None:
        raise KeyError(account_id)
    store.write(account_id, request.api_key.get_secret_value())
    return account


def delete_provider_account(
    store_path: Path,
    account_id: str,
    *,
    delete_model_presets: bool = False,
    credential_store: CredentialStore | None = None,
) -> ModelCatalog:
    store = _credential_store(store_path, credential_store)
    catalog = read_model_catalog(store_path, credential_store=store)
    if all(account.id != account_id for account in catalog.provider_accounts):
        raise KeyError(account_id)
    attached_presets = [
        preset for preset in catalog.model_presets if preset.provider_account_id == account_id
    ]
    if attached_presets and not delete_model_presets:
        count = len(attached_presets)
        label = "Model Preset" if count == 1 else "Model Presets"
        raise ProviderAccountInUseError(
            f"This Provider Account is used by {count} {label}. "
            "Confirm that those presets should also be deleted."
        )

    # Delete the secret before mutating account metadata.  A backend failure is
    # surfaced and leaves the catalog and its storage-mode metadata intact.
    recovery = store.delete(account_id)
    post_delete_metadata, post_delete_credentials = store.private_state()
    removed_preset_ids = {preset.id for preset in attached_presets}
    catalog.provider_accounts = [
        account for account in catalog.provider_accounts if account.id != account_id
    ]
    catalog.model_presets = [
        preset for preset in catalog.model_presets if preset.id not in removed_preset_ids
    ]
    if catalog.selected_model_id in removed_preset_ids:
        catalog.selected_model_id = catalog.model_presets[0].id if catalog.model_presets else None
    try:
        _write_catalog(store_path, catalog)
    except OSError as error:
        try:
            store.restore_recovery(
                recovery,
                post_delete_metadata=post_delete_metadata,
                post_delete_credentials=post_delete_credentials,
            )
        except CredentialStoreError as rollback_error:
            raise CredentialStoreError(
                "The Provider Account could not be deleted and its credential could not be "
                "restored safely."
            ) from rollback_error
        raise CredentialStoreError("The Provider Account could not be deleted safely.") from error
    return catalog


def save_model_preset(
    store_path: Path,
    request: ModelPresetRequest,
    capabilities: ProviderCapabilities,
    *,
    credential_store: CredentialStore | None = None,
) -> ModelPreset:
    catalog = read_model_catalog(store_path, credential_store=credential_store)
    if all(account.id != request.provider_account_id for account in catalog.provider_accounts):
        raise KeyError(request.provider_account_id)
    preset = ModelPreset(
        id=f"model-{uuid4().hex[:12]}",
        name=request.name,
        provider_account_id=request.provider_account_id,
        model=request.model,
        capabilities=capabilities,
        diagnostics=provider_diagnostics(capabilities),
    )
    catalog.model_presets.append(preset)
    if catalog.selected_model_id is None:
        catalog.selected_model_id = preset.id
    _write_catalog(store_path, catalog)
    return preset


def select_model_preset(
    store_path: Path, model_id: str, *, credential_store: CredentialStore | None = None
) -> ModelCatalog:
    catalog = read_model_catalog(store_path, credential_store=credential_store)
    if all(preset.id != model_id for preset in catalog.model_presets):
        raise KeyError(model_id)
    catalog.selected_model_id = model_id
    _write_catalog(store_path, catalog)
    return catalog


def provider_request_for_model(
    store_path: Path,
    request: ModelPresetRequest,
    *,
    credential_store: CredentialStore | None = None,
) -> ProviderConfigurationRequest:
    store = _credential_store(store_path, credential_store)
    catalog = read_model_catalog(store_path, credential_store=store)
    account = next(
        (
            candidate
            for candidate in catalog.provider_accounts
            if candidate.id == request.provider_account_id
        ),
        None,
    )
    api_key = store.read(request.provider_account_id)
    if account is None or api_key is None:
        raise KeyError(request.provider_account_id)
    return ProviderConfigurationRequest(
        kind=account.kind,
        model=request.model,
        api_key=SecretStr(api_key),
        base_url=account.base_url if account.kind == "openai-compatible" else None,
        allow_insecure_http=account.base_url.startswith("http://"),
    )


def resolve_selected_model(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> tuple[ProviderConfiguration, str] | None:
    store = _credential_store(store_path, credential_store)
    catalog = read_model_catalog(store_path, credential_store=store)
    preset = next(
        (
            candidate
            for candidate in catalog.model_presets
            if candidate.id == catalog.selected_model_id
        ),
        None,
    )
    if preset is None:
        return None
    account = next(
        (
            candidate
            for candidate in catalog.provider_accounts
            if candidate.id == preset.provider_account_id
        ),
        None,
    )
    api_key = store.read(preset.provider_account_id)
    if account is None or api_key is None:
        return None
    return (
        ProviderConfiguration(
            kind=account.kind,
            model=preset.model,
            base_url=account.base_url,
            capabilities=preset.capabilities,
        ),
        api_key,
    )


def resolve_active_model(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> tuple[ProviderConfiguration, str] | None:
    """Resolve the selected model, using legacy state only before a catalog exists."""
    store = _credential_store(store_path, credential_store)
    selected = resolve_selected_model(store_path, credential_store=store)
    if selected is not None or (store_path / "catalog.json").is_file():
        return selected
    configuration = read_provider_configuration(store_path)
    api_key = store.read("provider-legacy")
    if configuration is None or api_key is None:
        return None
    return configuration, api_key


def _migrate_legacy_catalog(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> ModelCatalog:
    configuration = read_provider_configuration(store_path)
    api_key = _credential_store(store_path, credential_store).read("provider-legacy")
    if configuration is None or api_key is None:
        return ModelCatalog()
    account = ProviderAccount(
        id="provider-legacy",
        name={
            "openrouter": "OpenRouter",
            "anthropic": "Anthropic",
            "openai-compatible": "OpenAI-compatible",
        }[configuration.kind],
        kind=configuration.kind,
        base_url=configuration.base_url,
    )
    preset = ModelPreset(
        id="model-legacy",
        name=configuration.model,
        provider_account_id=account.id,
        model=configuration.model,
        capabilities=configuration.capabilities,
        diagnostics=provider_diagnostics(configuration.capabilities),
    )
    catalog = ModelCatalog(
        provider_accounts=[account],
        model_presets=[preset],
        selected_model_id=preset.id,
    )
    # Keep the original credential representation.  Discovering a keyring must
    # never silently migrate an existing private JSON credential.
    try:
        _write_catalog(store_path, catalog)
    except OSError as error:
        raise CredentialStoreError(
            "Legacy Provider Account metadata could not be migrated safely."
        ) from error
    return catalog


def _write_catalog(store_path: Path, catalog: ModelCatalog) -> None:
    store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(store_path, 0o700)
    _write_private_json(store_path / "catalog.json", catalog.model_dump(mode="json"), mode=0o600)


def _write_private_json(path: Path, payload: object, *, mode: int) -> None:
    _write_private_bytes(path, json.dumps(payload, indent=2).encode("utf-8") + b"\n", mode=mode)


def _write_private_bytes(path: Path, payload: bytes, *, mode: int) -> None:
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _snapshot_private_file(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _restore_private_file(path: Path, snapshot: bytes | None) -> None:
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    _write_private_bytes(path, snapshot, mode=0o600)


def read_provider_configuration(store_path: Path) -> ProviderConfiguration | None:
    path = store_path / "provider.json"
    if not path.is_file():
        return None
    try:
        return ProviderConfiguration.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None


def read_provider_api_key(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> str | None:
    active = resolve_active_model(store_path, credential_store=credential_store)
    return active[1] if active is not None else None


def provider_status(
    store_path: Path, *, credential_store: CredentialStore | None = None
) -> ProviderStatus:
    store = _credential_store(store_path, credential_store)
    active = resolve_active_model(store_path, credential_store=store)
    if active is None:
        return ProviderStatus(configured=False)
    configuration, _ = active
    return _configured_provider_status(
        kind=configuration.kind,
        model=configuration.model,
        base_url=configuration.base_url,
        capabilities=configuration.capabilities,
    )


def _configured_provider_status(
    *,
    kind: ProviderKind,
    model: str,
    base_url: str,
    capabilities: ProviderCapabilities,
) -> ProviderStatus:
    return ProviderStatus(
        configured=True,
        kind=kind,
        model=model,
        base_url=base_url,
        capabilities=capabilities,
        diagnostics=provider_diagnostics(capabilities),
    )


def runtime_provider_status(store_path: Path) -> RuntimeProviderStatus:
    """Report provider metadata after a non-secret, non-prompting keyring availability check."""
    store = CredentialStore.for_provider_store(store_path, use_os_keyring=True)
    catalog = ModelCatalog()
    catalog_path = store_path / "catalog.json"
    if catalog_path.is_file():
        with suppress(OSError, ValueError):
            catalog = ModelCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))

    selected = next(
        (preset for preset in catalog.model_presets if preset.id == catalog.selected_model_id),
        None,
    )
    account = (
        next(
            (item for item in catalog.provider_accounts if item.id == selected.provider_account_id),
            None,
        )
        if selected is not None
        else None
    )
    if selected is not None and account is not None:
        storage_mode, storage_location = store.diagnostics(account.id)
        if storage_mode == "unavailable":
            return _unavailable_runtime_provider_status(
                catalog.selected_model_id,
                storage_mode,
                storage_location,
            )
        provider = _configured_provider_status(
            kind=account.kind,
            model=selected.model,
            base_url=account.base_url,
            capabilities=selected.capabilities,
        )
        return RuntimeProviderStatus(
            configured=True,
            selected_model_id=catalog.selected_model_id,
            provider=provider,
            credential_storage=CredentialStorageStatus(
                mode=storage_mode,
                location=storage_location,
            ),
        )

    # Keep legacy installations observable, but never call the migration helper:
    # that helper writes catalog files and would violate diagnostics' read-only contract.
    legacy = read_provider_configuration(store_path)
    if not catalog_path.is_file() and legacy is not None:
        storage_mode, storage_location = store.diagnostics("provider-legacy")
        if storage_mode == "unavailable":
            return _unavailable_runtime_provider_status(
                None,
                storage_mode,
                storage_location,
            )
        provider = _configured_provider_status(
            kind=legacy.kind,
            model=legacy.model,
            base_url=legacy.base_url,
            capabilities=legacy.capabilities,
        )
        return RuntimeProviderStatus(
            configured=True,
            provider=provider,
            credential_storage=CredentialStorageStatus(
                mode=storage_mode,
                location=storage_location,
            ),
        )

    storage_mode, storage_location = store.diagnostics(None)
    remediation = (
        "Credential storage metadata is unreadable. Restore or remove the reported metadata "
        "file, then configure the Provider Account again."
        if storage_mode == "unavailable"
        else (
            "Create or select a Course Workspace, then open Models to add a Provider Account "
            "and a compatible Model Preset."
        )
    )
    return RuntimeProviderStatus(
        configured=False,
        selected_model_id=catalog.selected_model_id,
        provider=ProviderStatus(configured=False),
        credential_storage=CredentialStorageStatus(
            mode=storage_mode,
            location=storage_location,
        ),
        remediation=remediation,
    )


def _unavailable_runtime_provider_status(
    selected_model_id: str | None,
    storage_mode: Literal["unavailable"],
    storage_location: str,
) -> RuntimeProviderStatus:
    return RuntimeProviderStatus(
        configured=False,
        selected_model_id=selected_model_id,
        provider=ProviderStatus(configured=False),
        credential_storage=CredentialStorageStatus(
            mode=storage_mode,
            location=storage_location,
        ),
        remediation=(
            f"Provider credential storage is unavailable ({storage_location}). Unlock or configure "
            "the operating-system keyring, or restore the private credential file, then update the "
            "Provider Account."
        ),
    )
