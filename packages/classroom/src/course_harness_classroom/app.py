"""The Classroom HTTP application.

Routes:

- ``/api/admin/*``: the Administrator's session, attached courses, sync and Share Links.
- ``/l/<token>`` and ``/i/<token>``: exchange a Learner or Instructor Share Link token for a
  course cookie and redirect to ``/c/<course slug>``.
- ``/api/c/<course slug>`` and below: the served Release of one course, for its cookie holders
  and the Administrator: its Lectures, its Quizzes without answer keys, Answers and a student's
  progress.
- ``/api/c/<course slug>/insights``, ``/answers.csv``, ``/sync`` and ``/links``: Instructor
  Insights, the Answers export, "Sync now" and the course's Share Links, for Instructors only.
  An Administrator session is an Instructor of every course.
- Everything else that is a ``GET``: the built Classroom interface, a single-page app.
"""

import hmac
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import unquote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError
from starlette.staticfiles import StaticFiles

from course_harness.core.quiz import option_by_id
from course_harness.core.slide_preview import PreviewSlot
from course_harness_classroom.config import ClassroomSettings
from course_harness_classroom.insights import (
    Insights,
    ReleaseQuizzes,
    answers_csv,
    build_insights,
)
from course_harness_classroom.learning import (
    CourseSource,
    LearnerQuiz,
    OptionFeedback,
    QuestionProgress,
    course_sources,
    feedback,
    find_question,
    is_correct,
    learner_quizzes,
    linked_citations,
    progress,
    source_locators,
)
from course_harness_classroom.repo_urls import InvalidRepoUrl, validate_repo_url
from course_harness_classroom.security import (
    ADMIN_COOKIE,
    ADMIN_SESSION_LIFETIME,
    COURSE_SESSION_LIFETIME,
    ExchangeLimiter,
    RateLimiter,
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
from course_harness_classroom.served import ServedLecture, ServedSnapshot
from course_harness_classroom.store import (
    ROLES,
    Course,
    DuplicateCourse,
    NewAnswer,
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
    r"|artifacts/presentation-[0-9a-f]{12}\.pptx"
    r"|images/presentation-[0-9a-f]{12}/slide-[0-9a-f]{12}\.(?:png|jpg|gif|webp))\Z"
)
IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
FAILED_LOGIN_DELAY_SECONDS = 1.0
NO_ACCESS = "Open the link you were given to see this course."
NO_RELEASE = "This course has no Release yet."
# Names and student IDs end up in Instructor tables and CSV files, so they must be plain text:
# no C0 or C1 controls, line or paragraph separators, or bidirectional overrides and isolates.
UNPRINTABLE = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]")
STUDENT_HEADER = "X-Classroom-Student"
ONLY_INSTRUCTORS = "Only the course's Instructors can see this."
RELEASE_TAG_PATTERN = r"^course-release/[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
MAX_ANSWER_BYTES = 4_096
# Answers per minute: one network address may be a whole classroom behind one router.
ANSWERS_PER_ADDRESS = 240
ANSWERS_PER_STUDENT = 30


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
    # The full link, or None for a link created before tokens were kept.
    url: str | None
    needs_rotation: bool


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
    """A new or rotated Share Link."""

    role: Role
    url: str
    link: ShareLinkResponse


class LectureSummary(BaseModel):
    id: str
    title: str
    group: str | None
    slide_count: int
    has_quiz: bool
    question_count: int
    download_url: str | None


class LectureLink(BaseModel):
    id: str
    title: str


class LearnerSlide(BaseModel):
    # A canonical Slide without its authoring-only ``purpose``.
    slide: dict[str, Any]
    slots: dict[str, PreviewSlot] | None
    background_url: str | None
    # The picture of an image Slide, as embedded in the Release PPTX.
    image_url: str | None = None


