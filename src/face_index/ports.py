"""The seams between the pipeline and the outside world (constitution II and VI).

``pipeline.py`` and ``crawl.py`` depend only on these protocols and on ``models``. Only the
adapters (``HttpFetcher``, ``PostgresPacer``, ``PillowNormalizer``, ``DeepFaceEmbedder``,
``PostgresIndexStore``) import network, image, model or database libraries.
"""

from collections.abc import Callable
from typing import Protocol

from face_index.models import (
    DetectedFace,
    ImageResponse,
    NormalizedImage,
    PageResponse,
    PendingImage,
    RequestKind,
    Skipped,
)


class Resolver(Protocol):
    def resolve(self, host: str) -> list[str]:
        """Return every IP address the host name resolves to."""
        ...


class Pacer(Protocol):
    def wait(self, host: str, kind: RequestKind) -> None:
        """Block until a request of this kind to this host may start (FR-003)."""
        ...


class Fetcher(Protocol):
    def get_page(self, url: str) -> PageResponse: ...

    def get_image(self, url: str) -> ImageResponse: ...


class ImageNormalizer(Protocol):
    def normalize(self, data: bytes, content_type: str | None) -> NormalizedImage | Skipped: ...


class FaceEmbedder(Protocol):
    def embed(self, jpeg: bytes) -> list[DetectedFace]: ...


class IndexStore(Protocol):
    """Everything the pipeline persists. Implementations raise only ``InfrastructureError``."""

    def init_schema(self) -> int:
        """Create the schema if needed and return the current model profile id."""
        ...

    def upsert_page(self, url: str, site: str, depth: int) -> int: ...

    def reset_lead(self, page_id: int) -> None: ...

    def find_image_by_url(self, url: str) -> int | None: ...

    def find_image_by_sha256(self, sha256: bytes) -> int | None: ...

    def add_url_and_link(self, image_id: int, url: str, page_id: int, is_lead: bool) -> None:
        """Record another URL for a known image and link it to the page."""
        ...

    def link(self, page_id: int, image_id: int, is_lead: bool) -> None: ...

    def stage_image(
        self, image: NormalizedImage | Skipped, url: str, page_id: int, is_lead: bool
    ) -> tuple[int, bool]:
        """Insert a new image (pending or skipped) with its URL and link in one transaction.

        Returns ``(image_id, created)``; ``created`` is False when another writer stored the
        same content first, in which case the URL and link point at the existing image.
        """
        ...

    def pending_batches(self, batch_size: int, max_attempts: int) -> list[list[int]]: ...

    def abandoned_pending(self, max_attempts: int) -> int: ...

    def process_pending(
        self,
        image_id: int,
        detect: Callable[[PendingImage], list[DetectedFace]],
        profile_id: int,
        *,
        wait: bool = False,
    ) -> int | None:
        """Lock one pending image, run ``detect`` on it and store the result atomically.

        Returns the number of faces stored, or None if the image is no longer pending or is
        locked by another worker. With ``wait``, a lock held elsewhere is awaited (bounded by
        ``config.DETECT_LOCK_TIMEOUT_S``) instead of skipped. If ``detect`` raises, nothing is
        stored and the exception propagates.
        """
        ...

    def stored_faces(self, image_id: int) -> int | None:
        """Faces stored for an image whose detection is done, or None while it is pending."""
        ...

    def is_abandoned(self, url: str, kind: RequestKind, max_attempts: int) -> bool: ...

    def record_failure(self, url: str, kind: RequestKind, error: str, run_id: str) -> None: ...

    def record_success(self, url: str, kind: RequestKind) -> None: ...

    def record_detect_failure(self, image_id: int, run_id: str) -> None: ...
