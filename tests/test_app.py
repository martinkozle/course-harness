from pathlib import Path

import httpx2
import pytest

from course_harness.app import create_app


@pytest.mark.anyio
async def test_application_reports_health_and_active_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "statistical-learning"
    workspace.mkdir()

    transport = httpx2.ASGITransport(app=create_app(workspace))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        health_response = await client.get("/api/health")
        workspace_response = await client.get("/api/workspace")

    assert health_response.status_code == 200
    assert health_response.json() == {"status": "ok"}
    assert workspace_response.status_code == 200
    assert workspace_response.json() == {
        "name": "statistical-learning",
        "path": str(workspace),
    }
