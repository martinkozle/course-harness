"""Take the pictures of a Lecture's image Slides out of its Release PPTX.

The Classroom never hosts Source binaries, but an image Slide's picture is already part of the
Release: the exported PPTX embeds it. Serving that embedded copy shows the Slide as it is in the
download, and only for images the Course Author placed on a Slide.
"""

from __future__ import annotations

import logging
from io import BytesIO
from pathlib import Path

from PIL import Image
from pptx import Presentation as PPTXPresentation
from pptx.shapes.picture import Picture
from pptx.shapes.placeholder import PlaceholderPicture

from course_harness.core.presentation import ImageSlide, Presentation

logger = logging.getLogger(__name__)

# Formats a browser shows as an image; SVG is left out, since it can carry script.
IMAGE_EXTENSIONS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "WEBP": "webp"}
MAX_IMAGE_BYTES = 20_000_000


def extract_slide_images(pptx: bytes, presentation: Presentation, target: Path) -> dict[str, str]:
    """Write the picture of each image Slide below ``target`` as ``<slide id>.<extension>``.

    The PPTX holds one slide per active Slide, in order, as the export wrote it. Returns the file
    name of each Slide whose picture was found and is a readable image.
    """
    active = [slide for slide in presentation.slides if not slide.archived]
    if not any(isinstance(slide, ImageSlide) for slide in active):
        return {}
    deck = PPTXPresentation(BytesIO(pptx))
    pages = list(deck.slides)
    if len(pages) != len(active):
        logger.warning(
            "%s has %d slides but its PPTX %d; its images are not shown",
            presentation.id,
            len(active),
            len(pages),
        )
        return {}
    found: dict[str, str] = {}
    for slide, page in zip(active, pages, strict=True):
        if not isinstance(slide, ImageSlide):
            continue
        picture = next(
            (shape for shape in page.shapes if isinstance(shape, Picture | PlaceholderPicture)),
            None,
        )
        if picture is None:
            continue
        content = picture.image.blob
        extension = _image_extension(content)
        if extension is None:
            continue
        target.mkdir(parents=True, exist_ok=True)
        name = f"{slide.id}.{extension}"
        (target / name).write_bytes(content)
        found[slide.id] = name
    return found


def _image_extension(content: bytes) -> str | None:
    """The file extension of a readable image in a format browsers show, by its content."""
    if len(content) > MAX_IMAGE_BYTES:
        return None
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            image_format = image.format
    except Exception:  # Pillow raises many kinds of errors for a damaged image
        return None
    return IMAGE_EXTENSIONS.get(image_format or "")