class LectureResponse(BaseModel):
    id: str
    title: str
    group: str | None
    number: int
    lecture_count: int
    previous: LectureLink | None
    next: LectureLink | None
    aspect_ratio: float
    slides: list[LearnerSlide]
    download_url: str | None
    question_count: int


def _plain_text(value: str) -> str:
    if UNPRINTABLE.search(value):
        raise ValueError("must be plain text without control or direction characters")
    return value


PlainText = Annotated[str, AfterValidator(_plain_text)]


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    lecture_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)
    learner_name: PlainText = Field(min_length=1, max_length=100)
    student_id: PlainText = Field(min_length=1, max_length=64)


class AnswerResponse(OptionFeedback):
    # False when the Question was already answered correctly, so nothing was recorded.
    recorded: bool


class ProgressResponse(BaseModel):
    student_id: str
    questions: list[QuestionProgress]


class CourseSyncResponse(BaseModel):
    """Where a course's sync stands, for an Instructor's "Sync now"."""

    syncing: bool
    last_sync_at: str | None
    sync_status: SyncStatus
    sync_message: str | None
    release: ServedReleaseResponse | None


class CourseLinksResponse(BaseModel):
    """A course's Share Links for its Instructors, and which roles' links they may rotate."""

    links: list[ShareLinkResponse]
    can_rotate: list[Role]


class CourseResponse(BaseModel):
    slug: str
    title: str
    # What the server lets this visitor do; viewing as a Learner is a browser preference only.
    role: Role
    administrator: bool
    release: ServedReleaseResponse | None
    lectures: list[LectureSummary]
    sources: list[CourseSource]


@dataclass(frozen=True)
class CourseVisitor:
    """A visitor's access to one course, as the server decides it."""

    course: Course
    role: Role
    administrator: bool


def _release_response(course: Course) -> ServedReleaseResponse | None:
    if course.release_slug is None or course.release_name is None:
        return None
    return ServedReleaseResponse(
        tag=f"course-release/{course.release_slug}",
        name=course.release_name,
        published_at=course.release_published_at or "",
    )


def _link_response(link: ShareLink, base_url: str) -> ShareLinkResponse:
    return ShareLinkResponse(
        role=link.role,
        created_at=link.created_at,
        rotated_at=link.rotated_at,
        url=f"{base_url}/{LINK_PREFIX[link.role]}/{link.token}" if link.token else None,
        needs_rotation=link.token is None,
    )


def _visible_slides(lecture: ServedLecture) -> list[Any]:
    if lecture.presentation is None:
        return []
    return [slide for slide in lecture.presentation.slides if not slide.archived]


