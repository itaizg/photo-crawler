"""Failure isolation and logging in the pipeline (FR-023, FR-024, FR-025, research R12).

Uses an in-memory fake store, so these tests need no database.
"""

import logging
import re
from collections.abc import Callable

import httpx
import pytest

from face_index import pipeline
from face_index.crawl import CrawlResult
from face_index.errors import InfrastructureError
from face_index.http import HttpFetcher
from face_index.models import (
    CrawledPage,
    DetectedFace,
    NormalizedImage,
    PendingImage,
    RequestKind,
    Skipped,
)
from face_index.normalize import PillowNormalizer
from face_index.pipeline import Deps
from face_index.ports import FaceEmbedder
from tests import images
from tests.fakes import FakeEmbedder, FakeResolver, MockSite, RecordingPacer, Reply, robots_reply

SITE = pipeline.config.Site("cnn", "https://news.test/", frozenset({"news.test"}))
JPEG = {"content-type": "image/jpeg"}


class MemoryStore:
    """Just enough of IndexStore for download/detect; records failures."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.failures: list[tuple[str, RequestKind, str]] = []
        self.detect_failures: list[int] = []
        self.by_url: dict[str, int] = {}
        self.by_sha: dict[bytes, int] = {}
        self.fail_on = fail_on
        self.outdated = 0  # what outdated_links reports for every page (FR-018)

    def _maybe_fail(self, what: str) -> None:
        if self.fail_on == what:
            raise InfrastructureError("database unavailable: OperationalError")

    def upsert_page(self, url: str, site: str, depth: int) -> int:
        self._maybe_fail("upsert_page")
        return 1

    def init_schema(self) -> int:
        return 1

    def reset_lead(self, page_id: int) -> None: ...

    def link(self, page_id: int, image_id: int, is_lead: bool) -> None: ...

    def add_url_and_link(self, image_id: int, url: str, page_id: int, is_lead: bool) -> None: ...

    def pending_batches(self, batch_size: int, max_attempts: int) -> list[list[int]]:
        return []

    def abandoned_pending(self, max_attempts: int) -> int:
        return 0

    def find_image_by_url(self, url: str) -> int | None:
        return self.by_url.get(url)

    def find_image_by_sha256(self, sha256: bytes) -> int | None:
        self._maybe_fail("find_image_by_sha256")
        return self.by_sha.get(sha256)

    def stage_image(
        self, image: NormalizedImage | Skipped, url: str, page_id: int, is_lead: bool
    ) -> tuple[int, bool]:
        image_id = len(self.by_sha) + 1
        self.by_sha[image.sha256] = image_id
        self.by_url[url] = image_id
        return image_id, True

    def is_abandoned(self, url: str, kind: RequestKind, max_attempts: int) -> bool:
        return False

    def record_failure(self, url: str, kind: RequestKind, error: str, run_id: str) -> None:
        self.failures.append((url, kind, error))

    def record_success(self, url: str, kind: RequestKind) -> None: ...

    def record_detect_failure(self, image_id: int, run_id: str) -> None:
        self.detect_failures.append(image_id)

    def process_pending(
        self, image_id: int, detect: Callable[[PendingImage], list[DetectedFace]], profile_id: int
    ) -> int | None:
        return len(detect(PendingImage(image_id, images.photo(64, 64), 64, 64)))

    def stored_faces(self, image_id: int) -> int | None:
        return None

    def outdated_links(self, page_id: int) -> int:
        return self.outdated


def deps(
    store: MemoryStore, routes: dict[str, Reply], embedder: FaceEmbedder | None = None
) -> Deps:
    site = MockSite(
        {
            "https://img.test/robots.txt": robots_reply(),
            "https://other.test/robots.txt": robots_reply(),
            **routes,
        }
    )
    fetcher = HttpFetcher(
        pacer=RecordingPacer(),
        resolver=FakeResolver({"internal.test": ["10.0.0.5"]}),
        transport=site.transport(),
        max_download_bytes=10_000,
    )
    return Deps(
        store=store,
        fetcher=fetcher,
        normalizer=PillowNormalizer(),
        embedder=embedder or FakeEmbedder(default_faces=1),
    )


def crawl_payload(refs: list[str]) -> dict[str, object]:
    page = CrawledPage("https://news.test/", 0, tuple(refs), None)
    return CrawlResult(site="cnn", pages=[page]).to_json()


GOOD = "https://img.test/good.jpg"
ROUTES = {
    GOOD: Reply(200, images.photo(100, 100), JPEG),
    "https://img.test/500.jpg": Reply(500),
    "https://img.test/slow.jpg": Reply(error=httpx.ReadTimeout),
    "https://img.test/corrupt.jpg": Reply(200, images.corrupt_jpeg(), JPEG),
    "https://img.test/huge.jpg": Reply(200, b"x", {"content-length": "999999", **JPEG}),
    "https://img.test/redirect.jpg": Reply(
        302, headers={"location": "https://internal.test/x.jpg"}
    ),
    "https://img.test/brand.svg": Reply(200, images.SVG, {"content-type": "image/svg+xml"}),
    "https://img.test/icon.jpg": Reply(200, images.photo(40, 40), JPEG),
}
FAILING = {
    "https://img.test/500.jpg": "http 500",
    "https://img.test/slow.jpg": "timeout",
    "https://img.test/corrupt.jpg": "decode error",
    "https://img.test/huge.jpg": "download too large",
    "https://img.test/redirect.jpg": "non-public address",
}


def test_item_failures_are_logged_counted_and_isolated(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    store = MemoryStore()
    refs = [*FAILING, "https://img.test/brand.svg", "https://img.test/icon.jpg", GOOD]
    counts = pipeline.download(SITE, crawl_payload(refs), deps(store, ROUTES), "run-1")

    assert counts["failures.download"] == len(FAILING)
    assert counts["staged"] == 1  # the healthy image still made it
    for url, reason in FAILING.items():
        assert f"image failed url={url} stage=download reason={reason}" in caplog.text
    assert {u for u, _, _ in store.failures} == set(FAILING)
    assert "image skipped url=https://img.test/brand.svg reason=unsupported_format" in caplog.text
    assert "image skipped url=https://img.test/icon.jpg reason=too_small" in caplog.text
    assert counts["skipped.unsupported_format"] == 1
    assert counts["skipped.too_small"] == 1


@pytest.mark.parametrize("where", ["upsert_page", "find_image_by_sha256"])
def test_infrastructure_errors_fail_the_task(where: str) -> None:
    store = MemoryStore(fail_on=where)
    with pytest.raises(InfrastructureError):
        pipeline.download(SITE, crawl_payload([GOOD]), deps(store, ROUTES), "run-1")
    assert store.failures == []  # never counted against the link


def test_detection_failure_is_counted_and_recorded(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryStore()
    failing = FakeEmbedder(default_faces=1, fail_on={""})  # every image fails
    counts = pipeline.detect([7, 8], 1, deps(store, {}, failing), "run-1")
    assert counts["failures.detect"] == 2
    assert store.detect_failures == [7, 8]
    assert "image failed id=7 stage=detect" in caplog.text


def test_model_unavailable_fails_the_task() -> None:
    class BrokenModel:
        def embed(self, jpeg: bytes) -> list[DetectedFace]:
            raise InfrastructureError("face model unavailable: OSError")

    store = MemoryStore()
    with pytest.raises(InfrastructureError):
        pipeline.detect([1], 1, deps(store, {}, BrokenModel()), "run-1")
    assert store.detect_failures == []


def test_summary_merges_and_logs_every_counter(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    total = pipeline.summarize(
        [
            {
                "pages_crawled.cnn": 3,
                "images_found": 10,
                "failures.download": 1,
                "skipped.too_small": 2,
            },
            {"pages_crawled.people": 2, "images_found": 5, "abandoned.image": 1},
            {"images_indexed": 4, "faces_stored": 6, "failures.detect": 1},
        ]
    )
    assert total["images_found"] == 15
    assert total["faces_stored"] == 6
    for label in (
        "pages crawled",
        "images found",
        "skipped",
        "newly indexed",
        "faces stored",
        "failures",
        "abandoned",
    ):
        assert label in caplog.text
    assert "    2" in caplog.text or "2" in caplog.text


def test_an_image_that_failed_is_not_requested_again_in_the_same_task() -> None:
    store = MemoryStore()
    broken = "https://img.test/500.jpg"
    pages = [CrawledPage(f"https://news.test/{i}", 1, (broken,), None) for i in range(3)]
    payload = CrawlResult(site="cnn", pages=pages).to_json()
    the_deps = deps(store, ROUTES)
    counts = pipeline.download(SITE, payload, the_deps, "run-1")
    assert counts["failures.download"] == 1
    assert counts["skipped.failed_earlier_in_run"] == 2
    assert len(store.failures) == 1


def test_detection_batches_per_run_stay_under_the_mapping_limit() -> None:
    class Backlog(MemoryStore):
        def pending_batches(self, batch_size: int, max_attempts: int) -> list[list[int]]:
            return [[i] for i in range(pipeline.config.MAX_DETECT_BATCHES + 5)]

        def abandoned_pending(self, max_attempts: int) -> int:
            return 0

    batches = pipeline.plan_batches(deps(Backlog(), {}))
    assert len(batches) == pipeline.config.MAX_DETECT_BATCHES


def test_a_redelivered_detection_recovers_its_counts() -> None:
    class AlreadyDone(MemoryStore):
        def process_pending(
            self,
            image_id: int,
            detect: Callable[[PendingImage], list[DetectedFace]],
            profile_id: int,
        ) -> int | None:
            return None

        def stored_faces(self, image_id: int) -> int | None:
            return 3

    counts = pipeline.detect([1], 1, deps(AlreadyDone(), {}), "run-1")
    assert counts == {"detect.recovered": 1, "faces_stored": 3, "images_indexed": 1}


@pytest.mark.parametrize("rate", [0.0, 0.01, 1e-9])
def test_rates_below_the_floor_are_rejected(rate: float) -> None:
    with pytest.raises(ValueError, match="page_rate"):
        pipeline.RunParams.from_mapping({"page_rate": rate})
    with pytest.raises(ValueError, match="image_rate"):
        pipeline.RunParams.from_mapping({"image_rate": rate})


# -- FR-018: outdated page links are reported, never deleted ----------------------------------


def test_download_logs_and_counts_outdated_links(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    store = MemoryStore()
    store.outdated = 2
    counts = pipeline.download(SITE, crawl_payload([GOOD]), deps(store, ROUTES), "run-1")
    assert counts["links.outdated"] == 2
    assert "outdated links page=https://news.test/ count=2" in caplog.text


def test_no_outdated_links_means_no_log_and_no_counter(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    counts = pipeline.download(SITE, crawl_payload([GOOD]), deps(MemoryStore(), ROUTES), "run-1")
    assert "links.outdated" not in counts
    assert "outdated links" not in caplog.text


def test_summary_shows_outdated_links(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    pipeline.summarize([{"links.outdated": 2}, {"links.outdated": 3}])
    assert re.search(r"outdated page links \(ignored by search\)\s+5", caplog.text)
