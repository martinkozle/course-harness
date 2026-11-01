"""The Classroom HTTP application."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Course Harness Classroom")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
