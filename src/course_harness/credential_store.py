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
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Never, Protocol

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


@dataclass(frozen=True)
class CredentialRecovery:
    """The exact credential state needed to compensate a higher-level mutation."""

    account_id: str
    credential: str
    mode: CredentialStorageMode
    metadata_snapshot: bytes | None
    credentials_snapshot: bytes | None


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
        # Validate both private stores before changing the keyring.  This makes a
        # corrupt legacy file a safe, fail-closed error rather than a half-applied
        # keyring rotation.
        modes = self._read_modes()
        previous_mode = modes.get(account_id, "file")
        self._read_file_credentials()
        metadata_snapshot = self._snapshot(self.metadata_path)
        credentials_snapshot = self._snapshot(self.credentials_path)
        if self._keyring_backend is not None:
            previous_keyring_credential = self._keyring_credential(account_id)
            try:
                self._keyring_backend.set_password(_SERVICE_NAME, account_id, credential)
            except Exception as error:
                # Some backends can change a secret and still raise. Restore the
                # known prior value before committing the file fallback.
                if not self._recover_failed_keyring_write(
                    account_id,
                    previous_mode,
                    previous_keyring_credential,
                ):
                    raise CredentialStoreError(
                        "The operating-system keyring credential could not be updated safely."
                    ) from error
                return self._commit_file_credential(
                    account_id,
                    credential,
                    metadata_snapshot,
                    credentials_snapshot,
                    error,
                )
            try:
                self._remove_file_credential(account_id)
                # Commit mode metadata only after the target keyring value and
                # stale-file cleanup both succeeded.
                self._set_mode(account_id, "keyring")
                return "keyring"
            except Exception as error:
                compensation_errors = self._restore_keyring_and_private(
                    account_id,
                    previous_keyring_credential,
                    metadata_snapshot,
                    credentials_snapshot,
                )
                self._raise_compensation_failure(
                    "The operating-system keyring credential could not be updated safely.",
                    error,
                    compensation_errors,
                )

        return self._commit_file_credential(
            account_id,
            credential,
            metadata_snapshot,
            credentials_snapshot,
        )

    def delete(self, account_id: str) -> CredentialRecovery:
        """Remove an account credential without hiding a failed keyring delete."""
        modes = self._read_modes()
        mode = modes.get(account_id, "file")
        metadata_snapshot = self._snapshot(self.metadata_path)
        credentials_snapshot = self._snapshot(self.credentials_path)
        previous_keyring_credential: str | None = None
        if mode == "keyring":
            if self._keyring_backend is None:
                raise CredentialStoreError("The operating-system keyring is unavailable.")
            previous_keyring_credential = self._keyring_credential(account_id)
            if previous_keyring_credential is None:
                raise CredentialStoreError(
                    "The operating-system keyring credential is unavailable."
                )
            try:
                self._keyring_backend.delete_password(_SERVICE_NAME, account_id)
            except Exception as error:
                raise CredentialStoreError(
                    "The operating-system keyring could not delete the key."
                ) from error
            credential = previous_keyring_credential
        else:
            credential = self._read_file_credentials().get(account_id)
            if credential is None:
                raise CredentialStoreError("The private credential is unavailable.")
        recovery = CredentialRecovery(
            account_id=account_id,
            credential=credential,
            mode=mode,
            metadata_snapshot=metadata_snapshot,
            credentials_snapshot=credentials_snapshot,
        )
        try:
            self._remove_file_credential(account_id)
            self._remove_mode(account_id)
        except Exception as error:
            compensation_errors: list[Exception] = []
            try:
                self._restore_private_snapshots(metadata_snapshot, credentials_snapshot)
            except CredentialStoreError as restore_error:
                compensation_errors.append(restore_error)
            if mode == "keyring":
                compensation_errors.extend(
                    self._restore_keyring_and_private(
                        account_id,
                        previous_keyring_credential,
                        metadata_snapshot=None,
                        credentials_snapshot=None,
                        restore_private=False,
                    )
                )
            self._raise_compensation_failure(
                "The credential could not be removed safely.",
                error,
                compensation_errors,
            )
        return recovery

    def private_state(self) -> tuple[bytes | None, bytes | None]:
        """Capture mode and fallback-file state for a compensating operation."""
        return (self._snapshot(self.metadata_path), self._snapshot(self.credentials_path))

    def restore_recovery(
        self,
        recovery: CredentialRecovery,
        *,
        post_delete_metadata: bytes | None,
        post_delete_credentials: bytes | None,
    ) -> None:
        """Restore a deleted credential or undo a partial restoration safely."""
        restored_keyring = False
        if recovery.mode == "keyring":
            if self._keyring_backend is None:
                raise CredentialStoreError("The operating-system keyring is unavailable.")
            try:
                self._keyring_backend.set_password(
                    _SERVICE_NAME,
                    recovery.account_id,
                    recovery.credential,
                )
                restored_keyring = True
                self._restore_private_snapshots(
                    recovery.metadata_snapshot,
                    recovery.credentials_snapshot,
                )
            except Exception as error:
                self._undo_partial_recovery(
                    recovery.account_id,
                    restored_keyring,
                    post_delete_metadata,
                    post_delete_credentials,
                )
                raise CredentialStoreError(
                    "The operating-system keyring credential could not be restored safely."
                ) from error
            return
        try:
            self._restore_private_snapshots(
                recovery.metadata_snapshot,
                recovery.credentials_snapshot,
            )
        except Exception as error:
            self._undo_partial_recovery(
                recovery.account_id,
                False,
                post_delete_metadata,
                post_delete_credentials,
            )
            raise CredentialStoreError(
                "The private credential could not be restored safely."
            ) from error

    def mode_for(self, account_id: str) -> CredentialStorageMode:
        # Existing JSON and {"api_key": ...} installations predate metadata and
        # are consequently file-owned until a user explicitly saves/rotates one.
        return self._read_modes().get(account_id, "file")

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
            if self._keyring_backend is None:
                return ("unavailable", "Operating-system keyring is unavailable")
            return ("os-keyring", "Operating-system keyring")
        # Diagnostics deliberately inspect only file identity, never its secret
        # contents. A malformed legacy file remains observable here and yields a
        # safe actionable error only when a caller attempts to use it.
        if not self.credentials_path.is_file():
            return ("unavailable", f"Credential is missing from: {self.credentials_path}")
        return ("private-json-file", str(self.credentials_path))

    def _commit_file_credential(
        self,
        account_id: str,
        credential: str,
        metadata_snapshot: bytes | None,
        credentials_snapshot: bytes | None,
        cause: Exception | None = None,
    ) -> CredentialStorageMode:
        try:
            self._write_file_credential(account_id, credential)
            # Commit mode metadata after the private target is durable.
            self._set_mode(account_id, "file")
            return "file"
        except Exception as error:
            self._restore_private_snapshots(metadata_snapshot, credentials_snapshot)
            raise CredentialStoreError("The private credential could not be updated safely.") from (
                cause or error
            )

    def _keyring_credential(self, account_id: str) -> str | None:
        assert self._keyring_backend is not None
        try:
            return self._keyring_backend.get_password(_SERVICE_NAME, account_id)
        except Exception as error:
            raise CredentialStoreError("The operating-system keyring is unavailable.") from error

    def _restore_keyring_credential(self, account_id: str, credential: str | None) -> None:
        assert self._keyring_backend is not None
        try:
            if credential is None:
                self._keyring_backend.delete_password(_SERVICE_NAME, account_id)
            else:
                self._keyring_backend.set_password(_SERVICE_NAME, account_id, credential)
        except Exception as error:
            raise CredentialStoreError(
                "The operating-system keyring credential could not be restored safely."
            ) from error

    def _recover_failed_keyring_write(
        self,
        account_id: str,
        previous_mode: CredentialStorageMode,
        previous_keyring_credential: str | None,
    ) -> bool:
        if previous_mode == "keyring":
            self._restore_keyring_credential(account_id, previous_keyring_credential)
            return False
        if previous_keyring_credential is not None:
            self._restore_keyring_credential(account_id, previous_keyring_credential)
            return True
        # A new or file-owned account has no keyring state the application owns.
        # Try to remove a value from a backend that may have changed despite
        # raising, but retain the documented private-file fallback if that
        # best-effort cleanup reports an absent/cancelled item.
        assert self._keyring_backend is not None
        with suppress(Exception):
            self._keyring_backend.delete_password(_SERVICE_NAME, account_id)
        return True

    def _snapshot(self, path: Path) -> bytes | None:
        try:
            return path.read_bytes() if path.exists() else None
        except OSError as error:
            raise CredentialStoreError("Credential storage is unreadable.") from error

    def _restore_private_snapshots(
        self,
        metadata_snapshot: bytes | None,
        credentials_snapshot: bytes | None,
    ) -> None:
        errors: list[Exception] = []
        try:
            self._restore_snapshot(self.metadata_path, metadata_snapshot)
        except OSError as error:
            errors.append(error)
        try:
            self._restore_snapshot(self.credentials_path, credentials_snapshot)
        except OSError as error:
            errors.append(error)
        if errors:
            raise CredentialStoreError("Credential storage could not be restored safely.") from (
                self._combined_error(errors)
            )

    def _restore_keyring_and_private(
        self,
        account_id: str,
        keyring_credential: str | None,
        metadata_snapshot: bytes | None,
        credentials_snapshot: bytes | None,
        *,
        restore_private: bool = True,
    ) -> list[Exception]:
        errors: list[Exception] = []
        if restore_private:
            try:
                self._restore_private_snapshots(metadata_snapshot, credentials_snapshot)
            except CredentialStoreError as error:
                errors.append(error)
        try:
            self._restore_keyring_credential(account_id, keyring_credential)
        except CredentialStoreError as error:
            errors.append(error)
        return errors

    def _raise_compensation_failure(
        self,
        message: str,
        original_error: Exception,
        compensation_errors: list[Exception],
    ) -> Never:
        if compensation_errors:
            raise CredentialStoreError(message) from self._combined_error(
                [original_error, *compensation_errors]
            )
        raise CredentialStoreError(message) from original_error

    def _combined_error(self, errors: list[Exception]) -> Exception:
        if len(errors) == 1:
            return errors[0]
        return ExceptionGroup("Credential storage recovery failures", errors)

    def _undo_partial_recovery(
        self,
        account_id: str,
        restored_keyring: bool,
        post_delete_metadata: bytes | None,
        post_delete_credentials: bytes | None,
    ) -> None:
        try:
            if restored_keyring:
                assert self._keyring_backend is not None
                self._keyring_backend.delete_password(_SERVICE_NAME, account_id)
            self._restore_private_snapshots(post_delete_metadata, post_delete_credentials)
        except Exception as error:
            raise CredentialStoreError("Credential recovery could not be undone safely.") from error

    def _restore_snapshot(self, path: Path, snapshot: bytes | None) -> None:
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        self._write_private_bytes(path, snapshot)

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
        if modes:
            self._write_private_json(self.metadata_path, modes)
        else:
            self.metadata_path.unlink(missing_ok=True)

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
        self._write_private_bytes(path, json.dumps(payload, indent=2).encode("utf-8") + b"\n")

    def _write_private_bytes(self, path: Path, payload: bytes) -> None:
        self.store_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.store_path, 0o700)
        temporary_path = path.with_suffix(f"{path.suffix}.tmp")
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
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
