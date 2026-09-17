"""Provider credential storage with an opportunistic OS-keyring backend.

The private JSON fallback is deliberately retained: it is the portable store for
installations without a safe keyring and it is the source for pre-keyring
installations.  Its mode metadata prevents a failed keyring lookup from falling
back to an older file value after a credential has moved to the OS keyring.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol

CredentialStorageMode = Literal["keyring", "file"]
CredentialDiagnosticMode = Literal["os-keyring", "private-json-file", "unavailable", "unconfigured"]

_SERVICE_NAME = "course-harness.provider-credentials"
_LEGACY_ACCOUNT_ID = "provider-legacy"


class KeyringBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def delete_password(self, service: str, username: str) -> object: ...


class CredentialStoreError(ValueError):
    """A credential could not be changed without risking its account state."""


class CredentialStore:
    """Read and write one provider credential per account.

    Supplying a backend is intended for explicit host integration and tests.
    Normal temporary/injected provider paths intentionally use the private JSON
    fallback, so tests and embeddings cannot unexpectedly access a user's OS
    keyring.
    """

    def __init__(self, store_path: Path, *, keyring_backend: KeyringBackend | None = None) -> None:
        self.store_path = store_path
        self._keyring_backend = keyring_backend

    @classmethod
    def for_provider_store(cls, store_path: Path, *, use_os_keyring: bool) -> CredentialStore:
        return cls(store_path, keyring_backend=_safe_keyring_backend() if use_os_keyring else None)

    @property
    def credentials_path(self) -> Path:
        return self.store_path / "credentials.json"

    @property
    def metadata_path(self) -> Path:
        return self.store_path / "credential-modes.json"

    def read(self, account_id: str) -> str | None:
        """Return the credential, never substituting an old fallback for keyring mode."""
        if self.mode_for(account_id) == "keyring":
            if self._keyring_backend is None:
                return None
            try:
                return self._keyring_backend.get_password(_SERVICE_NAME, account_id)
            except Exception:
                return None
        return self._read_file_credentials().get(account_id)

    def write(self, account_id: str, credential: str) -> CredentialStorageMode:
        """Prefer a usable keyring; fall back to the private JSON store on failure."""
        if self._keyring_backend is not None:
            # Claim keyring mode before touching the backend. If metadata cannot
            # be saved, no secret is changed; after it is saved, no stale file
            # value can be mistaken for the rotated credential.
            self._set_mode(account_id, "keyring")
            try:
                self._keyring_backend.set_password(_SERVICE_NAME, account_id, credential)
                self._remove_file_credential(account_id)
                return "keyring"
            except Exception:
                # A failed keyring operation must remain usable through the
                # supported fallback.  If metadata writes themselves fail, the
                # file value is retained but is not silently preferred.
                self._write_file_credential(account_id, credential)
                self._set_mode(account_id, "file")
                return "file"

        self._set_mode(account_id, "file")
        self._write_file_credential(account_id, credential)
        return "file"

    def delete(self, account_id: str) -> None:
        """Remove an account credential without hiding a failed keyring delete."""
        if self.mode_for(account_id) == "keyring":
            if self._keyring_backend is None:
                raise CredentialStoreError("The operating-system keyring is unavailable.")
            try:
                result = self._keyring_backend.delete_password(_SERVICE_NAME, account_id)
            except Exception as error:
                if not _is_missing_keyring_credential(error):
                    raise CredentialStoreError(
                        "The operating-system keyring could not delete the key."
                    ) from error
                result = None
            if result is False:
                raise CredentialStoreError("The operating-system keyring could not delete the key.")
        self._remove_file_credential(account_id)
        self._remove_mode(account_id)

    def mode_for(self, account_id: str) -> CredentialStorageMode:
        # Existing JSON and {"api_key": ...} installations predate metadata and
        # are consequently file-owned until a user explicitly saves/rotates one.
        return self._read_modes().get(account_id, "file")

    def has_reference(self, account_id: str) -> bool:
        """Return whether non-secret metadata identifies a stored credential."""
        try:
            modes = self._read_modes()
        except CredentialStoreError:
            return False
        if account_id in modes:
            return True
        # Pre-keyring installations have no mode metadata. File presence is
        # their only non-secret diagnostic signal.
        return self.credentials_path.is_file()

    def diagnostics(self, account_id: str | None) -> tuple[CredentialDiagnosticMode, str]:
        """Return mode and location without calling a keyring or reading a secret."""
        try:
            modes = self._read_modes()
        except CredentialStoreError:
            return ("unavailable", f"Unreadable storage metadata: {self.metadata_path}")
        if account_id is None:
            return ("unconfigured", str(self.credentials_path))
        mode = modes.get(account_id, "file")
        if mode == "keyring":
            return ("os-keyring", "Operating-system keyring")
        return ("private-json-file", str(self.credentials_path))

    def _read_modes(self) -> dict[str, CredentialStorageMode]:
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise CredentialStoreError("Credential storage metadata is unreadable.") from error
        if not isinstance(payload, Mapping):
            raise CredentialStoreError("Credential storage metadata is invalid.")
        return {
            account_id: mode
            for account_id, mode in payload.items()
            if isinstance(account_id, str) and mode in {"keyring", "file"}
        }

    def _set_mode(self, account_id: str, mode: CredentialStorageMode) -> None:
        modes = self._read_modes()
        modes[account_id] = mode
        self._write_private_json(self.metadata_path, modes)

    def _remove_mode(self, account_id: str) -> None:
        modes = self._read_modes()
        modes.pop(account_id, None)
        self._write_private_json(self.metadata_path, modes)

    def _read_file_credentials(self) -> dict[str, str]:
        try:
            payload = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise CredentialStoreError("The private credential file is unreadable.") from error
        if not isinstance(payload, Mapping):
            raise CredentialStoreError("The private credential file is invalid.")
        credentials = {
            account_id: credential
            for account_id, credential in payload.items()
            if account_id != "api_key"
            and isinstance(account_id, str)
            and isinstance(credential, str)
            and credential
        }
        legacy = payload.get("api_key")
        if isinstance(legacy, str) and legacy:
            credentials[_LEGACY_ACCOUNT_ID] = legacy
        return credentials

    def _write_file_credential(self, account_id: str, credential: str) -> None:
        credentials = self._read_file_credentials()
        credentials[account_id] = credential
        self._write_private_json(self.credentials_path, credentials)

    def _remove_file_credential(self, account_id: str) -> None:
        credentials = self._read_file_credentials()
        if account_id not in credentials:
            return
        credentials.pop(account_id)
        if credentials:
            self._write_private_json(self.credentials_path, credentials)
        else:
            self.credentials_path.unlink(missing_ok=True)

    def _write_private_json(self, path: Path, payload: object) -> None:
        self.store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.store_path, 0o700)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _safe_keyring_backend() -> KeyringBackend | None:
    """Return a non-alt keyring backend, without prompting or accessing a secret."""
    try:
        keyring = importlib.import_module("keyring")
        backend = keyring.get_keyring()
        if not _backend_is_safe(backend):
            return None
        return backend
    except Exception:
        return None


def _backend_is_safe(backend: object) -> bool:
    module = type(backend).__module__
    priority = getattr(backend, "priority", 0)
    if not isinstance(priority, (int, float)) or priority <= 0:
        return False
    chained = getattr(backend, "backends", None)
    if chained is not None:
        return (
            isinstance(chained, (list, tuple))
            and bool(chained)
            and all(_backend_is_safe(candidate) for candidate in chained)
        )
    return module in {
        "keyring.backends.SecretService",
        "keyring.backends.Windows",
        "keyring.backends.kwallet",
        "keyring.backends.macOS",
    }


def _is_missing_keyring_credential(error: Exception) -> bool:
    try:
        errors = importlib.import_module("keyring.errors")
        return isinstance(error, errors.PasswordDeleteError)
    except AttributeError, ImportError, TypeError:
        return False
