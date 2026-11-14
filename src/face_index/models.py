"""Plain data passed across the ports. No driver types appear here.

Every record is a frozen Pydantic model (constitution IX): built once, validated at
construction, never mutated. A changed record is a new one (``model_copy(update=...)``).
"""

from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

type RequestKind = Literal["page", "image"]
type SkipReason = Literal["unsupported_format", "too_small", "too_large"]


class Record(BaseModel):
    """Base for every data record: frozen, so a record is never changed after it is built."""

    model_config = ConfigDict(frozen=True)


class PageExtract(Record):
    """What one HTML page yields: links to follow, image references, and its lead image."""

    links: tuple[str, ...]
    image_refs: tuple[str, ...]
    lead_ref: str | None


class CrawledPage(Record):
    url: str
    depth: int
    image_refs: tuple[str, ...]
    lead_ref: str | None


class PageResponse(Record):
    url: str
    content_type: str
    text: str | None  # None when the response is not HTML


class ImageResponse(Record):
    url: str
    content_type: str | None
    body: bytes


class NormalizedImage(Record):
    sha256: bytes
    jpeg: bytes
    width: int
    height: int
    original_format: str
    original_width: int
    original_height: int


class Skipped(Record):
    sha256: bytes
    reason: SkipReason
    original_format: str
    original_width: int | None = None
    original_height: int | None = None

    @model_validator(mode="after")
    def _too_small_has_dimensions(self) -> Self:
        # Dimensions may be unknown only when the header could not be read for its size.
        if self.reason == "too_small" and self.original_width is None:
            raise ValueError("a too_small skip must carry its original dimensions")
        return self


class Box(Record):
    x: int
    y: int
    w: int
    h: int


class DetectedFace(Record):
    box: Box
    confidence: float
    embedding: tuple[float, ...]


class QueryFace(Record):
    """One face in the search query photo, numbered from 1 left to right (FR-010)."""

    number: int
    box: Box
    embedding: tuple[float, ...]


class PageMatch(Record):
    """One page whose images contain a face matching a query face (contracts/search-query.md)."""

    url: str
    best_distance: float
    lead: bool
    image_urls: tuple[str, ...]


class FaceResult(Record):
    face: QueryFace
    pages: tuple[PageMatch, ...]  # closest first; empty means "no matching pages"


class PendingImage(Record):
    id: int
    jpeg: bytes
    width: int
    height: int


class Summary(Record):
    """Counters returned by every stage and merged by ``summarize`` (FR-025).

    Stages count into a local ``Counter`` and freeze it with ``Summary.of`` when they return.
    ``counts`` is sorted by key, so equal counts give equal summaries and the same JSON.
    """

    counts: tuple[tuple[str, int], ...] = ()

    @classmethod
    def of(cls, counts: Mapping[str, int]) -> Self:
        return cls(counts=tuple(sorted(counts.items())))

    def to_json(self) -> dict[str, int]:
        return dict(self.counts)
