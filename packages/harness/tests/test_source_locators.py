"""Source Locators: public metadata recorded at admission, backfilled on open, and checked."""

import asyncio
import subprocess
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx2
import pytest
from pydantic import ValidationError

from course_harness import release_service
from course_harness.app import create_app
from course_harness.core import releases
from course_harness.core.course_plan import CoursePlan, Lecture, write_course_plan
from course_harness.core.presentation import (
    Presentation,
    SlideCitation,
    TitleSlide,
    write_presentation,
)
from course_harness.core.release_validation import ReleaseSelection, Waiver, validate_release
from course_harness.core.releases import CourseRelease
from course_harness.core.sources import (
    Source,
    SourceLocator,
    SourcesIndex,
    citation_link,
    public_locator,
    read_sources_index,
    serialize_sources_index,
    with_missing_locators,
    write_sources_index,
)
from course_harness.release_service import (
    PublishReleaseRequest,
    ReleaseValidationError,
    publish_release,
)
from course_harness.sources import backfill_source_locators
from course_harness.workspace_history import (
    RevisionCreateRequest,
    create_revision,
    read_current_state,
    record_app_authored_entries,
    record_app_authored_state,
)

SEARCH_FIXTURES = Path(__file__).with_name("fixtures") / "search"
SIGNED_URL = (
    "https://example.com/papers/attention.md?page=2&X-Amz-Signature=abc123"
    "&access_token=secret&apiKey=k&Expires=99#section-3"
)


async def _public_host_resolver(_hostname: str, _port: int) -> set[str]:
    return {"8.8.8.8"}


def _app(workspace: Path, data_dir: Path, cache_dir: Path):
    return create_app(
        workspace,
        library_data_path=data_dir,
        library_cache_path=cache_dir,
        remote_host_resolver=_public_host_resolver,
    )


async def _wait_for_ready(client: httpx2.AsyncClient, resource_id: str) -> None:
    async with asyncio.timeout(5):
        while (await client.get(f"/api/resources/{resource_id}")).json()["status"] != "ready":
            await asyncio.sleep(0.01)


