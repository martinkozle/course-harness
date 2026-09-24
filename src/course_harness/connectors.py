"""Connectors: remote MCP servers whose tools the Course Author lets the Course Agent use.

Connectors are configured per installation, beside Provider Accounts. Secret header values
and secret URLs are Connector Credentials: they live in their own credential store and are
never returned over HTTP or written to a Course Workspace.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from course_harness.credential_store import (
    CredentialDiagnosticMode,
    CredentialStore,
    CredentialStoreError,
)

CONNECTOR_CREDENTIAL_SERVICE = "course-harness.connector-credentials"
EXA_PRESET = "exa"
EXA_URL = "https://mcp.exa.ai/mcp"

ConnectorPreset = Literal["exa"]


class ConnectorError(ValueError):
    """A Connector could not be read or changed as requested."""


class ConnectorHeaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
    # For a secret header, an empty value keeps the saved Connector Credential.
    value: str | None = Field(default=None, max_length=4000)
    secret: bool = False


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    # For a secret URL, an empty value keeps the saved Connector Credential.
    url: str = Field(default="", max_length=2000)
    url_secret: bool = False
    headers: list[ConnectorHeaderInput] = Field(default_factory=list, max_length=20)
    enabled: bool = True
    hidden_tools: list[str] = Field(default_factory=list, max_length=200)
    fetch_tool: str | None = Field(default=None, max_length=200)

    @field_validator("headers")
    @classmethod
    def unique_header_names(cls, headers: list[ConnectorHeaderInput]) -> list[ConnectorHeaderInput]:
        names = [header.name.lower() for header in headers]
        if len(names) != len(set(names)):
            raise ValueError("Each header name can be used only once.")
        return headers

    @field_validator("fetch_tool")
    @classmethod
    def blank_fetch_tool_is_none(cls, value: str | None) -> str | None:
        return value or None


class StoredHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    # None for a secret header; its value is a Connector Credential.
    value: str | None = None
    secret: bool = False
    # Whether a secret header's Connector Credential has been saved.
    configured: bool = False


class StoredConnector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^connector-[0-9a-f]{12}$")
    name: str
    # For a secret URL, only its origin is kept here.
    url: str
    url_secret: bool = False
    headers: list[StoredHeader] = Field(default_factory=list)
    enabled: bool = True
    hidden_tools: list[str] = Field(default_factory=list)
    fetch_tool: str | None = None
    preset: ConnectorPreset | None = None


class ConnectorsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    # Whether the default Connectors were created once; deleting them is respected.
    seeded: bool = False
    connectors: list[StoredConnector] = Field(default_factory=list)


class ConnectorHeaderView(BaseModel):
    name: str
    value: str | None = None
    secret: bool
    configured: bool


class ConnectorView(BaseModel):
    id: str
    name: str
    url: str
    url_secret: bool
    headers: list[ConnectorHeaderView]
    enabled: bool
    hidden_tools: list[str]
    fetch_tool: str | None
    preset: ConnectorPreset | None
    credential_storage: CredentialDiagnosticMode | None = None


class ConnectorTool(BaseModel):
    name: str
    description: str | None = None


class ConnectorTest(BaseModel):
    tools: list[ConnectorTool]


@dataclass(frozen=True)
class ResolvedConnector:
    """A Connector with its Connector Credentials, ready to connect."""

    id: str
    name: str
    url: str
    headers: dict[str, str]
    hidden_tools: frozenset[str]
    fetch_tool: str | None

    @property
    def tool_prefix(self) -> str:
        slug = "".join(char if char.isalnum() else "_" for char in self.name.lower()).strip("_")
        return (slug or "connector")[:24]


def exa_connector() -> StoredConnector:
    return StoredConnector(
        id=_connector_id(),
        name="Exa",
        url=EXA_URL,
        headers=[StoredHeader(name="x-api-key", secret=True)],
        # agent_run is slow and needs a paid key; the search and fetch tools cover research.
        hidden_tools=["agent_run"],
        fetch_tool="web_fetch_exa",
        preset=EXA_PRESET,
    )


class ConnectorStore:
    def __init__(self, store_path: Path, credential_store: CredentialStore) -> None:
        self.store_path = store_path
        self.credentials = credential_store

    @classmethod
    def for_provider_store(cls, provider_path: Path, *, use_os_keyring: bool) -> ConnectorStore:
        return cls(
            provider_path,
            CredentialStore.for_provider_store(
                provider_path / "connector-credentials",
                use_os_keyring=use_os_keyring,
                service=CONNECTOR_CREDENTIAL_SERVICE,
            ),
        )

    @property
    def path(self) -> Path:
        return self.store_path / "connectors.json"

    def views(self) -> list[ConnectorView]:
        return [self._view(connector) for connector in self._read().connectors]

    def create(self, request: ConnectorInput) -> ConnectorView:
        state = self._read()
        connector = StoredConnector(id=_connector_id(), name=request.name, url="")
        connector = self._apply(connector, request)
        state.connectors.append(connector)
        self._write(state)
        return self._view(connector)

    def update(self, connector_id: str, request: ConnectorInput) -> ConnectorView:
        state = self._read()
        index = self._index(state, connector_id)
        connector = self._apply(state.connectors[index], request)
        state.connectors[index] = connector
        self._write(state)
        return self._view(connector)

    def delete(self, connector_id: str) -> None:
        state = self._read()
        connector = state.connectors.pop(self._index(state, connector_id))
        self._write(state)
        for key in self._credential_keys(connector):
            self._forget(key)

    def restore_defaults(self) -> list[ConnectorView]:
        state = self._read()
        if not any(connector.preset == EXA_PRESET for connector in state.connectors):
            state.connectors.insert(0, exa_connector())
            self._write(state)
        return [self._view(connector) for connector in state.connectors]

    def resolve(self, connector_id: str) -> ResolvedConnector:
        state = self._read()
        return self._resolve(state.connectors[self._index(state, connector_id)])

    def enabled(self) -> list[ResolvedConnector]:
        """Every enabled Connector that can connect. Unreadable credentials skip it."""
        resolved: list[ResolvedConnector] = []
        for connector in self._read().connectors:
            if not connector.enabled:
                continue
            try:
                resolved.append(self._resolve(connector))
            except ConnectorError:
                continue
        return resolved

    def _apply(self, connector: StoredConnector, request: ConnectorInput) -> StoredConnector:
        """Validate a change, save its Connector Credentials, and return the stored form."""
        url_key = _url_key(connector.id)
        if request.url_secret:
            if request.url:
                _validate_url(request.url)
                self._save(url_key, request.url)
            elif not (connector.url_secret and self.credentials.read(url_key)):
                raise ConnectorError("Enter the Connector URL.")
            displayed_url = _origin(request.url) if request.url else connector.url
        else:
            _validate_url(request.url)
            displayed_url = request.url
            if connector.url_secret:
                self._forget(url_key)

        previous = {header.name.lower(): header for header in connector.headers}
        headers: list[StoredHeader] = []
        for header in request.headers:
            key = _header_key(connector.id, header.name)
            old = previous.get(header.name.lower())
            if header.secret:
                if header.value:
                    self._save(key, header.value)
                configured = bool(header.value) or (
                    old is not None and old.secret and old.configured
                )
                headers.append(StoredHeader(name=header.name, secret=True, configured=configured))
            else:
                headers.append(StoredHeader(name=header.name, value=header.value or ""))
                if old is not None and old.secret:
                    self._forget(key)
        kept = {header.name.lower() for header in request.headers}
        for name, header in previous.items():
            if name not in kept and header.secret:
                self._forget(_header_key(connector.id, header.name))

        return connector.model_copy(
            update={
                "name": request.name,
                "url": displayed_url,
                "url_secret": request.url_secret,
                "headers": headers,
                "enabled": request.enabled,
                "hidden_tools": sorted(set(request.hidden_tools)),
                "fetch_tool": request.fetch_tool,
            }
        )

    def _resolve(self, connector: StoredConnector) -> ResolvedConnector:
        url = connector.url
        if connector.url_secret:
            secret_url = self._read_secret(_url_key(connector.id))
            if secret_url is None:
                raise ConnectorError(f"The {connector.name} Connector URL is unavailable.")
            url = secret_url
        headers: dict[str, str] = {}
        for header in connector.headers:
            if header.secret:
                value = self._read_secret(_header_key(connector.id, header.name))
                # An unset secret header is simply not sent, e.g. Exa without a key.
                if value:
                    headers[header.name] = value
            elif header.value:
                headers[header.name] = header.value
        return ResolvedConnector(
            id=connector.id,
            name=connector.name,
            url=url,
            headers=headers,
            hidden_tools=frozenset(connector.hidden_tools),
            fetch_tool=connector.fetch_tool,
        )

    def _view(self, connector: StoredConnector) -> ConnectorView:
        modes = {self.credentials.diagnostics(key)[0] for key in self._credential_keys(connector)}
        return ConnectorView(
            id=connector.id,
            name=connector.name,
            url=connector.url,
            url_secret=connector.url_secret,
            headers=[
                ConnectorHeaderView(
                    name=header.name,
                    value=None if header.secret else header.value,
                    secret=header.secret,
                    configured=header.configured if header.secret else bool(header.value),
                )
                for header in connector.headers
            ],
            enabled=connector.enabled,
            hidden_tools=connector.hidden_tools,
            fetch_tool=connector.fetch_tool,
            preset=connector.preset,
            credential_storage=modes.pop() if len(modes) == 1 else None,
        )

    def _credential_keys(self, connector: StoredConnector) -> list[str]:
        """The keys of the Connector Credentials that have been saved."""
        keys = [
            _header_key(connector.id, header.name)
            for header in connector.headers
            if header.secret and header.configured
        ]
        if connector.url_secret:
            keys.append(_url_key(connector.id))
        return keys

    def _read_secret(self, key: str) -> str | None:
        try:
            return self.credentials.read(key)
        except CredentialStoreError as error:
            raise ConnectorError(str(error)) from error

    def _save(self, key: str, value: str) -> None:
        try:
            self.credentials.write(key, value)
        except CredentialStoreError as error:
            raise ConnectorError(str(error)) from error

    def _forget(self, key: str) -> None:
        try:
            self.credentials.delete(key)
        except CredentialStoreError:
            # Nothing was saved under this key, or it is already gone.
            return

    def _index(self, state: ConnectorsFile, connector_id: str) -> int:
        for index, connector in enumerate(state.connectors):
            if connector.id == connector_id:
                return index
        raise KeyError(connector_id)

    def _read(self) -> ConnectorsFile:
        try:
            state = ConnectorsFile.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            state = ConnectorsFile()
        except (OSError, ValidationError) as error:
            raise ConnectorError(f"Connector settings could not be read: {self.path}") from error
        if not state.seeded:
            state = ConnectorsFile(seeded=True, connectors=[exa_connector(), *state.connectors])
            self._write(state)
        return state

    def _write(self, state: ConnectorsFile) -> None:
        self.store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".json.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(state.model_dump_json(indent=2).encode("utf-8") + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)


def _connector_id() -> str:
    return f"connector-{uuid4().hex[:12]}"


def _header_key(connector_id: str, header: str) -> str:
    return f"{connector_id}.header.{header.lower()}"


def _url_key(connector_id: str) -> str:
    return f"{connector_id}.url"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"


def _validate_url(url: str) -> None:
    """Connectors use HTTPS, or HTTP only to this machine."""
    parts = urlsplit(url)
    if not parts.hostname or parts.username or parts.password:
        raise ConnectorError("Enter a full Connector URL, such as https://mcp.example.com/mcp.")
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and _is_loopback(parts.hostname):
        return
    raise ConnectorError("Connector URLs must use HTTPS, or HTTP to this computer.")


def _is_loopback(hostname: str) -> bool:
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
