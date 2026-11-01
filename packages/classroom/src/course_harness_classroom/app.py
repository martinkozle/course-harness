"""The Classroom HTTP application.

Routes:

- ``/api/admin/*``: the Administrator's session, attached courses, sync and Share Links.
- ``/l/<token>`` and ``/i/<token>``: exchange a Learner or Instructor Share Link token for a
  course cookie and redirect to ``/c/<course slug>``.
- ``/api/c/<course slug>`` and below: the served Release of one course, for its cookie holders.
- Everything else that is a ``GET``: the built Classroom interface, a single-page app.
"""

import hmac
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.staticfiles import StaticFiles

from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.repo_urls import InvalidRepoUrl, validate_repo_url
from course_harness_classroom.security import (
    ADMIN_COOKIE,
    ADMIN_SESSION_LIFETIME,
    COURSE_SESSION_LIFETIME,
    ExchangeLimiter,
    admin_fingerprint,
    admin_token_matches,
    clear_session_cookie,
    course_cookie,
    install_access_log_redaction,
    new_token,
    public_base_url,
    secure_cookies,
    security_middleware,
    set_session_cookie,
    token_hash,
)
from course_harness_classroom.store import (
    ROLES,
    Course,
    DuplicateCourse,
    Role,
    ShareLink,
    Store,
    SyncStatus,
)
from course_harness_classroom.sync import CourseNotFound, PeriodicSync, SyncService

logger = logging.getLogger(__name__)

COURSE_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
LINK_PREFIX: dict[Role, str] = {"learner": "l", "instructor": "i"}
ASSET_PATH = re.compile(
    r"(?:previews/[A-Za-z0-9_-]{1,64}/backgrounds/v[0-9]{1,6}/[a-z_]{1,32}\.png"
    r"|artifacts/presentation-[0-9a-f]{12}\.pptx)\Z"
)
FAILED_LOGIN_DELAY_SECONDS = 1.0
NO_ACCESS = "Open the link you were given to see this course."


def default_static_dir() -> Path:
    """The interface built from this checkout by ``bun run build``."""
    return Path(__file__).resolve().parents[4] / "frontend" / "classroom" / "dist"


# Requests and responses


class AdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=1_000)


class AdminSessionResponse(BaseModel):
    configured: bool
    authenticated: bool


class AttachCourseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repo_url: str = Field(min_length=1, max_length=2_048)
    slug: str | None = Field(default=None, pattern=COURSE_SLUG_PATTERN)


class ShareLinkResponse(BaseModel):
    role: Role
    created_at: str
    rotated_at: str | None


class ServedReleaseResponse(BaseModel):
    tag: str
    name: str
    published_at: str


class AdminCourseResponse(BaseModel):
    slug: str
    repo_url: str
    title: str | None
    release: ServedReleaseResponse | None
    last_sync_at: str | None
    sync_status: SyncStatus
    sync_message: str | None
    links: list[ShareLinkResponse]


class CreatedShareLinkResponse(BaseModel):
    """A new Share Link; its URL is shown only in this response."""

    role: Role
    url: str
    link: ShareLinkResponse


class LectureSummary(BaseModel):
    id: str
    title: str
    group: str | None
    slide_count: int
    has_quiz: bool
    download_url: str | None


class CourseResponse(BaseModel):
    slug: str
    title: str
    role: Role
    release: ServedReleaseResponse | None
    lectures: list[LectureSummary]


def _release_response(course: Course) -> ServedReleaseResponse | None:
    if course.release_slug is None or course.release_name is None:
        return None
    return ServedReleaseResponse(
        tag=f"course-release/{course.release_slug}",
        name=course.release_name,
        published_at=course.release_published_at or "",
    )


def _link_response(link: ShareLink) -> ShareLinkResponse:
    return ShareLinkResponse(role=link.role, created_at=link.created_at, rotated_at=link.rotated_at)


def default_course_slug(repo_url: str) -> str:
    name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:64].strip("-")
    return slug or "course"


