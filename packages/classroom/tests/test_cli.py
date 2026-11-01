"""`course-classroom serve`: public URL requirement and trusted proxies."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from course_harness_classroom import cli

TOKEN = "classroom-admin-token-for-tests-0123456789"


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda _app, **keywords: calls.append(keywords))
    monkeypatch.setenv("CLASSROOM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLASSROOM_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("CLASSROOM_FETCH_INTERVAL", "0")
    for name in ["CLASSROOM_PUBLIC_URL", "CLASSROOM_TRUSTED_PROXIES"]:
        monkeypatch.delenv(name, raising=False)
    return calls


def test_serving_off_loopback_requires_a_public_url(
    runs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    result = CliRunner().invoke(cli.cli, ["serve", "--host", "0.0.0.0"])

    assert result.exit_code == 2
    assert "CLASSROOM_PUBLIC_URL" in result.output
    assert runs == []

    monkeypatch.setenv("CLASSROOM_PUBLIC_URL", "https://classroom.example.org")
    assert CliRunner().invoke(cli.cli, ["serve", "--host", "0.0.0.0"]).exit_code == 0
    assert runs[0]["host"] == "0.0.0.0"


def test_forwarded_headers_are_trusted_only_from_configured_proxies(
    runs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert CliRunner().invoke(cli.cli, ["serve"]).exit_code == 0
    assert runs[-1]["proxy_headers"] is False

    monkeypatch.setenv("CLASSROOM_TRUSTED_PROXIES", "10.0.0.2,10.0.0.3")
    assert CliRunner().invoke(cli.cli, ["serve"]).exit_code == 0
    assert runs[-1]["proxy_headers"] is True
    assert runs[-1]["forwarded_allow_ips"] == "10.0.0.2,10.0.0.3"
