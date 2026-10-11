"""Document processing choices and Processor Keys for this installation (ADR 0015).

Processor Keys live in installation credential storage beside Connector Credentials and
Paper Search Keys. They are never returned over HTTP or written to a Course Workspace.
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

PROCESSOR_KEY_SERVICE = "course-harness.processor-keys"

ProcessorKeyVendor = Literal["llamaparse", "mistral"]
PROCESSOR_KEY_LABELS: dict[ProcessorKeyVendor, str] = {
    "llamaparse": "LlamaParse",
    "mistral": "Mistral",
}
# Vendors a document may be sent to. A Model Preset's vendor is its Provider Account kind.
ConsentVendor = Literal["llamaparse", "mistral", "anthropic", "openai", "openrouter", "bedrock"]


class DocumentProcessingError(ValueError):
    """Document processing settings could not be read or changed as requested."""


class ProcessorKeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: SecretStr = Field(min_length=1, max_length=4000)


class ProcessorKeyView(BaseModel):
    vendor: ProcessorKeyVendor
    label: str
    configured: bool
    credential_storage: CredentialDiagnosticMode | None = None


class DocumentProcessingPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The processor offered, or applied, when a quick result fails its quality check.
    preferred_processor: str | None = Field(default=None, max_length=200)
    # Apply the preferred processor without asking. Off unless the Course Author opts in.
    auto_upgrade: bool = False


class DocumentProcessingFile(DocumentProcessingPreferences):
    version: Literal[1] = 1
    consented_vendors: list[ConsentVendor] = Field(default_factory=list)
    configured_keys: list[ProcessorKeyVendor] = Field(default_factory=list)


class DocumentProcessingStore:
    def __init__(self, store_path: Path, credential_store: CredentialStore) -> None:
        self.store_path = store_path
        self.credentials = credential_store

    @classmethod
    def for_provider_store(
        cls, provider_path: Path, *, use_os_keyring: bool
    ) -> DocumentProcessingStore:
        return cls(
            provider_path,
            CredentialStore.for_provider_store(
                provider_path / "processor-credentials",
                use_os_keyring=use_os_keyring,
                service=PROCESSOR_KEY_SERVICE,
            ),
        )

    @property
    def path(self) -> Path:
        return self.store_path / "document-processing.json"

    def read(self) -> DocumentProcessingFile:
        try:
            return DocumentProcessingFile.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return DocumentProcessingFile()
        except (OSError, ValidationError) as error:
            raise DocumentProcessingError(
                f"Document processing settings could not be read: {self.path}"
            ) from error

    def update_preferences(self, preferences: DocumentProcessingPreferences) -> None:
        state = self.read()
        self._write(state.model_copy(update=preferences.model_dump()))

    def set_consent(self, vendor: ConsentVendor, consented: bool) -> None:
        state = self.read()
        vendors = [v for v in state.consented_vendors if v != vendor]
        if consented:
            vendors.append(vendor)
        self._write(state.model_copy(update={"consented_vendors": vendors}))

    def key_views(self) -> list[ProcessorKeyView]:
        configured = set(self.read().configured_keys)
        return [
            ProcessorKeyView(
                vendor=vendor,
                label=label,
                configured=vendor in configured,
                credential_storage=(
                    self.credentials.diagnostics(vendor)[0] if vendor in configured else None
                ),
            )
            for vendor, label in PROCESSOR_KEY_LABELS.items()
        ]

    def save_key(self, vendor: ProcessorKeyVendor, request: ProcessorKeyInput) -> None:
        state = self.read()
        try:
            self.credentials.write(vendor, request.api_key.get_secret_value().strip())
        except CredentialStoreError as error:
            raise DocumentProcessingError(str(error)) from error
        if vendor not in state.configured_keys:
            self._write(
                state.model_copy(update={"configured_keys": [*state.configured_keys, vendor]})
            )

    def delete_key(self, vendor: ProcessorKeyVendor) -> None:
        state = self.read()
        if vendor not in state.configured_keys:
            return
        self._write(
            state.model_copy(
                update={"configured_keys": [v for v in state.configured_keys if v != vendor]}
            )
        )
        try:
            self.credentials.delete(vendor)
        except CredentialStoreError:
            # Nothing was saved under this key, or it is already gone.
            return

    def key(self, vendor: ProcessorKeyVendor) -> str | None:
        """A saved Processor Key, or ``None`` when it is missing or cannot be read."""
        try:
            if vendor not in self.read().configured_keys:
                return None
            return self.credentials.read(vendor) or None
        except DocumentProcessingError, CredentialStoreError:
            return None

    def _write(self, state: DocumentProcessingFile) -> None:
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