def create_app(settings: ClassroomSettings | None = None, *, periodic_sync: bool = True) -> FastAPI:
    settings = settings or ClassroomSettings.from_environment()
    store = Store(settings.database_path)
    sync_service = SyncService(store, settings)
    periodic = PeriodicSync(sync_service, settings.fetch_interval_seconds if periodic_sync else 0)
    exchange_limiter = ExchangeLimiter()
    install_access_log_redaction()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        periodic.start()
        try:
            yield
        finally:
            periodic.stop()

    app = FastAPI(
        title="Course Harness Classroom",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.sync = sync_service
    app.middleware("http")(security_middleware(settings))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Administrator

    def require_admin(request: Request) -> None:
        session_id = request.cookies.get(ADMIN_COOKIE)
        session = store.session(token_hash(session_id)) if session_id else None
        expected = admin_fingerprint(settings)
        if (
            expected is None
            or session is None
            or session.kind != "admin"
            or not hmac.compare_digest(session.admin_fingerprint or "", expected)
        ):
            raise HTTPException(status_code=401, detail="Sign in as the Administrator.")

    Admin = Annotated[None, Depends(require_admin)]

    def admin_course(slug: str) -> Course:
        course = store.course_by_slug(slug)
        if course is None:
            raise HTTPException(status_code=404, detail="That course is not attached.")
        return course

    def course_response(course: Course) -> AdminCourseResponse:
        links = store.share_links(course.id)
        return AdminCourseResponse(
            slug=course.slug,
            repo_url=course.repo_url,
            title=course.title,
            release=_release_response(course),
            last_sync_at=course.last_sync_at,
            sync_status=course.sync_status,
            sync_message=course.sync_message,
            links=[_link_response(links[role]) for role in ROLES if role in links],
        )

    @app.get("/api/admin/session")
    def admin_session(request: Request) -> AdminSessionResponse:
        try:
            require_admin(request)
        except HTTPException:
            return AdminSessionResponse(
                configured=settings.admin_token is not None, authenticated=False
            )
        return AdminSessionResponse(configured=True, authenticated=True)

    @app.post("/api/admin/session")
    def admin_login(
        body: AdminLoginRequest, request: Request, response: Response
    ) -> AdminSessionResponse:
        if settings.admin_token is None:
            raise HTTPException(
                status_code=503,
                detail="This Classroom has no Administrator token. Set CLASSROOM_ADMIN_TOKEN.",
            )
        if not admin_token_matches(settings, body.token):
            # A sync route runs in a worker thread, so the delay slows guessing, not the server.
            time.sleep(FAILED_LOGIN_DELAY_SECONDS)
            raise HTTPException(status_code=401, detail="That token is not correct.")
        session_id = new_token()
        store.add_session(
            token_hash(session_id),
            kind="admin",
            lifetime=ADMIN_SESSION_LIFETIME,
            admin_fingerprint=admin_fingerprint(settings),
        )
        set_session_cookie(
            response,
            name=ADMIN_COOKIE,
            value=session_id,
            lifetime=ADMIN_SESSION_LIFETIME,
            secure=secure_cookies(settings),
        )
        return AdminSessionResponse(configured=True, authenticated=True)

    @app.delete("/api/admin/session", status_code=204)
    def admin_logout(request: Request) -> Response:
        session_id = request.cookies.get(ADMIN_COOKIE)
        if session_id:
            store.delete_session(token_hash(session_id))
        response = Response(status_code=204)
        clear_session_cookie(response, name=ADMIN_COOKIE, secure=secure_cookies(settings))
        return response

    @app.get("/api/admin/courses")
    def list_courses(_admin: Admin) -> list[AdminCourseResponse]:
        return [course_response(course) for course in store.courses()]

    @app.post("/api/admin/courses", status_code=201)
    def attach_course(body: AttachCourseRequest, _admin: Admin) -> AdminCourseResponse:
        try:
            repo_url = validate_repo_url(body.repo_url, allow_local=settings.allow_local_repos)
        except InvalidRepoUrl as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        slug = body.slug or default_course_slug(repo_url)
        try:
            course = store.add_course(slug=slug, repo_url=repo_url)
        except DuplicateCourse as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return course_response(sync_service.sync(course.id))

    @app.delete("/api/admin/courses/{slug}", status_code=204)
    def detach_course(slug: str, _admin: Admin) -> Response:
        course = admin_course(slug)
        store.delete_course(course.id)
        sync_service.remove_course_data(course)
        return Response(status_code=204)

    @app.post("/api/admin/courses/{slug}/sync")
    def sync_course(slug: str, _admin: Admin) -> AdminCourseResponse:
        course = admin_course(slug)
        try:
            return course_response(sync_service.sync(course.id))
        except CourseNotFound as error:
            raise HTTPException(status_code=404, detail="That course is not attached.") from error

    @app.post("/api/admin/courses/{slug}/links/{role}")
    def create_share_link(
        slug: str, role: Role, request: Request, _admin: Admin
    ) -> CreatedShareLinkResponse:
        """Create a course's Share Link for a role, or rotate it: the previous one stops working."""
        course = admin_course(slug)
        token = new_token()
        store.set_share_link(course.id, role, token_hash(token))
        link = store.share_links(course.id)[role]
        url = f"{public_base_url(settings, request)}/{LINK_PREFIX[role]}/{token}"
        return CreatedShareLinkResponse(role=role, url=url, link=_link_response(link))

    # Share Links

    def exchange(token: str, role: Role, request: Request) -> Response:
        client = request.client.host if request.client else "unknown"
        if not exchange_limiter.allow(client):
            return PlainTextResponse(
                "Too many links were opened from your address. Try again in a minute.",
                status_code=429,
                headers={"Cache-Control": "no-store", "Retry-After": "60"},
            )
        session_id = new_token()
        course = (
            store.exchange_share_link(
                token_hash(token),
                role,
                session_hash=token_hash(session_id),
                lifetime=COURSE_SESSION_LIFETIME,
            )
            if len(token) <= 200
            else None
        )
        if course is None:
            response: Response = RedirectResponse("/link-not-valid", status_code=303)
        else:
            response = RedirectResponse(f"/c/{course.slug}", status_code=303)
            set_session_cookie(
                response,
                name=course_cookie(course.slug),
                value=session_id,
                lifetime=COURSE_SESSION_LIFETIME,
                secure=secure_cookies(settings),
            )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/l/{token}")
    def learner_link(token: str, request: Request) -> Response:
        return exchange(token, "learner", request)

    @app.get("/i/{token}")
    def instructor_link(token: str, request: Request) -> Response:
        return exchange(token, "instructor", request)

    # Courses, for Share Link holders

    def course_access(slug: str, request: Request) -> tuple[Course, Role]:
        course = store.course_by_slug(slug)
        session_id = request.cookies.get(course_cookie(slug)) if course else None
        session = store.session(token_hash(session_id)) if session_id else None
        if (
            course is None
            or session is None
            or session.kind != "course"
            or session.course_id != course.id
            or session.role is None
        ):
            raise HTTPException(status_code=401, detail=NO_ACCESS)
        return course, session.role

    CourseAccess = Annotated[tuple[Course, Role], Depends(course_access)]

    @app.get("/api/c/{slug}")
    def course_summary(access: CourseAccess) -> CourseResponse:
        course, role = access
        snapshot = sync_service.snapshot(course)
        directory = sync_service.snapshot_dir(course)
        lectures = []
        if snapshot is not None and directory is not None:
            for lecture in snapshot.lectures:
                download = (
                    f"/api/c/{course.slug}/assets/{directory.name}/artifacts/{lecture.artifact.id}.pptx"
                    if lecture.artifact
                    else None
                )
                lectures.append(
                    LectureSummary(
                        id=lecture.id,
                        title=lecture.title,
                        group=lecture.group,
                        slide_count=len(lecture.presentation.slides) if lecture.presentation else 0,
                        has_quiz=lecture.quiz is not None,
                        download_url=download,
                    )
                )
        return CourseResponse(
            slug=course.slug,
            title=snapshot.title if snapshot else course.title or course.slug,
            role=role,
            release=_release_response(course) if snapshot else None,
            lectures=lectures,
        )

    @app.get("/api/c/{slug}/assets/{name}/{path:path}")
    def course_asset(name: str, path: str, access: CourseAccess) -> FileResponse:
        """A rendered background or the Release PPTX of the Release the course serves now, or of
        the one it served before, so a page opened before a switch keeps working."""
        course, _role = access
        found = sync_service.snapshot_named(course, name) if ASSET_PATH.fullmatch(path) else None
        if found is None:
            raise HTTPException(status_code=404, detail="Not found")
        directory, snapshot = found
        target = (directory / path).resolve()
        if directory.resolve() not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="Not found")
        headers = {"Cache-Control": "private, max-age=86400, immutable"}
        if path.startswith("artifacts/"):
            artifact_id = Path(path).stem
            title = next(
                (
                    lecture.title
                    for lecture in snapshot.lectures
                    if lecture.artifact and lecture.artifact.id == artifact_id
                ),
                artifact_id,
            )
            filename = f"{default_course_slug(title)}.pptx"
            return FileResponse(
                target,
                media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                filename=filename,
                headers=headers,
            )
        return FileResponse(target, media_type="image/png", headers=headers)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def unknown_api(path: str) -> None:
        raise HTTPException(status_code=404, detail="Not found")

    # The interface

    static_dir = settings.static_dir or default_static_dir()
    index = static_dir / "index.html"
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def interface(path: str) -> Response:
        candidate = (static_dir / path).resolve()
        if (
            path
            and static_dir.resolve() in candidate.parents
            and candidate.is_file()
            and candidate.name != "index.html"
        ):
            return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(
                status_code=404,
                detail="The Classroom interface is not built. Run `bun run build`.",
            )
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app
