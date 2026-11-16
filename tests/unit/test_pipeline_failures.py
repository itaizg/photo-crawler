"""Failure isolation and logging in the pipeline (FR-023, FR-024, FR-025, research R12).

Uses an in-memory fake store, so these tests need no database.
"""

import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Final, LiteralString

import httpx
import pytest

from face_index import pipeline
from face_index.crawl import CrawlResult
from face_index.errors import InfrastructureError, ItemsFailed, StageFailed
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
from face_index.urls import in_scope
from tests import images
from tests.fakes import FakeEmbedder, FakeResolver, MockSite, RecordingPacer, Reply, robots_reply

SITE: Final[pipeline.config.Site] = pipeline.config.Site(
    name="cnn", seed_url="https://news.test/", page_hosts=frozenset({"news.test"})
)
JPEG: Final[Mapping[str, str]] = {"content-type": "image/jpeg"}


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

    def clock(self) -> datetime:
        return datetime(2026, 1, 1, tzinfo=UTC)

    def upsert_page(self, url: str, site: str, depth: int) -> int:
        self._maybe_fail("upsert_page")
        return 1

    def finish_page(self, page_id: int, started_at: datetime) -> None:
        self._maybe_fail("finish_page")

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
        self,
        image_id: int,
        detect: Callable[[PendingImage], list[DetectedFace]],
        profile_id: int,
        *,
        wait: bool = False,
    ) -> int | None:
        return len(
            detect(PendingImage(id=image_id, jpeg=images.photo(64, 64), width=64, height=64))
        )

    def stored_faces(self, image_id: int) -> int | None:
        return None

    def outdated_links(self, page_id: int) -> int:
        return self.outdated


def deps(
    store: MemoryStore,
    routes: Mapping[str, Reply],
    embedder: FaceEmbedder | None = None,
    scope: Callable[[str], bool] | None = None,
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
        scope=scope,
    )
    return Deps(
        store=store,
        fetcher=fetcher,
        normalizer=PillowNormalizer(),
        embedder=embedder or FakeEmbedder(default_faces=1),
    )


def crawl_payload(refs: list[str]) -> dict[str, object]:
    page = CrawledPage(url="https://news.test/", depth=0, image_refs=tuple(refs), lead_ref=None)
    return CrawlResult(site="cnn", pages=[page]).to_json()


def failed_download(payload: Mapping[str, object], the_deps: Deps) -> Mapping[str, object]:
    """Run a download in which items fail: the task fails, and carries its full result."""
    with pytest.raises(ItemsFailed, match=r"download for cnn: \d+ item\(s\) failed") as raised:
        pipeline.download(SITE, payload, the_deps, "run-1")
    return raised.value.result


