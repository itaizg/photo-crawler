import httpx
import pytest

from face_index import config
from face_index.errors import Disallowed, FetchRefused, ItemError
from face_index.http import HttpFetcher
from face_index.urls import in_scope
from tests.fakes import (
    PUBLIC_IP,
    FakeResolver,
    MockSite,
    RecordingPacer,
    Reply,
    html_reply,
    robots_reply,
)

CNN = config.SITES[0]
HOME = "https://edition.cnn.com/"
IMG = "https://media.cnn.com/a.jpg"


def make(
    routes: dict[str, Reply],
    resolver: FakeResolver | None = None,
    max_download: int = config.MAX_DOWNLOAD_BYTES,
) -> tuple[HttpFetcher, MockSite, RecordingPacer]:
    site = MockSite(routes)
    pacer = RecordingPacer()
    fetcher = HttpFetcher(
        pacer=pacer,
        resolver=resolver or FakeResolver(),
        transport=site.transport(),
        scope=lambda url: in_scope(url, CNN),
        max_download_bytes=max_download,
    )
    return fetcher, site, pacer


ROBOTS = {
    "https://edition.cnn.com/robots.txt": robots_reply("User-agent: *\nDisallow: /private\n"),
    "https://media.cnn.com/robots.txt": robots_reply(),
}


def test_page_fetch_sets_headers_and_pins_ip() -> None:
    fetcher, site, pacer = make({**ROBOTS, HOME: html_reply("<html></html>")})
    page = fetcher.get_page(HOME)
    assert page.text == "<html></html>"
    _, request = next((u, r) for u, r in site.requests if u == HOME)
    assert request.url.host == PUBLIC_IP
    assert request.headers["host"] == "edition.cnn.com"
    assert request.extensions["sni_hostname"] == "edition.cnn.com"
    assert request.headers["user-agent"] == config.USER_AGENT
    assert ("edition.cnn.com", "page") in pacer.calls


def test_image_accept_header_prefers_decodable_formats() -> None:
    fetcher, site, _ = make(
        {**ROBOTS, IMG: Reply(status=200, body=b"x", headers={"content-type": "image/jpeg"})}
    )
    fetcher.get_image(IMG)
    _, request = next((u, r) for u, r in site.requests if u == IMG)
    assert request.headers["accept"] == config.IMAGE_ACCEPT


def test_non_html_page_returns_no_text() -> None:
    fetcher, _, _ = make(
        {
            **ROBOTS,
            HOME: Reply(status=200, body=b"%PDF", headers={"content-type": "application/pdf"}),
        }
    )
    assert fetcher.get_page(HOME).text is None


def test_robots_disallow_blocks_without_request() -> None:
    url = "https://edition.cnn.com/private/x"
    fetcher, site, _ = make({**ROBOTS, url: html_reply("secret")})
    with pytest.raises(Disallowed):
        fetcher.get_page(url)
    assert not site.requested(url)


def test_robots_fetched_once_per_host() -> None:
    fetcher, site, _ = make(
        {**ROBOTS, HOME: html_reply("a"), "https://edition.cnn.com/b": html_reply("b")}
    )
    fetcher.get_page(HOME)
    fetcher.get_page("https://edition.cnn.com/b")
    robots_requests = [u for u, _ in site.requests if u.endswith("/robots.txt")]
    assert robots_requests == ["https://edition.cnn.com/robots.txt"]


def test_robots_404_allows_all() -> None:
    fetcher, _, _ = make({HOME: html_reply("ok")})
    assert fetcher.get_page(HOME).text == "ok"


@pytest.mark.parametrize("reply", [Reply(status=503), Reply(error=httpx.ConnectError)])
def test_robots_5xx_or_network_error_disallows_host(reply: Reply) -> None:
    fetcher, site, _ = make({"https://edition.cnn.com/robots.txt": reply, HOME: html_reply("ok")})
    with pytest.raises(Disallowed):
        fetcher.get_page(HOME)
    assert not site.requested(HOME)


def test_redirect_hops_are_checked_and_followed() -> None:
    target = "https://edition.cnn.com/final"
    fetcher, _, pacer = make(
        {
            **ROBOTS,
            HOME: Reply(status=301, headers={"location": "/final"}),
            target: html_reply("done"),
        }
    )
    page = fetcher.get_page(HOME)
    assert page.url == target
    assert pacer.calls.count(("edition.cnn.com", "page")) == 3  # robots, home, final


def test_page_redirect_out_of_scope_is_not_followed() -> None:
    elsewhere = "https://www.example.org/x"
    fetcher, site, _ = make({**ROBOTS, HOME: Reply(status=302, headers={"location": elsewhere})})
    with pytest.raises(Disallowed):
        fetcher.get_page(HOME)
    assert not site.requested(elsewhere)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1", "::1", "192.168.1.1"]
)
def test_redirect_to_non_public_address_is_refused_before_request(ip: str) -> None:
    resolver = FakeResolver({"internal.example": [ip]})
    evil = "https://internal.example/x.jpg"
    fetcher, site, _ = make(
        {
            **ROBOTS,
            IMG: Reply(status=302, headers={"location": evil}),
            evil: Reply(status=200, body=b"x"),
        },
        resolver=resolver,
    )
    with pytest.raises(FetchRefused, match="non-public"):
        fetcher.get_image(IMG)
    assert not site.requested(evil)


