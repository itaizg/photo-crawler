"""``draw_boxes``: numbered boxes on a JPEG, shared by export_image and face_matcher."""

import io

import pytest
from PIL import Image

from face_index import export_image
from face_index.drawing import draw_boxes
from face_index.models import Box
from face_index.store import ExportedImage
from tests import images


def decode(jpeg: bytes) -> Image.Image:
    return Image.open(io.BytesIO(jpeg)).convert("RGB")


def test_boxes_are_drawn_on_a_same_size_jpeg() -> None:
    source = images.photo(120, 80, seed=3)
    out = draw_boxes(
        source, [("1", Box(x=10, y=10, w=30, h=30)), ("2", Box(x=60, y=20, w=30, h=30))]
    )
    picture = decode(out)
    assert picture.size == (120, 80)
    # The outline is red on the box edge.
    pixel = picture.getpixel((10, 25))  # left edge of box 1
    assert isinstance(pixel, tuple)
    r, g, b = pixel[:3]
    assert r > 150
    assert g < 100
    assert b < 100


def test_no_boxes_gives_a_plain_copy_of_the_same_size() -> None:
    source = images.photo(64, 48, seed=1)
    assert decode(draw_boxes(source, [])).size == (64, 48)


def test_export_image_labels_faces_by_their_index(monkeypatch: pytest.MonkeyPatch) -> None:
    exported = ExportedImage(
        id=1,
        status="indexed",
        jpeg=images.photo(64, 64, seed=2),
        width=64,
        height=64,
        boxes=((0, 2, 2, 20, 20), (1, 30, 30, 20, 20)),
    )
    seen: list[list[tuple[str, Box]]] = []
    original = export_image.draw_boxes

    def spy(jpeg: bytes, boxes: list[tuple[str, Box]]) -> bytes:
        seen.append(list(boxes))
        return original(jpeg, boxes)

    monkeypatch.setattr(export_image, "draw_boxes", spy)
    export_image._with_boxes(exported)
    assert seen == [[("0", Box(x=2, y=2, w=20, h=20)), ("1", Box(x=30, y=30, w=20, h=20))]]
