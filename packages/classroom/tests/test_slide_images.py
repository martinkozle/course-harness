"""An image Slide's picture comes from the Release PPTX that embeds it."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from course_harness.core.export import export_presentation
from course_harness.core.presentation import ImageSlide, Presentation, TitleSlide
from course_harness.core.sources import SourceImage
from course_harness_classroom.slide_images import extract_slide_images


def _png(path: Path) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 20), "teal").save(buffer, format="PNG")
    path.write_bytes(buffer.getvalue())
    return buffer.getvalue()


def _presentation() -> Presentation:
    return Presentation(
        id="presentation-aaaaaaaaaaaa",
        lecture_id="lecture-aaaaaaaaaaaa",
        slides=[
            TitleSlide(id="slide-aaaaaaaaaaaa", title="Foundations"),
            ImageSlide(id="slide-dddddddddddd", archived=True, title="Left out"),
            ImageSlide(
                id="slide-bbbbbbbbbbbb",
                title="The diagram",
                image_source_id="source-aaaaaaaaaaaa",
                caption="One host, one client per server.",
            ),
            ImageSlide(id="slide-cccccccccccc", title="No picture", image_url="https://a.example"),
        ],
    )


def test_each_image_slide_gets_the_picture_its_pptx_embeds(tmp_path: Path) -> None:
    image = tmp_path / "diagram.png"
    content = _png(image)
    presentation = _presentation()
    pptx = export_presentation(
        presentation,
        image_resolver=lambda source_id: SourceImage(
            source_id=source_id, source_version_id="v1", media_type="image/png", path=image
        ),
    )

    found = extract_slide_images(pptx, presentation, tmp_path / "images")

    assert found == {"slide-bbbbbbbbbbbb": "slide-bbbbbbbbbbbb.png"}
    assert (tmp_path / "images" / "slide-bbbbbbbbbbbb.png").read_bytes() == content


def test_a_pptx_that_does_not_match_its_slides_shows_no_pictures(tmp_path: Path) -> None:
    presentation = _presentation()
    other = Presentation(
        id=presentation.id,
        lecture_id=presentation.lecture_id,
        slides=[TitleSlide(id="slide-aaaaaaaaaaaa", title="Foundations")],
    )

    assert extract_slide_images(export_presentation(other), presentation, tmp_path) == {}
