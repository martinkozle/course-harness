"""Read image files placed on Slides."""

from io import BytesIO

from PIL import Image, UnidentifiedImageError


def image_dimensions(content: bytes) -> tuple[int, int]:
    """Return an image's pixel size, rejecting content that is not a readable image."""
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            return image.size
    except (UnidentifiedImageError, OSError, SyntaxError) as error:
        raise ValueError("The image could not be read.") from error
