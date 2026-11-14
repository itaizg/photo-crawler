"""The search core: prepare the query photo and search each face (feature 002, research R6).

Depends only on the ``ImageNormalizer``, ``FaceEmbedder`` and ``SearchIndex`` protocols, so it is
tested with fakes. No I/O, printing or exit statuses here: those live in ``face_matcher``.
"""

from collections.abc import Sequence

from face_index import config
from face_index.errors import ItemError
from face_index.models import FaceResult, NormalizedImage, QueryFace, Skipped
from face_index.ports import FaceEmbedder, ImageNormalizer, SearchIndex


class QueryImageError(Exception):
    """The query file cannot be used as a photo (exit status 2)."""


class NoFaceError(Exception):
    """No face was detected in the query photo (exit status 3)."""


class FaceNumberError(Exception):
    """The requested face number is not in the photo (exit status 64)."""

    def __init__(self, number: int, count: int) -> None:
        super().__init__(f"--face {number}, but only {count} faces were found")
        self.number = number
        self.count = count


def _skip_message(skipped: Skipped) -> str:
    if skipped.reason == "unsupported_format":
        return "not a supported photo (SVG)"
    if skipped.reason == "too_small":
        return (
            f"image is {skipped.original_width}x{skipped.original_height}; "
            f"at least {config.MIN_SIDE} px on the shortest side is needed"
        )
    return f"image exceeds {config.MAX_PIXELS // 1_000_000} megapixels"


def prepare_query(
    data: bytes, normalizer: ImageNormalizer, embedder: FaceEmbedder
) -> tuple[NormalizedImage, list[QueryFace]]:
    """Normalize the photo like an indexed image, then detect and number its faces (FR-003)."""
    try:
        normalized = normalizer.normalize(data, None)
    except ItemError as exc:
        raise QueryImageError("not a decodable image") from exc
    if isinstance(normalized, Skipped):
        raise QueryImageError(_skip_message(normalized))
    try:
        detected = embedder.embed(normalized.jpeg)
    except ItemError as exc:
        raise QueryImageError(f"face detection failed: {exc.reason}") from exc
    if not detected:
        raise NoFaceError
    # The embedder returns faces left to right, then top to bottom (faces.narrow_results).
    faces = [
        QueryFace(number=i, box=f.box, embedding=f.embedding)
        for i, f in enumerate(detected, start=1)
    ]
    return normalized, faces


def select_faces(faces: Sequence[QueryFace], number: int | None) -> list[QueryFace]:
    """All faces, or only face ``number`` (FR-011)."""
    if number is None:
        return list(faces)
    chosen = [f for f in faces if f.number == number]
    if not chosen:
        raise FaceNumberError(number, len(faces))
    return chosen


def search_faces(
    faces: Sequence[QueryFace], index: SearchIndex, profile_id: int, threshold: float
) -> list[FaceResult]:
    """One index query per face; pages come back de-duplicated and closest first (FR-006, 007)."""
    return [
        FaceResult(
            face=face, pages=tuple(index.matching_pages(face.embedding, threshold, profile_id))
        )
        for face in faces
    ]