GOOD: Final[LiteralString] = "https://img.test/good.jpg"
ROUTES: Final[Mapping[str, Reply]] = {
    GOOD: Reply(status=200, body=images.photo(100, 100), headers=JPEG),
    "https://img.test/500.jpg": Reply(status=500),
    "https://img.test/slow.jpg": Reply(error=httpx.ReadTimeout),
    "https://img.test/corrupt.jpg": Reply(status=200, body=images.corrupt_jpeg(), headers=JPEG),
    "https://img.test/huge.jpg": Reply(
        status=200, body=b"x", headers={"content-length": "999999", **JPEG}
    ),
    "https://img.test/redirect.jpg": Reply(
        status=302, headers={"location": "https://internal.test/x.jpg"}
    ),
    "https://img.test/brand.svg": Reply(
        status=200, body=images.SVG, headers={"content-type": "image/svg+xml"}
    ),
    "https://img.test/icon.jpg": Reply(status=200, body=images.photo(40, 40), headers=JPEG),
}
FAILING: Final[Mapping[str, str]] = {
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
    counts = failed_download(crawl_payload(refs), deps(store, ROUTES))

    assert counts["failures.download"] == len(FAILING)
    assert counts["staged"] == 1  # the healthy image still made it
    for url, reason in FAILING.items():
        assert f"image failed url={url} stage=download reason={reason}" in caplog.text
    assert {u for u, _, _ in store.failures} == set(FAILING)
    assert "image skipped url=https://img.test/brand.svg reason=unsupported_format" in caplog.text
    assert "image skipped url=https://img.test/icon.jpg reason=too_small" in caplog.text
    assert counts["skipped.unsupported_format"] == 1
    assert counts["skipped.too_small"] == 1


@pytest.mark.parametrize("where", ["upsert_page", "find_image_by_sha256", "finish_page"])
def test_infrastructure_errors_fail_the_task(where: str) -> None:
    store = MemoryStore(fail_on=where)
    with pytest.raises(InfrastructureError):
        pipeline.download(SITE, crawl_payload([GOOD]), deps(store, ROUTES), "run-1")
    assert store.failures == []  # never counted against the link


def test_detection_failure_is_counted_and_recorded(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryStore()
    failing = FakeEmbedder(default_faces=1, fail_on={""})  # every image fails
    with pytest.raises(StageFailed, match="all 2 images"):  # nothing succeeded: Airflow retries
        pipeline.detect([7, 8], 1, deps(store, {}, failing), "run-1")
    assert store.detect_failures == [7, 8]  # recorded before the task fails
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
    with pytest.raises(ItemsFailed) as raised:  # a download failure: logged, then the run fails
        pipeline.summarize(
            [
                {
                    "pages_crawled.cnn": 3,
                    "images_found": 10,
                    "failures.download": 1,
                    "skipped.too_small": 2,
                },
                {"pages_crawled.people": 4, "images_found": 5, "abandoned.image": 1},
                {"images_indexed": 4, "faces_stored": 6, "failures.detect": 1},
            ]
        )
    total = raised.value.result
    assert total["images_found"] == 15
    assert total["faces_stored"] == 6
    assert total["skipped.too_small"] == 2
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
    # exact rows: the skipped total and its reason, each with its own count
    assert re.search(r"^skipped\s+2$", caplog.text, re.MULTILINE)
    assert re.search(r"^  too_small\s+2$", caplog.text, re.MULTILINE)
    assert re.search(r"^  people\s+4$", caplog.text, re.MULTILINE)


def test_an_image_that_failed_is_not_requested_again_in_the_same_task() -> None:
    store = MemoryStore()
    broken = "https://img.test/500.jpg"
    pages = [
        CrawledPage(url=f"https://news.test/{i}", depth=1, image_refs=(broken,), lead_ref=None)
        for i in range(3)
    ]
    payload = CrawlResult(site="cnn", pages=pages).to_json()
    pages.append(
        CrawledPage(url="https://news.test/ok", depth=1, image_refs=(GOOD,), lead_ref=None)
    )
    payload = CrawlResult(site="cnn", pages=pages).to_json()
    the_deps = deps(store, ROUTES)
    counts = failed_download(payload, the_deps)
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
            *,
            wait: bool = False,
        ) -> int | None:
            return None

        def stored_faces(self, image_id: int) -> int | None:
            return 3

    counts = pipeline.detect([1], 1, deps(AlreadyDone(), {}), "run-1")
    assert counts == {"detect.recovered": 1, "faces_stored": 3, "images_indexed": 1}


@pytest.mark.parametrize("rate", [0.0, 0.01, 1e-9])
def test_rates_below_the_floor_are_rejected(rate: float) -> None:
    with pytest.raises(ValueError, match="page_rate"):
        pipeline.RunParams.model_validate({"page_rate": rate})
    with pytest.raises(ValueError, match="image_rate"):
        pipeline.RunParams.model_validate({"image_rate": rate})


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


def test_one_failed_image_request_fails_the_download_task_after_the_rest_is_done() -> None:
    store = MemoryStore()
    refs = ["https://img.test/500.jpg", GOOD]  # the failure comes first: the good one still runs
    with pytest.raises(ItemsFailed, match=r"download for cnn: 1 item\(s\) failed") as raised:
        pipeline.download(SITE, crawl_payload(refs), deps(store, ROUTES), "run-1")
    assert [u for u, _, _ in store.failures] == ["https://img.test/500.jpg"]
    assert raised.value.result["staged"] == 1  # published by the DAG, so detection still runs
    assert isinstance(raised.value, StageFailed)  # Airflow marks the task failed


def crawl_deps(routes: Mapping[str, Reply], store: MemoryStore | None = None) -> Deps:
    routes = {"https://news.test/robots.txt": robots_reply(), **routes}
    return deps(store or MemoryStore(), routes, scope=lambda url: in_scope(url, SITE))


def linking_home(path: str) -> Reply:
    return Reply(
        status=200,
        body=f'<html><body><a href="{path}">x</a></body></html>'.encode(),
        headers={"content-type": "text/html"},
    )


def test_one_failed_page_request_fails_the_crawl_task_and_keeps_its_result() -> None:
    routes = {
        "https://news.test/": linking_home("/broken"),
        "https://news.test/broken": Reply(status=500),
    }
    with pytest.raises(ItemsFailed, match=r"crawl of cnn: 1 page request\(s\) failed") as raised:
        pipeline.crawl(SITE, pipeline.RunParams(depth=1), crawl_deps(routes), "run-1")
    result = CrawlResult.from_json(raised.value.result)
    assert [p.url for p in result.pages] == ["https://news.test/"]
    assert result.summary.to_json()["failures.crawl"] == 1


@pytest.mark.parametrize("status", [404, 410])
def test_a_dead_link_is_a_skip_and_does_not_fail_the_crawl(status: int) -> None:
    store = MemoryStore()
    routes = {
        "https://news.test/": linking_home("/gone"),
        "https://news.test/gone": Reply(status=status),
    }
    payload = pipeline.crawl(SITE, pipeline.RunParams(depth=1), crawl_deps(routes, store), "run-1")
    summary = CrawlResult.from_json(payload).summary.to_json()
    assert summary["skipped.page.not_found"] == 1
    assert "failures.crawl" not in summary
    assert store.failures == []


def test_a_dead_image_link_is_a_skip_and_does_not_fail_the_download() -> None:
    store = MemoryStore()
    refs = ["https://img.test/missing.jpg", GOOD]  # not routed: the mock site answers 404
    counts = pipeline.download(SITE, crawl_payload(refs), deps(store, ROUTES), "run-1")
    assert counts["skipped.not_found"] == 1
    assert counts["staged"] == 1
    assert store.failures == []


def test_a_refused_site_fails_the_crawl_task() -> None:
    store = MemoryStore()
    the_deps = crawl_deps({"https://news.test/": Reply(status=402)}, store)
    with pytest.raises(ItemsFailed, match="crawl of cnn: 1 page"):
        pipeline.crawl(SITE, pipeline.RunParams(depth=0), the_deps, "run-1")
    assert store.failures == [("https://news.test/", "page", "http 402")]


def test_a_clean_crawl_returns_its_result() -> None:
    home = Reply(status=200, body=b"<html></html>", headers={"content-type": "text/html"})
    payload = pipeline.crawl(
        SITE, pipeline.RunParams(depth=0), crawl_deps({"https://news.test/": home}), "run-1"
    )
    assert CrawlResult.from_json(payload).summary.to_json() == {
        "images_found": 0,
        "pages_crawled.cnn": 1,
    }


@pytest.mark.parametrize("counter", pipeline.FAILING_COUNTERS)
def test_summary_fails_the_run_when_a_request_failed(
    counter: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(ItemsFailed, match=r"2 page or image item\(s\) failed") as raised:
        pipeline.summarize([{counter: 2, "staged": 5}])
    assert "run summary" in caplog.text  # logged before the task fails
    assert raised.value.result == {counter: 2, "staged": 5}


def test_a_download_with_only_skips_does_not_fail() -> None:
    counts = pipeline.download(
        SITE, crawl_payload(["https://img.test/brand.svg"]), deps(MemoryStore(), ROUTES), "run-1"
    )
    assert counts["skipped.unsupported_format"] == 1


def test_plain_http_images_are_skipped_not_failed() -> None:
    store = MemoryStore()
    counts = pipeline.download(
        SITE, crawl_payload(["http://img.test/good.jpg", GOOD]), deps(store, ROUTES), "run-1"
    )
    assert counts["skipped.insecure_scheme"] == 1
    assert store.failures == []


def test_reuse_by_url_clears_a_stale_failure() -> None:
    class Known(MemoryStore):
        def __init__(self) -> None:
            super().__init__()
            self.by_url[GOOD] = 5
            self.successes: list[str] = []

        def record_success(self, url: str, kind: RequestKind) -> None:
            self.successes.append(url)

    store = Known()
    counts = pipeline.download(SITE, crawl_payload([GOOD]), deps(store, {}), "run-1")
    assert counts["reused_by_url"] == 1
    assert store.successes == [GOOD]


def test_signed_urls_are_masked_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    signed = "https://img.test/500.jpg?token=s3cr3t"
    routes = {**ROUTES, signed: Reply(status=500)}
    failed_download(crawl_payload([signed, GOOD]), deps(MemoryStore(), routes))
    assert "s3cr3t" not in caplog.text
    assert "image failed url=https://img.test/500.jpg?token=… stage=download" in caplog.text


class Locked(MemoryStore):
    """The image is locked by another worker on the first, non-waiting try."""

    def __init__(self, after_wait: int | None, done_after_wait: bool) -> None:
        super().__init__()
        self.after_wait = after_wait
        self.done_after_wait = done_after_wait
        self.waited = False

    def process_pending(
        self,
        image_id: int,
        detect: Callable[[PendingImage], list[DetectedFace]],
        profile_id: int,
        *,
        wait: bool = False,
    ) -> int | None:
        if not wait:
            return None
        self.waited = True
        return self.after_wait

    def stored_faces(self, image_id: int) -> int | None:
        return 0 if self.waited and self.done_after_wait else None


@pytest.mark.parametrize(
    ("store", "expected"),
    [
        (Locked(after_wait=2, done_after_wait=False), {"images_indexed": 1, "faces_stored": 2}),
        (Locked(after_wait=None, done_after_wait=True), {"detect.already_done": 1}),
        (Locked(after_wait=None, done_after_wait=False), {"detect.locked_elsewhere": 1}),
    ],
    ids=["holder-failed-we-process", "holder-finished", "lock-timeout"],
)
def test_a_locked_image_is_waited_for_not_counted_as_done(
    store: Locked, expected: dict[str, int]
) -> None:
    assert pipeline.detect([1], 1, deps(store, {}), "run-1") == expected
    assert store.waited


@pytest.mark.parametrize("name", ["depth", "page_cap"])
def test_fractional_limits_are_rejected(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        pipeline.RunParams.model_validate({name: 1.5})


def test_summary_logs_failed_stages_then_fails(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(StageFailed, match="1 stage"):
        pipeline.summarize([{"pages_crawled.cnn": 2, "pages_left_by_cap.cnn": 7}, None])
    assert "pages crawled" in caplog.text
    assert "pages left by the cap" in caplog.text
    assert "stages with no result (failed)" in caplog.text


def test_summary_shows_pages_left_by_the_cap(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    pipeline.summarize([{"pages_left_by_cap.people": 4}])
    assert "pages left by the cap" in caplog.text
    assert "people" in caplog.text
