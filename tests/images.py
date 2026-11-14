"""Test images generated in code, so no binary fixtures are committed."""

import io
import struct
import zlib

from PIL import Image


def encode(image: Image.Image, fmt: str, **save: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


def photo(width: int = 400, height: int = 300, seed: int = 0, fmt: str = "JPEG") -> bytes:
    """A deterministic, non-uniform RGB image (distinct per seed)."""
    image = Image.new("RGB", (width, height))
    pixels = [
        ((x * 7 + seed * 31) % 256, (y * 5 + seed * 17) % 256, ((x + y) * 3 + seed) % 256)
        for y in range(height)
        for x in range(width)
    ]
    image.putdata(pixels)
    return encode(image, fmt)


def rgba_png(width: int = 200, height: int = 200) -> bytes:
    image = Image.new("RGBA", (width, height), (255, 0, 0, 0))
    for x in range(width // 2):
        for y in range(height):
            image.putpixel((x, y), (0, 0, 255, 255))
    return encode(image, "PNG")


def animated_gif() -> bytes:
    frames = [Image.new("RGB", (120, 120), color) for color in ((255, 0, 0), (0, 255, 0))]
    return encode(frames[0], "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)


def exif_rotated_jpeg() -> bytes:
    """A 300x100 image whose EXIF orientation (6 = rotate 90° CW) makes it 100x300 upright."""
    image = Image.new("RGB", (300, 100), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6
    return encode(image, "JPEG", exif=exif.tobytes())


SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>'


def corrupt_jpeg() -> bytes:
    return photo(200, 200)[:300]


def bomb_png(width: int = 10_000, height: int = 10_000) -> bytes:
    """A tiny PNG whose header claims a huge size (never decoded by the normalizer)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(b""))
        + chunk(b"IEND", b"")
    )
