from collections.abc import Mapping
from typing import Any, Final

import pytest

from face_index import config
from face_index.crawl import CrawlResult, crawl_site
from face_index.http import HttpFetcher
from face_index.urls import in_scope
from tests.fakes import FakeResolver, MockSite, RecordingPacer, Reply, html_reply, robots_reply

SITE: Final[config.Site] = config.Site(
    name="cnn", seed_url="https://edition.cnn.com/", page_hosts=frozenset({"edition.cnn.com"})
)


def page(links: list[str], images: tuple[str, ...] = ()) -> Reply:
    body = "".join(f'<a href="{u}">x</a>' for u in links)
    body += "".join(f'<img src="{u}">' for u in images)
    return html_reply(f"<html><body>{body}</body></html>")


ROUTES: Final[Mapping[str, Reply]] = {
    "https://edition.cnn.com/robots.txt": robots_reply("User-agent: *\nDisallow: /private\n"),
    "https://edition.cnn.com/": page(
        ["/a", "/b", "https://www.example.org/off", "/private/x"], ("https://m.cnn.com/home.jpg",)
    ),
    "https://edition.cnn.com/a": page(["/a2"], ("https://m.cnn.com/a.jpg",)),
    "https://edition.cnn.com/b": Reply(
        status=200, body=b"%PDF", headers={"content-type": "application/pdf"}
    ),
    "https://edition.cnn.com/a2": page([], ("https://m.cnn.com/a2.jpg",)),
}


def run(depth: int, cap: int | None = None) -> tuple[CrawlResult, MockSite]:
    site = MockSite(ROUTES)
    fetcher = HttpFetcher(
        pacer=RecordingPacer(),
        resolver=FakeResolver(),
        transport=site.transport(),
        scope=lambda u: in_scope(u, SITE),
    )
    return crawl_site(SITE, fetcher, depth, cap), site


def test_depth_zero_crawls_only_the_seed() -> None:
    result, _ = run(depth=0)
    assert [p.url for p in result.pages] == ["https://edition.cnn.com/"]


def test_depth_one_is_breadth_first_and_in_scope() -> None:
    result, site = run(depth=1)
    assert [(p.url, p.depth) for p in result.pages] == [
        ("https://edition.cnn.com/", 0),
        ("https://edition.cnn.com/a", 1),
    ]
    assert not site.requested("https://www.example.org/off")
    assert not site.requested("https://edition.cnn.com/private/x")  # robots
    assert site.requested("https://edition.cnn.com/b")  # fetched, but not HTML
    assert result.summary.to_json()["skipped.page.not_html"] == 1
    assert result.summary.to_json()["skipped.page.robots_disallowed"] == 1
    assert not site.requested("https://edition.cnn.com/a2")  # depth 2


def test_image_refs_and_counters() -> None:
    result, _ = run(depth=1)
    assert result.pages[0].image_refs == ("https://m.cnn.com/home.jpg",)
    assert result.summary.to_json()["images_found"] == 2
    assert result.summary.to_json()["pages_crawled.cnn"] == 2


def test_result_round_trips_through_json() -> None:
    result, _ = run(depth=1)
    again = CrawlResult.from_json(result.to_json())
    assert again.pages == result.pages
    assert again.summary == result.summary


def crawl_routes(
    routes: dict[str, Reply], depth: int, cap: int | None, **hooks: Any
) -> tuple[CrawlResult, MockSite]:
    site = MockSite({"https://edition.cnn.com/robots.txt": robots_reply(), **routes})
    fetcher = HttpFetcher(
        pacer=RecordingPacer(),
        resolver=FakeResolver(),
        transport=site.transport(),
        scope=lambda u: in_scope(u, SITE),
    )
    return crawl_site(SITE, fetcher, depth, cap, **hooks), site


def test_failed_and_non_html_requests_count_toward_the_cap() -> None:
    result, site = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/pdf", "/gone", "/a"]),
            "https://edition.cnn.com/pdf": Reply(
                status=200, body=b"%PDF", headers={"content-type": "application/pdf"}
            ),
            "https://edition.cnn.com/a": page([]),
        },
        depth=1,
        cap=3,
    )
    assert site.requested("https://edition.cnn.com/gone")  # 404: a sent request, a dead link
    assert not site.requested("https://edition.cnn.com/a")  # cap of 3 requests reached
    assert result.summary.to_json()["pages_left_by_cap.cnn"] == 1


