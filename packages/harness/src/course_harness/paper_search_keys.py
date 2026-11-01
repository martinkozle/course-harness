"""Paper Search Keys: optional API keys for the native paper indexes.

Paper search is native rather than a Connector (ADR 0013), so its keys are stored
beside Connector Credentials in their own credential store. They are never returned
over HTTP or written to a Course Workspace.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from course_harness.credential_store import (
    CredentialDiagnosticMode,
    CredentialStore,
    CredentialStoreError,
)

PAPER_SEARCH_CREDENTIAL_SERVICE = "course-harness.paper-search-credentials"

PaperSearchKeyProvider = Literal["semantic_scholar"]
PROVIDER_LABELS: dict[PaperSearchKeyProvider, str] = {"semantic_scholar": "Semantic Scholar"}


class PaperSearchKeyError(ValueError):
    """A Paper Search Key could not be read or changed as requested."""


class PaperSearchKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4000)


class PaperSearchKeyView(BaseModel):
    provider: PaperSearchKeyProvider
    label: str
    configured: bool
    credential_storage: CredentialDiagnosticMode | None = None


class PaperSearchKeysFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    configured: list[PaperSearchKeyProvider] = Field(default_factory=list)


class PaperSearchKeyStore:
    def __init__(self, store_path: Path, credential_store: CredentialStore) -> None:
        self.store_path = store_path
        self.credentials = credential_store

    @classmethod
    def for_provider_store(
        cls, provider_path: Path, *, use_os_keyring: bool
    ) -> PaperSearchKeyStore:
        return cls(
            provider_path,
            CredentialStore.for_provider_store(
                provider_path / "paper-search-credentials",
                use_os_keyring=use_os_keyring,
                service=PAPER_SEARCH_CREDENTIAL_SERVICE,
            ),
        )

    @property
    def path(self) -> Path:
        return self.store_path / "paper-search-keys.json"

    def views(self) -> list[PaperSearchKeyView]:
        configured = set(self._read().configured)
        return [
            PaperSearchKeyView(
                provider=provider,
                label=label,
                configured=provider in configured,
                credential_storage=(
                    self.credentials.diagnostics(provider)[0] if provider in configured else None
                ),
            )
            for provider, label in PROVIDER_LABELS.items()
        ]

    def save(self, provider: PaperSearchKeyProvider, request: PaperSearchKeyInput) -> None:
        state = self._read()
        try:
            self.credentials.write(provider, request.api_key.get_secret_value().strip())
        except CredentialStoreError as error:
            raise PaperSearchKeyError(str(error)) from error
        if provider not in state.configured:
            state.configured.append(provider)
            self._write(state)

    def delete(self, provider: PaperSearchKeyProvider) -> None:
        state = self._read()
        if provider not in state.configured:
            return
        state.configured.remove(provider)
        self._write(state)
        try:
            self.credentials.delete(provider)
        except CredentialStoreError:
            # Nothing was saved under this key, or it is already gone.
            return

    def keys(self) -> dict[str, str]:
        """Every saved key that can be read. An unreadable key is simply not sent."""
        keys: dict[str, str] = {}
        try:
            configured = self._read().configured
        except PaperSearchKeyError:
            return keys
        for provider in configured:
            try:
                value = self.credentials.read(provider)
            except CredentialStoreError:
                continue
            if value:
                keys[provider] = value
        return keys

    def _read(self) -> PaperSearchKeysFile:
        try:
            return PaperSearchKeysFile.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return PaperSearchKeysFile()
        except (OSError, ValidationError) as error:
            raise PaperSearchKeyError(
                f"Paper search settings could not be read: {self.path}"
            ) from error

    def _write(self, state: PaperSearchKeysFile) -> None:
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
