import pytest

from face_index.config import SITES, Site
from face_index.urls import in_scope, normalize_image_url, normalize_page_url

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
