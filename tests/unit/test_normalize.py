import hashlib
import io

import pytest
from PIL import Image

from face_index.errors import ItemError
from face_index.models import NormalizedImage, Skipped
from face_index.normalize import PillowNormalizer
from tests import images

normalizer = PillowNormalizer()


def ok(data: bytes, content_type: str | None = None) -> NormalizedImage:
    result = normalizer.normalize(data, content_type)
    assert isinstance(result, NormalizedImage)
    return result


def rgb_at(image: Image.Image, xy: tuple[int, int]) -> tuple[int, int, int]:
    pixel = image.getpixel(xy)
    assert isinstance(pixel, tuple)
    return int(pixel[0]), int(pixel[1]), int(pixel[2])


def decoded(result: NormalizedImage) -> Image.Image:
    return Image.open(io.BytesIO(result.jpeg))


def test_large_jpeg_shrinks_to_1024_longest_side() -> None:
    result = ok(images.photo(3000, 1500))
    assert (result.width, result.height) == (1024, 512)
    assert (result.original_width, result.original_height) == (3000, 1500)
    assert result.jpeg.startswith(b"\xff\xd8\xff")
    assert decoded(result).size == (1024, 512)


def test_small_image_is_not_upscaled() -> None:
    result = ok(images.photo(500, 400, fmt="PNG"))
    assert (result.width, result.height) == (500, 400)
    assert result.original_format == "PNG"


def test_rgba_png_flattened_on_white() -> None:
    result = ok(images.rgba_png())
    image = decoded(result)
    assert image.mode == "RGB"
    r, g, b = rgb_at(image, (190, 100))  # transparent half → white
    assert min(r, g, b) > 240


def test_animated_gif_uses_first_frame() -> None:
    image = decoded(ok(images.animated_gif()))
    r, g, _ = rgb_at(image, (60, 60))
    assert r > 200
    assert g < 60


def test_exif_orientation_applied_and_stripped() -> None:
    result = ok(images.exif_rotated_jpeg())
    assert (result.width, result.height) == (100, 300)
    assert not decoded(result).getexif()


def test_sha256_is_of_original_bytes() -> None:
    data = images.photo(200, 200)
    assert ok(data).sha256 == hashlib.sha256(data).digest()


def test_svg_is_skipped() -> None:
    result = normalizer.normalize(images.SVG, "image/svg+xml")
    assert isinstance(result, Skipped)
    assert result.reason == "unsupported_format"
    assert isinstance(normalizer.normalize(images.SVG, None), Skipped)


def test_tiny_image_is_skipped_with_dimensions() -> None:
    result = normalizer.normalize(images.photo(40, 40), "image/jpeg")
    assert isinstance(result, Skipped)
    assert (result.reason, result.original_width, result.original_height) == ("too_small", 40, 40)


def test_decompression_bomb_is_skipped_without_decoding() -> None:
    result = normalizer.normalize(images.bomb_png(), "image/png")
    assert isinstance(result, Skipped)
    assert result.reason == "too_large"


def test_corrupt_bytes_are_an_item_error_not_a_skip() -> None:
    with pytest.raises(ItemError, match="decode error"):
        normalizer.normalize(images.corrupt_jpeg(), "image/jpeg")
    with pytest.raises(ItemError):
        normalizer.normalize(b"not an image", None)
