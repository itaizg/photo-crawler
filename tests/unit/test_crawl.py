from face_index import config
from face_index.crawl import CrawlResult, crawl_site
from face_index.http import HttpFetcher
from face_index.urls import in_scope
from tests.fakes import FakeResolver, MockSite, RecordingPacer, Reply, html_reply, robots_reply

SITE = config.Site("cnn", "https://edition.cnn.com/", frozenset({"edition.cnn.com"}))


def page(links: list[str], images: tuple[str, ...] = ()) -> Reply:
    body = "".join(f'<a href="{u}">x</a>' for u in links)
    body += "".join(f'<img src="{u}">' for u in images)
    return html_reply(f"<html><body>{body}</body></html>")


ROUTES = {
    "https://edition.cnn.com/robots.txt": robots_reply("User-agent: *\nDisallow: /private\n"),
    "https://edition.cnn.com/": page(
        ["/a", "/b", "https://www.example.org/off", "/private/x"], ("https://m.cnn.com/home.jpg",)
    ),
    "https://edition.cnn.com/a": page(["/a2"], ("https://m.cnn.com/a.jpg",)),
    "https://edition.cnn.com/b": Reply(200, b"%PDF", {"content-type": "application/pdf"}),
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
    assert result.summary.counts["skipped.page.not_html"] == 1
    assert result.summary.counts["skipped.page.robots_disallowed"] == 1
    assert not site.requested("https://edition.cnn.com/a2")  # depth 2


def test_image_refs_and_counters() -> None:
    result, _ = run(depth=1)
    assert result.pages[0].image_refs == ("https://m.cnn.com/home.jpg",)
    assert result.summary.counts["images_found"] == 2
    assert result.summary.counts["pages_crawled.cnn"] == 2


def test_result_round_trips_through_json() -> None:
    result, _ = run(depth=1)
    again = CrawlResult.from_json(result.to_json())
    assert again.pages == result.pages
    assert again.summary.counts == result.summary.counts
