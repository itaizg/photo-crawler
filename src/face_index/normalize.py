"""Convert downloaded bytes to one JPEG of at most 1024 px, or a recorded skip (FR-008, FR-010,
FR-010a, research R7)."""

import hashlib
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from face_index import config
from face_index.errors import ItemError
from face_index.models import NormalizedImage, Skipped


def _is_svg(data: bytes, content_type: str | None) -> bool:
    if content_type and "svg" in content_type.lower():
        return True
    head = data[:512].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _flatten(image: Image.Image) -> Image.Image:
    """Composite transparency onto white and return an RGB image."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


class PillowNormalizer:
    """``ImageNormalizer`` adapter built on Pillow."""

    def __init__(
        self,
        max_side: int = config.MAX_SIDE,
        min_side: int = config.MIN_SIDE,
        max_pixels: int = config.MAX_PIXELS,
        quality: int = config.JPEG_QUALITY,
    ) -> None:
        self._max_side = max_side
        self._min_side = min_side
        self._max_pixels = max_pixels
        self._quality = quality

    def normalize(self, data: bytes, content_type: str | None) -> NormalizedImage | Skipped:
        sha256 = hashlib.sha256(data).digest()
        if _is_svg(data, content_type):
            return Skipped(sha256, "unsupported_format", content_type or "image/svg+xml")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(data))  # reads the header only
                original_format = image.format or "UNKNOWN"
                width, height = image.size
                if width * height > self._max_pixels:
                    return Skipped(sha256, "too_large", original_format, width, height)
                if min(width, height) < self._min_side:
                    return Skipped(sha256, "too_small", original_format, width, height)
                image.seek(0)  # first frame of animated images
                image.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            return Skipped(sha256, "too_large", "UNKNOWN")
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
            raise ItemError(f"decode error: {type(exc).__name__}") from exc

        upright = ImageOps.exif_transpose(image) or image
        rgb = _flatten(upright)
        rgb.thumbnail((self._max_side, self._max_side), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        rgb.save(buffer, format="JPEG", quality=self._quality, optimize=True)
        return NormalizedImage(
            sha256=sha256,
            jpeg=buffer.getvalue(),
            width=rgb.width,
            height=rgb.height,
            original_format=original_format,
            original_width=width,
            original_height=height,
        )