def test_literal_private_ip_is_refused() -> None:
    fetcher, site, _ = make({})
    with pytest.raises(FetchRefused):
        fetcher.get_image("http://10.0.0.5/x.jpg")
    assert site.requests == []


def test_any_private_address_in_dns_answer_is_refused() -> None:
    resolver = FakeResolver({"media.cnn.com": [PUBLIC_IP, "10.1.1.1"]})
    fetcher, _, _ = make({**ROBOTS, IMG: Reply(status=200, body=b"x")}, resolver=resolver)
    with pytest.raises(FetchRefused):
        fetcher.get_image(IMG)


@pytest.mark.parametrize("url", ["ftp://media.cnn.com/a.jpg", "https://media.cnn.com:8443/a.jpg"])
def test_only_http_https_on_default_ports(url: str) -> None:
    fetcher, _, _ = make({})
    with pytest.raises(FetchRefused):
        fetcher.get_image(url)


def test_download_limit_by_content_length() -> None:
    reply = Reply(status=200, body=b"x" * 10, headers={"content-length": "999999"})
    fetcher, _, _ = make({**ROBOTS, IMG: reply}, max_download=100)
    with pytest.raises(FetchRefused, match="too large"):
        fetcher.get_image(IMG)


def test_download_limit_while_streaming() -> None:
    fetcher, _, _ = make({**ROBOTS, IMG: Reply(status=200, body=b"x" * 500)}, max_download=100)
    with pytest.raises(FetchRefused, match="too large"):
        fetcher.get_image(IMG)


def test_http_error_and_timeout_are_item_errors() -> None:
    fetcher, _, _ = make({**ROBOTS, IMG: Reply(status=500), HOME: Reply(error=httpx.ReadTimeout)})
    with pytest.raises(ItemError, match="http 500"):
        fetcher.get_image(IMG)
    with pytest.raises(ItemError, match="timeout"):
        fetcher.get_page(HOME)


def test_too_many_redirects() -> None:
    loop = Reply(status=302, headers={"location": HOME})
    fetcher, _, _ = make({**ROBOTS, HOME: loop})
    with pytest.raises(ItemError, match="redirects"):
        fetcher.get_page(HOME)


def test_pages_need_a_scope() -> None:
    site = MockSite({**ROBOTS, HOME: html_reply("ok")})
    fetcher = HttpFetcher(
        pacer=RecordingPacer(), resolver=FakeResolver(), transport=site.transport()
    )
    with pytest.raises(Disallowed):
        fetcher.get_page(HOME)
    assert not site.requested(HOME)


def test_ipv6_host_header_is_bracketed() -> None:
    url = "https://[2606:4700:4700::1111]/a.jpg"
    fetcher, site, _ = make({})
    with pytest.raises(ItemError):
        fetcher.get_image(url)
    _, request = site.requests[-1]
    assert request.headers["host"] == "[2606:4700:4700::1111]"


def test_each_host_gets_its_own_connection_pool_and_no_cookies_are_kept() -> None:
    other = "https://cdn.cnn.com/b.jpg"
    fetcher, site, _ = make(
        {
            **ROBOTS,
            IMG: Reply(
                status=200,
                body=b"x",
                headers={"content-type": "image/jpeg", "set-cookie": "s=1; Path=/"},
            ),
            other: Reply(status=200, body=b"y", headers={"content-type": "image/jpeg"}),
        }
    )
    fetcher.get_image(IMG)
    fetcher.get_image(other)
    _, request = next((u, r) for u, r in site.requests if u == other)
    assert "cookie" not in request.headers  # both hosts resolve to the same IP
    assert len(fetcher._clients) == 2


def test_robots_rules_are_kept_per_scheme() -> None:
    http_url = "http://edition.cnn.com/private/x"
    fetcher, _, _ = make(
        {
            **ROBOTS,
            "http://edition.cnn.com/robots.txt": robots_reply(),  # http allows everything
            http_url: html_reply("open"),
        }
    )
    with pytest.raises(Disallowed):
        fetcher.get_page("https://edition.cnn.com/private/x")
    assert fetcher.get_page(http_url).text == "open"


def test_unknown_charset_falls_back_to_utf8() -> None:
    reply = Reply(
        status=200, body="café".encode(), headers={"content-type": "text/html; charset=x-made-up"}
    )
    fetcher, _, _ = make({**ROBOTS, HOME: reply})
    assert fetcher.get_page(HOME).text == "café"