def _lecture_link(lecture: ServedLecture) -> LectureLink:
    return LectureLink(id=lecture.id, title=lecture.title)


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
    answer_limits = {
        "address": RateLimiter(limit=ANSWERS_PER_ADDRESS),
        "student": RateLimiter(limit=ANSWERS_PER_STUDENT),
    }

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
    app.state.answer_limits = answer_limits
    app.middleware("http")(security_middleware(settings))

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # Administrator

    def is_admin(request: Request) -> bool:
        """Whether the request carries a live Administrator session for the current token."""
        session_id = request.cookies.get(ADMIN_COOKIE)
        session = store.session(token_hash(session_id)) if session_id else None
        expected = admin_fingerprint(settings)
        return (
            expected is not None
            and session is not None
            and session.kind == "admin"
            and hmac.compare_digest(session.admin_fingerprint or "", expected)
        )

    def require_admin(request: Request) -> None:
        if not is_admin(request):
            raise HTTPException(status_code=401, detail="Sign in as the Administrator.")

    Admin = Annotated[None, Depends(require_admin)]

    def admin_course(slug: str) -> Course:
        course = store.course_by_slug(slug)
        if course is None:
            raise HTTPException(status_code=404, detail="That course is not attached.")
        return course

    def link_responses(course: Course, request: Request) -> list[ShareLinkResponse]:
        links = store.share_links(course.id)
        base_url = public_base_url(settings, request)
        return [_link_response(links[role], base_url) for role in ROLES if role in links]

    def rotate_link(course: Course, role: Role, request: Request) -> ShareLinkResponse:
        """Create a course's Share Link for a role, or rotate it: the previous link and every
        session opened with it stop working."""
        store.set_share_link(course.id, role, new_token())
        link = store.share_links(course.id)[role]
        return _link_response(link, public_base_url(settings, request))

    def course_response(course: Course, request: Request) -> AdminCourseResponse:
        return AdminCourseResponse(
            slug=course.slug,
            repo_url=course.repo_url,
            title=course.title,
            release=_release_response(course),
            last_sync_at=course.last_sync_at,
            sync_status=course.sync_status,
            sync_message=course.sync_message,
            links=link_responses(course, request),
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
    def list_courses(request: Request, _admin: Admin) -> list[AdminCourseResponse]:
        return [course_response(course, request) for course in store.courses()]

    @app.post("/api/admin/courses", status_code=201)
    def attach_course(
        body: AttachCourseRequest, request: Request, _admin: Admin
    ) -> AdminCourseResponse:
        try:
            repo_url = validate_repo_url(body.repo_url, allow_local=settings.allow_local_repos)
        except InvalidRepoUrl as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        slug = body.slug or default_course_slug(repo_url)
        try:
            course = store.add_course(slug=slug, repo_url=repo_url)
        except DuplicateCourse as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return course_response(sync_service.sync(course.id), request)

    @app.delete("/api/admin/courses/{slug}", status_code=204)
    def detach_course(slug: str, _admin: Admin) -> Response:
        course = admin_course(slug)
        store.delete_course(course.id)
        sync_service.remove_course_data(course)
        return Response(status_code=204)

    @app.post("/api/admin/courses/{slug}/sync")
    def sync_course(slug: str, request: Request, _admin: Admin) -> AdminCourseResponse:
        course = admin_course(slug)
        try:
            return course_response(sync_service.sync(course.id), request)
        except CourseNotFound as error:
            raise HTTPException(status_code=404, detail="That course is not attached.") from error

    @app.post("/api/admin/courses/{slug}/links/{role}")
    def create_share_link(
        slug: str, role: Role, request: Request, _admin: Admin
    ) -> CreatedShareLinkResponse:
        """Create a course's Share Link for a role, or rotate it: the previous one stops working."""
        link = rotate_link(admin_course(slug), role, request)
        assert link.url is not None
        return CreatedShareLinkResponse(role=role, url=link.url, link=link)

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

    def course_access(slug: str, request: Request) -> CourseVisitor:
        """Who may see a course: a Share Link session, or an Administrator session, which is an
        Instructor of every course. A browser viewing the course as a Learner never changes
        this."""
        course = store.course_by_slug(slug)
        if course is not None and is_admin(request):
            return CourseVisitor(course=course, role="instructor", administrator=True)
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
        return CourseVisitor(course=course, role=session.role, administrator=False)

    CourseAccess = Annotated[CourseVisitor, Depends(course_access)]

    def download_url(course: Course, directory: Path, lecture: ServedLecture) -> str | None:
        if lecture.artifact is None:
            return None
        return f"/api/c/{course.slug}/assets/{directory.name}/artifacts/{lecture.artifact.id}.pptx"

    def served(course: Course) -> tuple[ServedSnapshot, Path]:
        snapshot = sync_service.snapshot(course)
        directory = sync_service.snapshot_dir(course)
        if snapshot is None or directory is None:
            raise HTTPException(status_code=409, detail=NO_RELEASE)
        return snapshot, directory

    @app.get("/api/c/{slug}")
    def course_summary(access: CourseAccess) -> CourseResponse:
        course, role = access.course, access.role
        snapshot = sync_service.snapshot(course)
        directory = sync_service.snapshot_dir(course)
        lectures = []
        if snapshot is not None and directory is not None:
            for lecture in snapshot.lectures:
                lectures.append(
                    LectureSummary(
                        id=lecture.id,
                        title=lecture.title,
                        group=lecture.group,
                        slide_count=len(_visible_slides(lecture)),
                        has_quiz=lecture.quiz is not None,
                        question_count=len(lecture.quiz.questions) if lecture.quiz else 0,
                        download_url=download_url(course, directory, lecture),
                    )
                )
        return CourseResponse(
            slug=course.slug,
            title=snapshot.title if snapshot else course.title or course.slug,
            role=role,
            administrator=access.administrator,
            release=_release_response(course) if snapshot else None,
            lectures=lectures,
            sources=course_sources(snapshot) if snapshot is not None else [],
        )

    @app.get("/api/c/{slug}/lectures/{lecture_id}")
    def lecture_detail(lecture_id: str, access: CourseAccess) -> LectureResponse:
        """A Lecture's Slides with their preview geometry; its Quiz comes from ``/quizzes``."""
        course = access.course
        snapshot, directory = served(course)
        lectures = snapshot.lectures
        index = next(
            (number for number, item in enumerate(lectures) if item.id == lecture_id), None
        )
        if index is None:
            raise HTTPException(status_code=404, detail="This course has no such Lecture.")
        lecture = lectures[index]
        previews = (
            {slide.slide_id: slide for slide in lecture.preview.slides} if lecture.preview else {}
        )
        locators = source_locators(snapshot)
        slides = []
        for slide in _visible_slides(lecture):
            preview = previews.get(slide.id)
            linked = slide.model_copy(
                update={"citations": linked_citations(slide.citations, locators)}
            )
            slides.append(
                LearnerSlide(
                    slide=linked.model_dump(mode="json", exclude={"purpose"}),
                    slots=preview.slots if preview else None,
                    background_url=preview.background_url if preview else None,
                    image_url=(
                        f"/api/c/{course.slug}/assets/{directory.name}/images/"
                        f"{lecture.artifact.id}/{lecture.slide_images[slide.id]}"
                        if lecture.artifact and slide.id in lecture.slide_images
                        else None
                    ),
                )
            )
        return LectureResponse(
            id=lecture.id,
            title=lecture.title,
            group=lecture.group,
            number=index + 1,
            lecture_count=len(lectures),
            previous=_lecture_link(lectures[index - 1]) if index > 0 else None,
            next=_lecture_link(lectures[index + 1]) if index + 1 < len(lectures) else None,
            aspect_ratio=(
                lecture.preview.slide_width / lecture.preview.slide_height
                if lecture.preview and lecture.preview.slide_height
                else 16 / 9
            ),
            slides=slides,
            download_url=download_url(course, directory, lecture),
            question_count=len(lecture.quiz.questions) if lecture.quiz else 0,
        )

    def student_header(
        student: Annotated[str | None, Header(alias=STUDENT_HEADER, max_length=400)] = None,
    ) -> str | None:
        """The student ID a page sends in a header, percent-encoded, so that it stays out of URLs
        and access logs."""
        if student is None:
            return None
        try:
            value = unquote(student, errors="strict").strip() if student.isascii() else ""
        except UnicodeDecodeError:
            value = ""
        if not value or len(value) > 64 or UNPRINTABLE.search(value):
            raise HTTPException(status_code=422, detail="That student ID is not valid.")
        return value

    StudentHeader = Annotated[str | None, Depends(student_header)]

    @app.get("/api/c/{slug}/quizzes")
    def quizzes(access: CourseAccess, student: StudentHeader) -> list[LearnerQuiz]:
        """Every Quiz without its answer key, its Options shuffled for this student."""
        course = access.course
        snapshot, _directory = served(course)
        return learner_quizzes(snapshot, student_id=student or "")

    async def answer_body(request: Request) -> AnswerRequest:
        """The Answer in the request body, refused early when one network address sends too many
        or the body is too large."""
        client = request.client.host if request.client else "unknown"
        if not answer_limits["address"].allow(client):
            raise HTTPException(status_code=429, detail="Too many answers. Wait a minute.")
        declared = request.headers.get("content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_ANSWER_BYTES):
            raise HTTPException(status_code=413, detail="That answer is too large.")
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_ANSWER_BYTES:
                raise HTTPException(status_code=413, detail="That answer is too large.")
        try:
            return AnswerRequest.model_validate_json(bytes(body))
        except ValidationError as error:
            raise RequestValidationError(
                error.errors(include_url=False, include_context=False, include_input=False)
            ) from error

    @app.post("/api/c/{slug}/answers")
    def answer(
        access: CourseAccess, body: Annotated[AnswerRequest, Depends(answer_body)]
    ) -> AnswerResponse:
        """Record a picked Option and tell the student about it.

        Course access is checked before the rate limits and the body. The Answer stores the
        Release tag, the IDs and the attempt number, never whether it was correct (ADR 0018).
        Picking an Option again, or any Option once the Question is answered correctly, is
        answered but not recorded.
        """
        course, role = access.course, access.role
        snapshot, _directory = served(course)
        found = find_question(snapshot, body.lecture_id, body.question_id)
        if found is None:
            raise HTTPException(status_code=422, detail="That question is not part of this course.")
        lecture, question = found
        option = option_by_id(question, body.option_id)
        if option is None:
            raise HTTPException(status_code=422, detail="That option is not part of the question.")
        if not answer_limits["student"].allow(f"{course.id}:{body.student_id}"):
            raise HTTPException(status_code=429, detail="Too many answers. Wait a minute.")
        recorded = store.record_answer(
            NewAnswer(
                course_id=course.id,
                release_tag=snapshot.manifest.tag,
                lecture_id=lecture.id,
                question_id=question.id,
                option_id=option.id,
                learner_name=body.learner_name,
                student_id=body.student_id,
                role=role,
            ),
            settled=lambda earlier: any(is_correct(question, picked) for picked in earlier),
        )
        return AnswerResponse(
            **feedback(question, option, source_locators(snapshot)).model_dump(),
            recorded=recorded is not None,
        )

    @app.get("/api/c/{slug}/progress")
    def student_progress(access: CourseAccess, student: StudentHeader) -> ProgressResponse:
        """A student's state on each Question, from the Answers given with their student ID
        under the same role: an Instructor's tries never show up as a Learner's progress."""
        course, role = access.course, access.role
        if student is None:
            raise HTTPException(
                status_code=422, detail=f"Send your student ID in {STUDENT_HEADER}."
            )
        snapshot, _directory = served(course)
        return ProgressResponse(
            student_id=student,
            questions=progress(snapshot, store.answers(course.id, student=(role, student))),
        )

    # Instructor Insights

    def instructor_access(access: CourseAccess) -> CourseVisitor:
        if access.role != "instructor":
            raise HTTPException(status_code=403, detail=ONLY_INSTRUCTORS)
        return access

    InstructorAccess = Annotated[CourseVisitor, Depends(instructor_access)]

    def instructor_course(access: InstructorAccess) -> Course:
        return access.course

    InstructorCourse = Annotated[Course, Depends(instructor_course)]
    ReleaseTag = Annotated[str | None, Query(max_length=79, pattern=RELEASE_TAG_PATTERN)]

    def grading_release(course: Course, release: str | None) -> tuple[ReleaseQuizzes, str]:
        """The Quizzes to grade against, and the served Release's tag."""
        snapshot, _directory = served(course)
        served_tag = snapshot.manifest.tag
        quizzes = sync_service.release_quizzes(course, release or served_tag)
        if quizzes is None:
            raise HTTPException(
                status_code=404,
                detail="That Release is not in this course's repository or cannot be read.",
            )
        return quizzes, served_tag

    @app.get("/api/c/{slug}/insights")
    def insights(
        course: InstructorCourse,
        release: ReleaseTag = None,
        include_instructors: bool = False,
    ) -> Insights:
        """Where Learners get stuck, graded against the served Release or the one named."""
        grading, served_tag = grading_release(course, release)
        answers = store.answers(course.id)
        tags = dict.fromkeys(answer.release_tag for answer in answers)
        return build_insights(
            answers,
            grading,
            {tag: sync_service.release_quizzes(course, tag) for tag in tags},
            served_tag=served_tag,
            include_instructors=include_instructors,
        )

    @app.get("/api/c/{slug}/answers.csv")
    def answers_export(
        course: InstructorCourse,
        release: ReleaseTag = None,
        include_instructors: bool = False,
    ) -> Response:
        """Every Answer with its correctness and score derived against the grading Release."""
        grading, _served_tag = grading_release(course, release)
        content = answers_csv(
            store.answers(course.id), grading, include_instructors=include_instructors
        )
        name = f"{course.slug}-answers-{grading.tag.removeprefix('course-release/')}.csv"
        return Response(
            content.encode("utf-8"),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    def sync_state(course: Course) -> CourseSyncResponse:
        current = store.course_by_id(course.id) or course
        return CourseSyncResponse(
            syncing=sync_service.is_syncing(course.id),
            last_sync_at=current.last_sync_at,
            sync_status=current.sync_status,
            sync_message=current.sync_message,
            release=_release_response(current),
        )

    @app.get("/api/c/{slug}/sync")
    def course_sync_status(course: InstructorCourse) -> CourseSyncResponse:
        return sync_state(course)

    @app.post("/api/c/{slug}/sync", status_code=202)
    def course_sync_now(course: InstructorCourse) -> CourseSyncResponse:
        """Start syncing the course in the background; poll ``GET`` for the outcome. A sync that
        started less than the cooldown ago is refused with 429."""
        started = sync_service.start_background_sync(course.id)
        if started.outcome == "cooling-down":
            seconds = started.retry_after_seconds
            raise HTTPException(
                status_code=429,
                detail=(
                    "This course was synced moments ago. "
                    f"Try again in {seconds} second{'s' if seconds != 1 else ''}."
                ),
                headers={"Retry-After": str(seconds)},
            )
        return sync_state(course)

    def rotatable_roles(access: CourseVisitor) -> list[Role]:
        """Instructors rotate the Learner link; only the Administrator rotates the Instructor
        link, so one Instructor can never lock the others out."""
        return list(ROLES) if access.administrator else ["learner"]

    @app.get("/api/c/{slug}/links")
    def course_links(access: InstructorAccess, request: Request) -> CourseLinksResponse:
        return CourseLinksResponse(
            links=link_responses(access.course, request), can_rotate=rotatable_roles(access)
        )

    @app.post("/api/c/{slug}/links/{role}")
    def rotate_course_link(
        role: Role, access: InstructorAccess, request: Request
    ) -> ShareLinkResponse:
        """Create or rotate a course's Share Link: the previous link and its sessions end."""
        if role not in rotatable_roles(access):
            raise HTTPException(
                status_code=403, detail="Only the Administrator can rotate the Instructor link."
            )
        return rotate_link(access.course, role, request)

    @app.get("/api/c/{slug}/assets/{name}/{path:path}")
    def course_asset(name: str, path: str, access: CourseAccess) -> FileResponse:
        """A rendered background, an image Slide's picture or the Release PPTX of the Release the
        course serves now, or of the one it served before, so a page opened before a switch keeps
        working."""
        course = access.course
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
        return FileResponse(
            target, media_type=IMAGE_MEDIA_TYPES.get(target.suffix, "image/png"), headers=headers
        )

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
