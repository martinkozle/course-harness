from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import pytest
from pptx import Presentation as PPTXPresentation

from course_harness.app import create_app
from course_harness.core import slide_preview
from course_harness.core.course_plan import (
    CoursePlanInput,
    LectureInput,
    create_course_plan,
    create_course_plan_file,
    initialize_workspace_history,
)
from course_harness.core.presentation import BulletsSlide, Presentation, Slide
from course_harness.core.slide_preview import (
    PreviewContext,
    UnsafePresentationArchive,
    build_preview,
    check_pptx_archive,
    normalized_layout_slots,
    render_presentation_preview,
    renderer_capability,
)
from course_harness.core.template_inspect import inspect_template
from course_harness.core.template_profiles import TemplateLayoutMapping, TemplateProfile
from course_harness.template_profiles import profile_dir, write_profile_version


def _profile(template_path: Path) -> TemplateProfile:
    inspection = inspect_template(template_path)
    return TemplateProfile(
        id="tpl-000000000001",
        name="Preview template",
        version=2,
        template_filename=template_path.name,
        slide_width=inspection["slide_width"],
        slide_height=inspection["slide_height"],
        slide_count=inspection["slide_count"],
        layouts=[
            TemplateLayoutMapping(
                semantic_layout="bullets",
                template_layout_index=1,
                confidence=1,
                rationale="Test mapping",
                slot_mappings={"title": 0, "body": 1},
            )
        ],
    )


def test_layout_slots_are_normalized_and_keep_extracted_typography(tmp_path: Path) -> None:
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)

    inspection = inspect_template(template_path)
    slots = normalized_layout_slots(profile, inspection, "bullets")

    assert set(slots) == {"title", "body"}
    assert slots["title"].left == 0.05
    assert 0 < slots["body"].width <= 1
    assert slots["title"].font_size > 0
    assert slots["title"].font_family
    assert slots["title"].font_family == inspection["theme"]["fonts"]["major"]


def test_preview_keeps_semantic_fallback_when_renderer_is_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    presentation = Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=[
            BulletsSlide(
                id="slide-abc123def456",
                title="Visible without LibreOffice",
                bullets=["One", "Two"],
            )
        ],
    )
    monkeypatch.setattr("course_harness.core.slide_preview.shutil.which", lambda _name: None)

    preview = build_preview(
        presentation, PreviewContext(profile, template_path, tmp_path / "cache")
    )

    assert preview.renderer.available is False
    assert preview.slides[0].thumbnail_url is None
    assert preview.slides[0].slots["body"].top > 0
    assert preview.slides[0].background_url is None


def test_renderer_capability_reports_detected_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which",
        lambda _name: "/nix/store/libreoffice/bin/libreoffice",
    )

    capability = renderer_capability()

    assert capability.available is True
    assert capability.name == "LibreOffice"


def test_renderer_capability_accepts_soffice_binary(monkeypatch) -> None:
    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which",
        lambda name: "/opt/libreoffice/program/soffice" if name == "soffice" else None,
    )

    assert renderer_capability().available is True


