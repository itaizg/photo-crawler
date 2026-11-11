"""Plain data passed across the ports. No driver types appear here."""

from dataclasses import dataclass, field
from typing import Literal

RequestKind = Literal["page", "image"]
SkipReason = Literal["unsupported_format", "too_small", "too_large"]


@dataclass(frozen=True, slots=True)
class PageExtract:
    """What one HTML page yields: links to follow, image references, and its lead image."""

    links: tuple[str, ...]
    image_refs: tuple[str, ...]
    lead_ref: str | None


@dataclass(frozen=True, slots=True)
class CrawledPage:
    url: str
    depth: int
    image_refs: tuple[str, ...]
    lead_ref: str | None


@dataclass(frozen=True, slots=True)
class PageResponse:
    url: str
    content_type: str
    text: str | None  # None when the response is not HTML


@dataclass(frozen=True, slots=True)
class ImageResponse:
    url: str
    content_type: str | None
    body: bytes


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    sha256: bytes
    jpeg: bytes
    width: int
    height: int
    original_format: str
    original_width: int
    original_height: int


@dataclass(frozen=True, slots=True)
class Skipped:
    sha256: bytes
    reason: SkipReason
    original_format: str
    original_width: int | None = None
    original_height: int | None = None


@dataclass(frozen=True, slots=True)
class Box:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True, slots=True)
class DetectedFace:
    box: Box
    confidence: float
    embedding: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PendingImage:
    id: int
    jpeg: bytes
    width: int
    height: int


@dataclass(slots=True)
class Summary:
    """Counters returned by every stage and merged by ``summarize`` (FR-025)."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n

    def merge(self, other: "Summary") -> "Summary":
        for key, value in other.counts.items():
            self.add(key, value)
        return self

    def to_json(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))

    @classmethod
    def from_json(cls, data: dict[str, int]) -> "Summary":
        return cls(counts={str(k): int(v) for k, v in data.items()})
