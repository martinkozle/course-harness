from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from course_harness_classroom.config import ClassroomSettings, ConfigurationError


def test_healthz_reports_ok_with_security_headers(make_client: Any) -> None:
    response = make_client().get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    policy = response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "script-src 'self'" in policy


def test_the_interface_is_served_for_app_routes(
    make_client: Any, settings: ClassroomSettings, tmp_path: Path
) -> None:
    interface = tmp_path / "dist"
    (interface / "assets").mkdir(parents=True)
    (interface / "index.html").write_text("<!doctype html><title>Classroom</title>")
    (interface / "assets" / "app.js").write_text("console.log('classroom')")
    client = make_client(replace(settings, static_dir=interface))

    for path in ["/", "/admin", "/c/causal", "/link-not-valid"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "<title>Classroom</title>" in response.text
        assert response.headers["referrer-policy"] == "no-referrer"
    assert client.get("/assets/app.js").text == "console.log('classroom')"
    assert client.get("/api/unknown").status_code == 404
    (tmp_path / "secret.txt").write_text("not for browsers")
    for path in ["/..%2fsecret.txt", "/%2e%2e/secret.txt", "/assets/..%2f..%2fsecret.txt"]:
        assert "not for browsers" not in client.get(path).text


def test_settings_come_from_the_environment(tmp_path: Path) -> None:
    settings = ClassroomSettings.from_environment(
        {
            "CLASSROOM_DATA_DIR": str(tmp_path),
            "CLASSROOM_ADMIN_TOKEN": "a" * 32,
            "CLASSROOM_PUBLIC_URL": "https://classroom.example.org/",
            "CLASSROOM_FETCH_INTERVAL": "60",
        }
    )

    assert settings.data_dir == tmp_path
    assert settings.public_url == "https://classroom.example.org"
    assert settings.fetch_interval_seconds == 60
    assert settings.allow_local_repos is False


def test_a_short_admin_token_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        ClassroomSettings(data_dir=tmp_path, admin_token="short")