def test_render_populates_cached_background_and_authoritative_thumbnail(
    tmp_path: Path, monkeypatch
) -> None:
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    presentation = Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=[
            BulletsSlide(
                id="slide-abc123def456",
                title="Rendered title",
                bullets=["Rendered body"],
            ),
            BulletsSlide(
                id="slide-abc123def457",
                title="Archived title",
                bullets=["Archived body"],
                archived=True,
            ),
        ],
    )
    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which", lambda _name: "/bin/libreoffice"
    )

    def fake_render(_pptx_bytes: bytes, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")

    monkeypatch.setattr("course_harness.core.slide_preview._render_pptx_png", fake_render)

    rendered = render_presentation_preview(
        presentation, PreviewContext(profile, template_path, tmp_path / "cache")
    )

    assert rendered.slides[0].background_url is not None
    assert rendered.slides[0].thumbnail_url is not None
    assert rendered.slides[0].render_key in rendered.slides[0].thumbnail_url
    assert rendered.slides[1].thumbnail_url is not None


def test_editing_one_slide_only_renders_that_slide_again(tmp_path: Path, monkeypatch) -> None:
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    slides: list[Slide] = [
        BulletsSlide(
            id=f"slide-abc123def45{index}",
            title=f"Slide {index}",
            bullets=["Body"],
        )
        for index in range(3)
    ]
    presentation = Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=slides,
    )
    context = PreviewContext(profile, template_path, tmp_path / "cache")
    rendered_targets: list[Path] = []

    def fake_render(_pptx_bytes: bytes, target: Path) -> None:
        rendered_targets.append(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")

    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which", lambda _name: "/bin/libreoffice"
    )
    monkeypatch.setattr(
        "course_harness.core.slide_preview.render_layout_backgrounds", lambda _: None
    )
    monkeypatch.setattr("course_harness.core.slide_preview._render_pptx_png", fake_render)

    original = render_presentation_preview(presentation, context)
    edited = presentation.model_copy(
        update={
            "slides": [
                slides[0],
                slides[1].model_copy(update={"title": "Edited slide"}),
                slides[2],
            ]
        }
    )
    updated = render_presentation_preview(edited, context)
    notes_only = edited.model_copy(
        update={
            "slides": [
                edited.slides[0],
                edited.slides[1].model_copy(update={"speaker_notes": "New notes"}),
                edited.slides[2],
            ]
        }
    )
    notes_updated = render_presentation_preview(notes_only, context)

    assert len(rendered_targets) == 4
    assert original.slides[0].thumbnail_url == updated.slides[0].thumbnail_url
    assert original.slides[1].thumbnail_url != updated.slides[1].thumbnail_url
    assert original.slides[2].thumbnail_url == updated.slides[2].thumbnail_url
    assert notes_updated.slides[1].thumbnail_url == updated.slides[1].thumbnail_url


def test_concurrent_requests_render_each_thumbnail_once(tmp_path: Path, monkeypatch) -> None:
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    presentation = Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=[
            BulletsSlide(
                id="slide-abc123def456",
                title="One render",
                bullets=["Shared by every request"],
            )
        ],
    )
    context = PreviewContext(profile, template_path, tmp_path / "cache")
    render_count = 0
    count_lock = threading.Lock()

    def fake_render(_pptx_bytes: bytes, target: Path) -> None:
        nonlocal render_count
        with count_lock:
            render_count += 1
        time.sleep(0.05)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")

    monkeypatch.setattr(
        "course_harness.core.slide_preview.shutil.which", lambda _name: "/bin/libreoffice"
    )
    monkeypatch.setattr(
        "course_harness.core.slide_preview.render_layout_backgrounds", lambda _: None
    )
    monkeypatch.setattr("course_harness.core.slide_preview._render_pptx_png", fake_render)

    with ThreadPoolExecutor(max_workers=4) as executor:
        previews = list(
            executor.map(
                lambda _: render_presentation_preview(presentation, context),
                range(4),
            )
        )

    assert render_count == 1
    assert all(preview.slides[0].thumbnail_url for preview in previews)


