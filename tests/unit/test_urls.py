import pytest

from face_index.config import SITES, Site
from face_index.urls import for_log, in_scope, normalize_image_url, normalize_page_url

CNN, PEOPLE = SITES


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP://Edition.CNN.com/", "https://edition.cnn.com/"),
        ("https://edition.cnn.com/world/x/", "https://edition.cnn.com/world/x"),
        ("https://edition.cnn.com//world//x", "https://edition.cnn.com/world/x"),
        ("https://edition.cnn.com/x#comments", "https://edition.cnn.com/x"),
        ("https://edition.cnn.com/x?utm_source=a&utm_medium=b", "https://edition.cnn.com/x"),
        ("https://people.com/x?fbclid=1&page=2&gclid=3", "https://people.com/x?page=2"),
        ("https://people.com/x?b=2&a=1", "https://people.com/x?a=1&b=2"),
        ("https://people.com:443/x", "https://people.com/x"),
        ("http://people.com:80/x", "https://people.com/x"),
        ("https://people.com", "https://people.com/"),
    ],
)
def test_normalize_page_url(raw: str, expected: str) -> None:
    assert normalize_page_url(raw) == expected


def test_page_variants_collapse_to_one() -> None:
    variants = [
        "http://edition.cnn.com/2026/11/10/story/",
        "https://EDITION.cnn.com/2026/11/10/story#top",
        "https://edition.cnn.com/2026/11/10/story?utm_campaign=x",
    ]
    assert len({normalize_page_url(v) for v in variants}) == 1


def test_content_query_keeps_pages_distinct() -> None:
    a = normalize_page_url("https://people.com/gallery?slide=1")
    b = normalize_page_url("https://people.com/gallery?slide=2")
    assert a != b


def test_normalize_image_url_keeps_query_and_drops_fragment() -> None:
    url = "https://Media.CNN.com/api/v1/images/abc.jpg?c=16x9&q=w_800#x"
    assert normalize_image_url(url) == "https://media.cnn.com/api/v1/images/abc.jpg?c=16x9&q=w_800"


@pytest.mark.parametrize(
    ("url", "site", "expected"),
    [
        ("https://edition.cnn.com/world", CNN, True),
        ("https://www.cnn.com/world", CNN, False),
        ("https://money.cnn.com/x", CNN, False),
        ("https://people.com/x", PEOPLE, True),
        ("https://www.people.com/x", PEOPLE, True),
        ("https://people.com.evil.example/x", PEOPLE, False),
        ("ftp://people.com/x", PEOPLE, False),
    ],
)
def test_in_scope(url: str, site: Site, expected: bool) -> None:
    assert in_scope(url, site) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://u:secret@img.test/a.jpg?sig=abc&w=200", "https://img.test/a.jpg?sig=…&w=…"),
        (
            "https://img.test/a.jpg?X-Amz-Signature=deadbeef",
            "https://img.test/a.jpg?X-Amz-Signature=…",
        ),
        ("https://img.test/a.jpg#frag", "https://img.test/a.jpg"),
        ("https://img.test:8443/a.jpg", "https://img.test:8443/a.jpg"),
        ("https://img.test:bad/a.jpg?token=t", "<malformed url>"),
        ("http://[::1/a.jpg?token=t", "<malformed url>"),
        # a bare token (no "=") is masked whole, not logged as a key
        ("https://img.test/a.jpg?s3cr3t&w=200", "https://img.test/a.jpg?…&w=…"),
        # an encoded CR/LF in a key stays encoded: no forged log line
        (
            "https://img.test/a.jpg?a%0D%0Afake%20line=1",
            "https://img.test/a.jpg?a%0D%0Afake%20line=…",
        ),
        # control or line-separator characters in the host are percent-encoded too
        ("https://a\x0bb.com/x", "https://a%0Bb.com/x"),
        ("https://a\u2028b.com/x", "https://a%E2%80%A8b.com/x"),
        ("https://[::1]:8443/x?k=v", "https://[::1]:8443/x?k=…"),
        # raw control or non-ASCII characters in the path or a key are percent-encoded
        ("https://img.test/a\x0bb/é.jpg?k\x00=1", "https://img.test/a%0Bb/%C3%A9.jpg?k%00=…"),
    ],
)
def test_for_log_masks_secrets(raw: str, expected: str) -> None:
    assert for_log(raw) == expected
