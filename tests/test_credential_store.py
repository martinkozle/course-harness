import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from keyring.errors import PasswordDeleteError
from pydantic import SecretStr

from course_harness import credential_store as credential_store_module
from course_harness import providers as providers_module
from course_harness.credential_store import CredentialStore, CredentialStoreError
from course_harness.providers import (
    ModelPresetRequest,
    ProviderAccountCredentialRequest,
    ProviderAccountRequest,
    ProviderCapabilities,
    ProviderConfigurationRequest,
    delete_provider_account,
    read_model_catalog,
    replace_provider_account_credential,
    resolve_active_model,
    runtime_provider_status,
    save_model_preset,
    save_provider_account,
    save_provider_configuration,
    select_model_preset,
)


class FakeKeyring:
    priority = 1

    def __init__(self) -> None:
        self.secrets: dict[tuple[str, str], str] = {}
        self.get_calls = 0
        self.fail_write = False
        self.fail_delete = False
        self.missing_delete = False

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls += 1
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.fail_write:
            raise RuntimeError("locked")
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.fail_delete:
            raise RuntimeError("locked")
        if self.missing_delete:
            raise PasswordDeleteError("missing")
        self.secrets.pop((service, username), None)


def _account_request(secret: str) -> ProviderAccountRequest:
    return ProviderAccountRequest(
        name="OpenRouter",
        kind="openrouter",
        api_key=SecretStr(secret),
    )


def test_keyring_write_removes_stale_file_and_records_non_secret_mode(tmp_path: Path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    store.credentials_path.parent.mkdir(parents=True)
    store.credentials_path.write_text('{"account-1": "stale"}\n', encoding="utf-8")

    assert store.write("account-1", "current") == "keyring"

    assert store.read("account-1") == "current"
    assert not store.credentials_path.exists()
    assert json.loads(store.metadata_path.read_text(encoding="utf-8")) == {"account-1": "keyring"}
    assert store.store_path.stat().st_mode & 0o777 == 0o700
    assert store.metadata_path.stat().st_mode & 0o777 == 0o600


def test_failed_keyring_write_uses_private_file_fallback(tmp_path: Path) -> None:
    backend = FakeKeyring()
    backend.fail_write = True
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)

    assert store.write("account-1", "fallback") == "file"

    assert store.read("account-1") == "fallback"
    assert json.loads(store.metadata_path.read_text(encoding="utf-8")) == {"account-1": "file"}
    assert store.credentials_path.stat().st_mode & 0o777 == 0o600


def test_keyring_owned_read_never_uses_a_stale_file_value(tmp_path: Path) -> None:
    store_path = tmp_path / "provider"
    store_path.mkdir()
    (store_path / "credential-modes.json").write_text(
        '{"account-1": "keyring"}\n', encoding="utf-8"
    )
    (store_path / "credentials.json").write_text('{"account-1": "stale"}\n', encoding="utf-8")

    store = CredentialStore(store_path)

    assert store.read("account-1") is None
    assert store.diagnostics("account-1") == ("os-keyring", "Operating-system keyring")


def test_existing_legacy_json_remains_file_owned(tmp_path: Path) -> None:
    store_path = tmp_path / "provider"
    store_path.mkdir()
    (store_path / "credentials.json").write_text('{"api_key": "legacy-secret"}\n', encoding="utf-8")
    backend = FakeKeyring()
    store = CredentialStore(store_path, keyring_backend=backend)

    assert store.read("provider-legacy") == "legacy-secret"
    assert backend.get_calls == 0
    assert store.mode_for("provider-legacy") == "file"


def test_mixed_legacy_json_preserves_every_credential_when_updated(tmp_path: Path) -> None:
    store_path = tmp_path / "provider"
    store_path.mkdir()
    (store_path / "credentials.json").write_text(
        '{"api_key": "legacy", "account-1": "first"}\n', encoding="utf-8"
    )
    store = CredentialStore(store_path)

    store.write("account-2", "second")

    assert json.loads(store.credentials_path.read_text(encoding="utf-8")) == {
        "provider-legacy": "legacy",
        "account-1": "first",
        "account-2": "second",
    }