@pytest.mark.parametrize("status", [404, 410])
def test_a_dead_page_is_counted_and_recorded_with_its_status(status: int) -> None:
    dead: list[tuple[str, int]] = []
    result, _ = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/gone"]),
            "https://edition.cnn.com/gone": Reply(status=status),
        },
        depth=1,
        cap=None,
        on_dead=lambda url, code: dead.append((url, code)),
    )
    assert dead == [("https://edition.cnn.com/gone", status)]
    summary = result.summary.to_json()
    assert summary["dead_link.page"] == 1
    assert not any(k.startswith(("skipped.", "failures.")) for k in summary)


def test_a_known_dead_page_is_not_requested_and_uses_no_cap_budget() -> None:
    known = "https://edition.cnn.com/gone"
    result, site = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/gone", "/a"]),
            known: Reply(status=404),
            "https://edition.cnn.com/a": page([]),
        },
        depth=1,
        cap=2,  # the home page and /a: the known dead link must not take a slot
        is_dead=lambda url: url == known,
    )
    assert not site.requested(known)
    assert site.requested("https://edition.cnn.com/a")
    assert result.summary.to_json()["known_dead.page"] == 1


def test_malformed_urls_are_skipped_not_fatal() -> None:
    result, _ = crawl_routes(
        {
            "https://edition.cnn.com/": page(
                ["https://edition.cnn.com:99999/x", "/a"],
                ("https://m.cnn.com:bad/x.jpg", "/ok.jpg"),
            ),
            "https://edition.cnn.com/a": page([]),
        },
        depth=1,
        cap=None,
    )
    assert [p.url for p in result.pages] == [
        "https://edition.cnn.com/",
        "https://edition.cnn.com/a",
    ]
    assert result.pages[0].image_refs == ("https://edition.cnn.com/ok.jpg",)


def test_redirected_page_is_stored_once_under_its_final_url() -> None:
    result, site = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/old", "/new"]),
            "https://edition.cnn.com/old": Reply(status=301, headers={"location": "/new"}),
            "https://edition.cnn.com/new": page([]),
        },
        depth=1,
        cap=None,
    )
    assert [p.url for p in result.pages] == [
        "https://edition.cnn.com/",
        "https://edition.cnn.com/new",
    ]
    assert sum(u == "https://edition.cnn.com/new" for u, _ in site.requests) == 1


def test_a_redirect_refused_after_its_first_hop_counts_toward_the_cap() -> None:
    result, site = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/out", "/a"]),
            "https://edition.cnn.com/out": Reply(
                status=302, headers={"location": "https://x.org/"}
            ),
            "https://edition.cnn.com/a": page([]),
        },
        depth=1,
        cap=2,
    )
    assert site.requested("https://edition.cnn.com/out")
    assert not site.requested("https://edition.cnn.com/a")  # home + /out used the 2 requests
    assert result.summary.to_json()["skipped.page.out_of_scope"] == 1


def test_a_crawl_where_every_request_failed_records_and_counts_them() -> None:
    failures: list[str] = []
    site = MockSite(
        {
            "https://edition.cnn.com/robots.txt": robots_reply(),
            "https://edition.cnn.com/": Reply(status=500),
        }
    )
    fetcher = HttpFetcher(
        pacer=RecordingPacer(),
        resolver=FakeResolver(),
        transport=site.transport(),
        scope=lambda u: in_scope(u, SITE),
    )
    result = crawl_site(SITE, fetcher, 1, None, on_failure=lambda url, reason: failures.append(url))
    assert failures == ["https://edition.cnn.com/"]
    assert result.pages == ()
    assert result.summary.to_json() == {"failures.crawl": 1}  # pipeline.crawl fails the task


def test_a_failed_page_is_counted_and_the_crawl_goes_on() -> None:
    result, _ = crawl_routes(
        {
            "https://edition.cnn.com/": page(["/broken"]),
            "https://edition.cnn.com/broken": Reply(status=500),
        },
        depth=1,
        cap=None,
    )
    assert result.summary.to_json()["failures.crawl"] == 1
    assert [p.url for p in result.pages] == ["https://edition.cnn.com/"]


def test_a_crawl_with_nothing_requested_does_not_fail() -> None:
    site = MockSite({"https://edition.cnn.com/robots.txt": robots_reply()})
    fetcher = HttpFetcher(
        pacer=RecordingPacer(),
        resolver=FakeResolver(),
        transport=site.transport(),
        scope=lambda u: in_scope(u, SITE),
    )
    result = crawl_site(SITE, fetcher, 1, None, is_abandoned=lambda url: True)
    assert result.summary.to_json()["abandoned.page"] == 1
