from fastapi.testclient import TestClient

from course_harness_classroom.app import create_app


def test_healthz_reports_ok() -> None:
    response = TestClient(create_app()).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