def test_malformed_private_file_is_never_overwritten(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "provider")
    store.store_path.mkdir()
    store.credentials_path.write_text("not json\n", encoding="utf-8")

    with pytest.raises(CredentialStoreError, match="unreadable"):
        store.write("account-2", "second")

    assert store.credentials_path.read_text(encoding="utf-8") == "not json\n"


def test_malformed_mode_metadata_fails_closed_without_stale_fallback(tmp_path: Path) -> None:
    store = CredentialStore(tmp_path / "provider")
    store.store_path.mkdir()
    store.metadata_path.write_text("not json\n", encoding="utf-8")
    store.credentials_path.write_text('{"account-1": "stale"}\n', encoding="utf-8")

    with pytest.raises(CredentialStoreError, match="metadata is unreadable"):
        store.read("account-1")
    with pytest.raises(CredentialStoreError, match="metadata is unreadable"):
        store.write("account-2", "second")

    assert store.diagnostics("account-1") == (
        "unavailable",
        f"Unreadable storage metadata: {store.metadata_path}",
    )
    assert store.metadata_path.read_text(encoding="utf-8") == "not json\n"
    assert json.loads(store.credentials_path.read_text(encoding="utf-8")) == {"account-1": "stale"}


def test_rotation_updates_keyring_without_replacing_account(tmp_path: Path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    account = save_provider_account(
        store.store_path,
        _account_request("original"),
        credential_store=store,
    )

    rotated = replace_provider_account_credential(
        store.store_path,
        account.id,
        ProviderAccountCredentialRequest(api_key=SecretStr("replacement")),
        credential_store=store,
    )

    assert rotated == account
    assert store.read(account.id) == "replacement"


def test_failed_keyring_delete_preserves_provider_metadata(tmp_path: Path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    account = save_provider_account(
        store.store_path,
        _account_request("secret"),
        credential_store=store,
    )
    backend.fail_delete = True

    with pytest.raises(CredentialStoreError, match="could not delete"):
        delete_provider_account(store.store_path, account.id, credential_store=store)

    catalog = read_model_catalog(store.store_path, credential_store=store)
    assert catalog.provider_accounts == [account]
    assert store.mode_for(account.id) == "keyring"


def test_missing_keyring_credential_does_not_block_idempotent_delete(tmp_path: Path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    store.write("account-1", "secret")
    backend.missing_delete = True

    store.delete("account-1")

    assert "account-1" not in json.loads(store.metadata_path.read_text(encoding="utf-8"))


def test_catalog_write_failure_restores_deleted_keyring_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    account = save_provider_account(
        store.store_path,
        _account_request("secret"),
        credential_store=store,
    )

    def fail_catalog_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(providers_module, "_write_catalog", fail_catalog_write)

    with pytest.raises(OSError, match="disk full"):
        delete_provider_account(store.store_path, account.id, credential_store=store)

    assert store.read(account.id) == "secret"
    assert read_model_catalog(store.store_path, credential_store=store).provider_accounts == [
        account
    ]


def test_runtime_diagnostics_do_not_read_keyring_secret(tmp_path: Path) -> None:
    backend = FakeKeyring()
    store = CredentialStore(tmp_path / "provider", keyring_backend=backend)
    account = save_provider_account(
        store.store_path,
        _account_request("secret"),
        credential_store=store,
    )
    preset = save_model_preset(
        store.store_path,
        ModelPresetRequest(
            name="Planning",
            provider_account_id=account.id,
            model="example/model",
        ),
        ProviderCapabilities(
            tool_calling=True,
            structured_output=True,
            streaming=True,
            context_window=32_000,
            vision=False,
        ),
        credential_store=store,
    )
    before = backend.get_calls

    status = runtime_provider_status(store.store_path)

    assert status.configured
    assert status.selected_model_id == preset.id
    assert status.credential_storage.mode == "os-keyring"
    assert backend.get_calls == before
    assert store.mode_for(account.id) == "keyring"


def test_selected_catalog_never_falls_back_to_stale_legacy_provider(tmp_path: Path) -> None:
    store_path = tmp_path / "provider"
    capabilities = ProviderCapabilities(
        tool_calling=True,
        structured_output=True,
        streaming=True,
        context_window=32_000,
        vision=False,
    )
    save_provider_configuration(
        store_path,
        ProviderConfigurationRequest(
            kind="openrouter",
            model="legacy/model",
            api_key=SecretStr("legacy-secret"),
        ),
        capabilities,
        credential_store=CredentialStore(store_path),
    )
    backend = FakeKeyring()
    keyring_store = CredentialStore(store_path, keyring_backend=backend)
    account = save_provider_account(
        store_path,
        _account_request("current-secret"),
        credential_store=keyring_store,
    )
    preset = save_model_preset(
        store_path,
        ModelPresetRequest(
            name="Current",
            provider_account_id=account.id,
            model="current/model",
        ),
        capabilities,
        credential_store=keyring_store,
    )
    select_model_preset(
        store_path,
        preset.id,
        credential_store=keyring_store,
    )

    assert (
        resolve_active_model(
            store_path,
            credential_store=CredentialStore(store_path),
        )
        is None
    )


def test_legacy_diagnostics_require_a_credential_reference(tmp_path: Path) -> None:
    store_path = tmp_path / "provider"
    store = CredentialStore(store_path)
    save_provider_configuration(
        store_path,
        ProviderConfigurationRequest(
            kind="openrouter",
            model="legacy/model",
            api_key=SecretStr("legacy-secret"),
        ),
        ProviderCapabilities(
            tool_calling=True,
            structured_output=True,
            streaming=True,
            context_window=32_000,
            vision=False,
        ),
        credential_store=store,
    )
    store.delete("provider-legacy")

    assert not runtime_provider_status(store_path).configured


def test_insecure_alternative_keyring_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    alt_type = type(
        "PlaintextKeyring",
        (),
        {
            "priority": 1,
            "get_password": lambda self, service, username: None,
            "set_password": lambda self, service, username, password: None,
            "delete_password": lambda self, service, username: None,
        },
    )
    alt_type.__module__ = "keyrings.alt.file"
    monkeypatch.setattr(
        credential_store_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(get_keyring=lambda: alt_type()),
    )

    assert credential_store_module._safe_keyring_backend() is None


def test_known_operating_system_backend_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    native_type = type(
        "SecretServiceKeyring",
        (),
        {
            "priority": 5,
            "get_password": lambda self, service, username: None,
            "set_password": lambda self, service, username, password: None,
            "delete_password": lambda self, service, username: None,
        },
    )
    native_type.__module__ = "keyring.backends.SecretService"
    backend = native_type()
    monkeypatch.setattr(
        credential_store_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(get_keyring=lambda: backend),
    )

    assert credential_store_module._safe_keyring_backend() is backend


def test_chained_backend_is_rejected_when_it_contains_plaintext_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    safe_type = type("SecretServiceKeyring", (), {"priority": 5})
    safe_type.__module__ = "keyring.backends.SecretService"
    alt_type = type("PlaintextKeyring", (), {"priority": 1})
    alt_type.__module__ = "keyrings.alt.file"
    chain_type = type(
        "ChainerBackend",
        (),
        {"priority": 10, "backends": [safe_type(), alt_type()]},
    )
    chain_type.__module__ = "keyring.backend"
    monkeypatch.setattr(
        credential_store_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(get_keyring=lambda: chain_type()),
    )

    assert credential_store_module._safe_keyring_backend() is None
