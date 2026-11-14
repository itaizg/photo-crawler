"""Draw labelled face boxes on a JPEG copy (export_image --boxes, face_matcher --list-faces)."""

import io
from collections.abc import Sequence

from PIL import Image, ImageDraw

from face_index import config
from face_index.models import Box

_RED = (255, 0, 0)


def draw_boxes(jpeg: bytes, boxes: Sequence[tuple[str, Box]]) -> bytes:
    """Return a JPEG of the same size with each box outlined and its label at the top-left."""
    picture = Image.open(io.BytesIO(jpeg)).convert("RGB")
    draw = ImageDraw.Draw(picture)
    for label, box in boxes:
        draw.rectangle((box.x, box.y, box.x + box.w - 1, box.y + box.h - 1), outline=_RED, width=2)
        draw.text((box.x + 3, box.y + 3), label, fill=_RED)
    out = io.BytesIO()
    picture.save(out, format="JPEG", quality=config.JPEG_QUALITY)
    return out.getvalue()