def test_libreoffice_adapter_produces_png_without_pixel_identity_requirement(
    tmp_path: Path,
) -> None:
    if not renderer_capability().available:
        pytest.skip("LibreOffice is not installed")
    template_path = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    presentation = Presentation(
        id="presentation-abc123def456",
        lecture_id="lecture-abc123def456",
        slides=[
            BulletsSlide(
                id="slide-abc123def456",
                title="Adapter smoke",
                bullets=["Renderer output stays editable and readable"],
            )
        ],
    )

    rendered = render_presentation_preview(
        presentation, PreviewContext(profile, template_path, tmp_path / "cache")
    )

    thumbnail = (
        tmp_path
        / "cache"
        / profile.id
        / "previews"
        / rendered.slides[0].render_key
        / "thumbnail.png"
    )
    png = thumbnail.read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert width >= 500
    assert width / height == pytest.approx(profile.slide_width / profile.slide_height, rel=0.03)

    golden = Path(__file__).parent / "fixtures" / "previews" / "libreoffice-bullets.png"
    if os.environ.get("COURSE_HARNESS_UPDATE_PREVIEW_FIXTURE") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(thumbnail, golden)
    comparator = shutil.which("compare")
    if comparator is None:
        pytest.skip("ImageMagick compare is not installed")
    assert golden.is_file(), "Run with COURSE_HARNESS_UPDATE_PREVIEW_FIXTURE=1"
    comparison = subprocess.run(
        [comparator, "-metric", "RMSE", str(golden), str(thumbnail), "null:"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert comparison.returncode in {0, 1}
    normalized_match = re.search(r"\((\d+(?:\.\d+)?)\)", comparison.stderr)
    assert normalized_match is not None
    assert float(normalized_match.group(1)) <= 0.06


@pytest.mark.anyio
async def test_preview_api_exposes_semantic_fallback_and_renderer_state(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plan = create_course_plan(
        CoursePlanInput(
            title="Preview course",
            audience="Course Authors",
            lectures=[LectureInput(title="Lecture one")],
        )
    ).model_copy(
        update={
            "template_profile_id": "tpl-000000000001",
            "template_profile_version": 2,
        }
    )
    initialize_workspace_history(workspace)
    create_course_plan_file(workspace, plan)
    template_data = tmp_path / "templates"
    template_path = profile_dir(template_data, "tpl-000000000001") / "template.pptx"
    template_path.parent.mkdir(parents=True)
    PPTXPresentation().save(str(template_path))
    profile = _profile(template_path)
    write_profile_version(template_data, profile)
    from course_harness.core.presentation import write_presentation

    write_presentation(
        workspace,
        Presentation(
            id="presentation-abc123def456",
            lecture_id=plan.lectures[0].id,
            slides=[
                BulletsSlide(
                    id="slide-abc123def456",
                    title="API preview",
                    bullets=["Semantic content"],
                )
            ],
        ),
    )
    monkeypatch.setattr("course_harness.core.slide_preview.shutil.which", lambda _name: None)
    app = create_app(
        workspace,
        templates_data_path=template_data,
        templates_cache_path=tmp_path / "cache",
    )

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/api/presentations/{plan.lectures[0].id}/preview")
        render = await client.post(f"/api/presentations/{plan.lectures[0].id}/preview/render")

    assert response.status_code == 200
    assert response.json()["slides"][0]["slots"]["title"]["left"] == 0.05
    assert response.json()["renderer"]["available"] is False
    assert render.status_code == 422
    assert "Semantic previews remain available" in render.json()["detail"]


def test_a_template_that_inflates_too_far_is_refused_before_it_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    template = tmp_path / "template.pptx"
    PPTXPresentation().save(str(template))
    check_pptx_archive(template)

    monkeypatch.setattr(slide_preview, "MAX_PPTX_UNCOMPRESSED_BYTES", 1_000)
    with pytest.raises(UnsafePresentationArchive, match="too large"):
        check_pptx_archive(template)
    monkeypatch.undo()
    monkeypatch.setattr(slide_preview, "MAX_PPTX_ENTRIES", 3)
    with pytest.raises(UnsafePresentationArchive, match="too many"):
        check_pptx_archive(template)
    (tmp_path / "plain.pptx").write_bytes(b"not a zip")
    with pytest.raises(UnsafePresentationArchive):
        check_pptx_archive(tmp_path / "plain.pptx")


def _fake_libreoffice(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake-libreoffice"
    script.write_text(f"#!/bin/sh\n{body}\n")
    script.chmod(0o755)
    return script


def test_each_libreoffice_run_gets_its_own_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arguments = tmp_path / "arguments"
    script = _fake_libreoffice(
        tmp_path,
        f'printf "%s\\n" "$@" >> {arguments}\n'
        'while [ "$1" != "--outdir" ]; do shift; done\n'
        'printf PNG > "$2/slide.png"',
    )
    monkeypatch.setattr(slide_preview, "_libreoffice_executable", lambda: str(script))

    slide_preview._render_pptx_png(b"pptx", tmp_path / "one.png")
    slide_preview._render_pptx_png(b"pptx", tmp_path / "two.png")

    assert (tmp_path / "one.png").read_bytes() == b"PNG"
    profiles = [line for line in arguments.read_text().splitlines() if line.startswith("-env:")]
    assert len(profiles) == 2 and profiles[0] != profiles[1]
    assert all(line.startswith("-env:UserInstallation=file://") for line in profiles)


def test_a_libreoffice_run_that_overruns_is_killed_with_its_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = tmp_path / "child.pid"
    script = _fake_libreoffice(tmp_path, f"sleep 30 & echo $! > {child}\nwait")
    monkeypatch.setattr(slide_preview, "_libreoffice_executable", lambda: str(script))
    monkeypatch.setattr(slide_preview, "LIBREOFFICE_TIMEOUT_SECONDS", 1)

    with pytest.raises(RuntimeError, match="timed out"):
        slide_preview._render_pptx_png(b"pptx", tmp_path / "slide.png")

    pid = int(child.read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError("The renderer's child process survived")