async def _admit_remote_and_upload(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A Workspace with one remote Source (from a signed URL) and one uploaded Source."""
    workspace = tmp_path / "course"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        with patch(
            "course_harness.library.fetch_remote_resource",
            AsyncMock(return_value=(b"# Attention\n\nIs all you need.", "text/markdown")),
        ):
            remote = await client.post("/api/resources/remote", json={"url": SIGNED_URL})
            assert remote.status_code == 201, remote.text
            await _wait_for_ready(client, remote.json()["resource_id"])
        upload = await client.post(
            "/api/resources/upload",
            files={
                "file": (
                    "chapter_causal.md",
                    (SEARCH_FIXTURES / "chapter_causal.md").read_bytes(),
                    "text/markdown",
                )
            },
        )
        for resource_id, label in (
            (remote.json()["resource_id"], "Attention"),
            (upload.json()["resource_id"], "Lecture notes"),
        ):
            admitted = await client.post(
                "/api/sources", json={"resource_id": resource_id, "label": label}
            )
            assert admitted.status_code == 201, admitted.text
    write_course_plan(
        workspace,
        CoursePlan(
            id="course-aaaaaaaaaaaa",
            title="Transformers",
            audience="Engineers",
            lectures=[Lecture(id="lecture-aaaaaaaaaaaa", title="Attention")],
        ),
    )
    record_app_authored_entries(
        workspace, {"course.yaml": (workspace / "course.yaml").read_bytes()}
    )
    assert read_current_state(workspace).validation.valid
    return workspace, data_dir, cache_dir


def _sources_by_label(workspace: Path) -> dict[str, Source]:
    index = read_sources_index(workspace)
    assert index is not None
    return {source.label: source for source in index.sources}


def _record_as_app_authored(workspace: Path, index: SourcesIndex) -> None:
    write_sources_index(workspace, index)
    record_app_authored_entries(workspace, {"sources.yaml": serialize_sources_index(index)})


def _without_locators(workspace: Path) -> SourcesIndex:
    index = read_sources_index(workspace)
    assert index is not None
    return index.model_copy(
        update={"sources": [s.model_copy(update={"locator": None}) for s in index.sources]}
    )


async def _open(workspace: Path, data_dir: Path, cache_dir: Path) -> None:
    transport = httpx2.ASGITransport(app=_app(workspace, data_dir, cache_dir))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/workspace")
    assert response.status_code == 200, response.text


# Sanitising public URLs


def test_public_locator_strips_credentials_secrets_and_fragments() -> None:
    locator = public_locator(SIGNED_URL)
    assert locator == SourceLocator(url="https://example.com/papers/attention.md?page=2")

    with_userinfo = public_locator("HTTPS://user:pw@Example.org:8443/a?Signature=x&q=b+c")
    assert with_userinfo is not None
    assert with_userinfo.url == "https://example.org:8443/a?q=b+c"

    kept = public_locator("https://example.org/search?author=Rubin&year=1974&auth=x")
    assert kept is not None and kept.url == "https://example.org/search?author=Rubin&year=1974"


@pytest.mark.parametrize(
    "location",
    [
        "/home/author/papers/secret.pdf",
        "file:///home/author/papers/secret.pdf",
        "C:\\Users\\author\\paper.pdf",
        "ftp://example.org/paper.pdf",
        "javascript:alert(1)",
        "http://localhost:8000/paper.pdf",
        "http://127.0.0.1/paper.pdf",
        "http://10.0.0.4/paper.pdf",
        "http://[::1]/paper.pdf",
        "http://intranet.local/paper.pdf",
        "https://example.org/a b",
        "https:///no-host",
    ],
)
def test_non_public_locations_get_no_locator(location: str) -> None:
    assert public_locator(location) is None


def test_doi_and_arxiv_ids_are_recognised_from_their_resolvers() -> None:
    doi = public_locator("https://doi.org/10.1037/h0037350")
    assert doi is not None and doi.doi == "10.1037/h0037350"
    arxiv = public_locator("https://arxiv.org/pdf/1706.03762v7.pdf")
    assert arxiv is not None
    assert arxiv.arxiv_id == "1706.03762"
    assert arxiv.url == "https://arxiv.org/pdf/1706.03762v7.pdf"
    plain = public_locator("https://example.org/10.1037/h0037350")
    assert plain is not None and plain.doi is None and plain.arxiv_id is None


def test_the_locator_schema_only_accepts_public_http_metadata() -> None:
    for url in ("file:///etc/passwd", "https://user:pw@example.org/", "http://localhost/"):
        with pytest.raises(ValidationError):
            SourceLocator(url=url)
    with pytest.raises(ValidationError):
        SourceLocator()
    with pytest.raises(ValidationError):
        SourceLocator(doi="not-a-doi")
    assert SourceLocator(doi="10.1000/xyz").link() == "https://doi.org/10.1000/xyz"
    assert SourceLocator(arxiv_id="1706.03762").link() == "https://arxiv.org/abs/1706.03762"


def test_a_citations_own_url_wins_over_its_sources_locator() -> None:
    locator = SourceLocator(url="https://example.org/paper")
    assert citation_link("https://example.org/page-4", locator) == "https://example.org/page-4"
    assert citation_link(None, locator) == "https://example.org/paper"
    assert citation_link("javascript:alert(1)", locator) == "https://example.org/paper"
    assert citation_link(None, None) is None


def test_sources_yaml_without_locators_reads_and_writes_unchanged(tmp_path: Path) -> None:
    old = (
        "version: 1\n"
        "sources:\n"
        "- id: source-aaaaaaaaaaaa\n"
        "  resource_id: resource-aaaaaaaaaaaa\n"
        f"  source_version_id: {'a' * 64}\n"
        "  label: Causal Foundations\n"
        "  admitted_at: '2026-09-14T00:00:00+00:00'\n"
    )
    (tmp_path / "sources.yaml").write_text(old, encoding="utf-8")
    index = read_sources_index(tmp_path)
    assert index is not None and index.sources[0].locator is None
    assert serialize_sources_index(index).decode() == old


def test_filling_locators_never_replaces_one_already_present() -> None:
    kept = SourceLocator(url="https://mirror.example.org/paper")
    index = SourcesIndex(
        sources=[
            Source(
                id="source-aaaaaaaaaaaa",
                resource_id="resource-aaaaaaaaaaaa",
                source_version_id="a" * 64,
                label="Kept",
                admitted_at="2026-09-14T00:00:00+00:00",
                locator=kept,
            ),
            Source(
                id="source-bbbbbbbbbbbb",
                resource_id="resource-bbbbbbbbbbbb",
                source_version_id="b" * 64,
                label="Filled",
                admitted_at="2026-09-14T00:00:00+00:00",
            ),
        ]
    )
    library = {
        "resource-aaaaaaaaaaaa": SourceLocator(url="https://example.org/a"),
        "resource-bbbbbbbbbbbb": SourceLocator(url="https://example.org/b"),
    }
    filled = with_missing_locators(index, library)
    assert filled is not None
    assert [source.locator for source in filled.sources] == [
        kept,
        SourceLocator(url="https://example.org/b"),
    ]
    assert with_missing_locators(filled, library) is None


# Admission


@pytest.mark.anyio
async def test_admission_records_a_sanitised_locator_and_none_for_an_upload(
    tmp_path: Path,
) -> None:
    workspace, _data_dir, _cache_dir = await _admit_remote_and_upload(tmp_path)

    sources = _sources_by_label(workspace)
    assert sources["Attention"].locator == SourceLocator(
        url="https://example.com/papers/attention.md?page=2"
    )
    assert sources["Lecture notes"].locator is None
    text = (workspace / "sources.yaml").read_text(encoding="utf-8")
    assert "locator:\n    url: https://example.com/papers/attention.md?page=2\n" in text
    for secret in ("Signature", "access_token", "apiKey", "Expires", "section-3", "chapter_"):
        assert secret not in text
    assert str(tmp_path) not in text
    assert read_current_state(workspace).drift == "clean"


# Backfill when a Workspace opens


@pytest.mark.anyio
async def test_opening_a_workspace_backfills_missing_locators_once(tmp_path: Path) -> None:
    workspace, data_dir, cache_dir = await _admit_remote_and_upload(tmp_path)
    _record_as_app_authored(workspace, _without_locators(workspace))
    assert read_current_state(workspace).drift == "clean"

    await _open(workspace, data_dir, cache_dir)

    sources = _sources_by_label(workspace)
    assert sources["Attention"].locator == SourceLocator(
        url="https://example.com/papers/attention.md?page=2"
    )
    assert sources["Lecture notes"].locator is None
    state = read_current_state(workspace)
    assert state.drift == "clean" and state.validation.valid

    sources_path = workspace / "sources.yaml"
    before = sources_path.stat()
    await _open(workspace, data_dir, cache_dir)
    after = sources_path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.anyio
async def test_backfill_never_overwrites_a_locator_already_present(tmp_path: Path) -> None:
    workspace, data_dir, _cache_dir = await _admit_remote_and_upload(tmp_path)
    chosen = SourceLocator(url="https://mirror.example.org/attention", doi="10.1000/xyz")
    index = _without_locators(workspace)
    index = index.model_copy(
        update={
            "sources": [
                s.model_copy(update={"locator": chosen}) if s.label == "Attention" else s
                for s in index.sources
            ]
        }
    )
    _record_as_app_authored(workspace, index)
    content = (workspace / "sources.yaml").read_bytes()

    assert backfill_source_locators(workspace, data_dir) is False
    assert (workspace / "sources.yaml").read_bytes() == content
    assert _sources_by_label(workspace)["Attention"].locator == chosen


@pytest.mark.anyio
async def test_backfill_leaves_workspace_drift_alone_and_retries_on_a_later_open(
    tmp_path: Path,
) -> None:
    workspace, data_dir, cache_dir = await _admit_remote_and_upload(tmp_path)
    # An external edit removes the locators: Workspace Drift the Course Author must review.
    write_sources_index(workspace, _without_locators(workspace))
    drifted = (workspace / "sources.yaml").read_bytes()
    assert read_current_state(workspace).drift == "drift"

    await _open(workspace, data_dir, cache_dir)
    assert (workspace / "sources.yaml").read_bytes() == drifted

    # Once the change is accepted as app-authored state, the next open fills the locator in.
    record_app_authored_entries(workspace, {"sources.yaml": drifted})
    await _open(workspace, data_dir, cache_dir)
    assert _sources_by_label(workspace)["Attention"].locator is not None


@pytest.mark.anyio
async def test_backfill_leaves_an_invalid_sources_file_alone(tmp_path: Path) -> None:
    workspace, data_dir, cache_dir = await _admit_remote_and_upload(tmp_path)
    (workspace / "sources.yaml").write_text("sources: [not valid", encoding="utf-8")

    assert backfill_source_locators(workspace, data_dir) is False
    await _open(workspace, data_dir, cache_dir)
    assert (workspace / "sources.yaml").read_text(encoding="utf-8") == "sources: [not valid"


# Release validation and manifests

LECTURE_ID = "lecture-aaaaaaaaaaaa"
PRESENTATION_ID = "presentation-aaaaaaaaaaaa"
LINKED_ID = "source-aaaaaaaaaaaa"
UNLINKED_ID = "source-bbbbbbbbbbbb"
LOCATOR = SourceLocator(url="https://example.org/foundations", doi="10.1000/foundations")
MISSING_LOCATOR = f"citation.source-without-locator:{UNLINKED_ID}"


def _release_sources() -> SourcesIndex:
    return SourcesIndex(
        sources=[
            Source(
                id=LINKED_ID,
                resource_id="resource-aaaaaaaaaaaa",
                source_version_id="a" * 64,
                label="Foundations",
                admitted_at="2026-09-14T00:00:00+00:00",
                locator=LOCATOR,
            ),
            Source(
                id=UNLINKED_ID,
                resource_id="resource-bbbbbbbbbbbb",
                source_version_id="b" * 64,
                label="Lecture notes",
                admitted_at="2026-09-14T00:00:00+00:00",
            ),
        ]
    )


def _presentation(*citations: SlideCitation) -> Presentation:
    return Presentation(
        id=PRESENTATION_ID,
        lecture_id=LECTURE_ID,
        slides=[TitleSlide(id="slide-aaaaaaaaaaaa", title="Foundations", citations=[*citations])],
    )


def _plan() -> CoursePlan:
    return CoursePlan(
        id="course-aaaaaaaaaaaa",
        title="Causal inference",
        audience="Graduate students",
        lectures=[Lecture(id=LECTURE_ID, title="Foundations", presentation_id=PRESENTATION_ID)],
    )


SELECTION = ReleaseSelection(lecture_ids=[LECTURE_ID], artifact_ids=[PRESENTATION_ID])


def _validate(presentation: Presentation, waivers: list[Waiver] | None = None):
    return validate_release(
        plan=_plan(),
        presentations=[presentation],
        sources=_release_sources(),
        selection=SELECTION,
        waivers=waivers,
    )


def test_a_cited_source_without_a_locator_is_a_waivable_warning() -> None:
    unlinked = SlideCitation(source_id=UNLINKED_ID, label="Lecture notes")
    linked = SlideCitation(source_id=LINKED_ID, label="Foundations")

    result = _validate(_presentation(linked, unlinked))
    (finding,) = result.findings
    assert finding.id == MISSING_LOCATOR
    assert finding.severity == "warning"
    assert finding.target.scope == "source" and finding.target.source_id == UNLINKED_ID
    assert "Lecture notes" in finding.message
    assert result.structurally_valid and not result.can_publish

    waived = _validate(
        _presentation(linked, unlinked),
        [Waiver(finding_id=MISSING_LOCATOR, justification="Notes are handed out in class.")],
    )
    assert waived.can_publish and waived.findings[0].waived


def test_no_locator_warning_when_every_citation_has_its_own_link_or_none_cites_it() -> None:
    own_url = SlideCitation(
        source_id=UNLINKED_ID, label="Lecture notes", url="https://example.org/notes"
    )
    assert _validate(_presentation(own_url)).findings == []
    linked = SlideCitation(source_id=LINKED_ID, label="Foundations")
    assert _validate(_presentation(linked)).findings == []


def _workspace_with_citation(tmp_path: Path, citation: SlideCitation) -> Path:
    workspace = tmp_path / "course"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(workspace)], check=True)
    write_course_plan(workspace, _plan())
    write_presentation(workspace, _presentation(citation))
    write_sources_index(workspace, _release_sources())
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    return workspace


def _publish(workspace: Path, tmp_path: Path, waivers: list[Waiver]) -> CourseRelease:
    return publish_release(
        workspace=workspace,
        release_data_root=tmp_path / "releases",
        templates_data_root=tmp_path / "templates",
        evidence_line_counts={},
        request=PublishReleaseRequest(
            slug="fall-2026", name="Fall 2026", selection=SELECTION, waivers=waivers
        ),
    )


def test_release_manifests_carry_source_locators_for_readers(tmp_path: Path) -> None:
    workspace = _workspace_with_citation(
        tmp_path, SlideCitation(source_id=UNLINKED_ID, label="Lecture notes")
    )
    with pytest.raises(ReleaseValidationError):
        _publish(workspace, tmp_path, [])
    release = _publish(
        workspace,
        tmp_path,
        [Waiver(finding_id=MISSING_LOCATOR, justification="Notes are handed out in class.")],
    )

    mirror = tmp_path / "mirror.git"
    subprocess.run(
        ["git", "clone", "--quiet", "--mirror", str(workspace), str(mirror)],
        check=True,
        capture_output=True,
    )
    read = releases.read_verified_release(mirror, "fall-2026")
    assert read is not None and read == release
    assert [pin.locator for pin in read.sources] == [LOCATOR, None]
    tag = subprocess.run(
        ["git", "cat-file", "tag", "course-release/fall-2026"],
        cwd=mirror,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert '"locator":{"doi":"10.1000/foundations","url":"https://example.org/foundations"}' in tag


def test_releases_published_before_locators_still_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Release from before locators cites a Source without one and records no warning."""
    workspace = tmp_path / "course"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(workspace)], check=True)
    write_course_plan(workspace, _plan())
    write_presentation(
        workspace, _presentation(SlideCitation(source_id=UNLINKED_ID, label="Lecture notes"))
    )
    old_sources = SourcesIndex(sources=[_release_sources().sources[1]])
    write_sources_index(workspace, old_sources)
    create_revision(workspace, RevisionCreateRequest(summary="Initial Course"))
    record_app_authored_state(workspace)
    monkeypatch.setattr(
        release_service,
        "validate_release",
        partial(validate_release, check_source_locators=False),
    )
    monkeypatch.setattr(
        releases,
        "validate_release",
        partial(validate_release, check_source_locators=False),
    )
    release = _publish(workspace, tmp_path, [])
    monkeypatch.undo()

    assert release.validation.findings == []
    assert "locator" not in releases.canonical_json(release.model_dump(mode="json")).decode()
    assert releases.read_verified_release(workspace, "fall-2026") == release
