"""A tiny two-site web, served by ``httpx.MockTransport``, for the offline smoke test (R13).

Site ``cnn`` (news.test, images on img.news.test) and site ``people`` (celeb.test, images on the
same host, like people.com) each have a home page and two articles. Faces are decided by the
``FakeEmbedder`` from each normalized image's size (see ``FACES_BY_SIZE``).
"""

import io

from PIL import Image

from face_index.config import Site
from tests import images
from tests.fakes import FakeEmbedder, MockSite, Reply, html_reply, robots_reply

NEWS = Site(name="cnn", seed_url="https://news.test/", page_hosts=frozenset({"news.test"}))
CELEB = Site(name="people", seed_url="https://celeb.test/", page_hosts=frozenset({"celeb.test"}))
SITES = (NEWS, CELEB)

FACE_ONE = images.photo(400, 300, seed=1)  # FakeEmbedder: 1 face
FACE_TWO = images.photo(420, 300, seed=2)  # FakeEmbedder: 2 faces
NO_FACE = images.photo(440, 300, seed=3)
FACES_BY_SIZE = {"400x300": 1, "420x300": 2}

URL = {
    "face_one": "https://img.news.test/face-one.jpg",
    "face_one_dup": "https://celeb.test/thmb/same-picture.jpg",  # same bytes, other URL and site
    "face_two": "https://img.news.test/face-two.jpg",
    "no_face": "https://celeb.test/thmb/landscape.jpg",
    "alpha": "https://celeb.test/thmb/logo.png",
    "svg": "https://img.news.test/brand.svg",
    "icon": "https://img.news.test/icon.jpg",
    "corrupt": "https://img.news.test/broken.jpg",
    "error": "https://celeb.test/thmb/server-error.jpg",
}

JPEG = {"content-type": "image/jpeg"}


def _page(lead: str, links: list[str], imgs: list[str]) -> Reply:
    head = f'<meta property="og:image" content="{lead}">'
    body = "".join(f'<a href="{u}">link</a>' for u in links)
    body += "".join(f'<img src="{u}">' for u in imgs)
    return html_reply(f"<html><head>{head}</head><body>{body}</body></html>")


def routes() -> dict[str, Reply]:
    return {
        "https://news.test/robots.txt": robots_reply(),
        "https://img.news.test/robots.txt": robots_reply(),
        "https://celeb.test/robots.txt": robots_reply(),
        "https://news.test/": _page(
            URL["face_one"], ["/a1", "/a2"], [URL["face_one"], URL["svg"], URL["icon"]]
        ),
        "https://news.test/a1": _page(URL["face_two"], ["/"], [URL["face_two"], URL["corrupt"]]),
        "https://news.test/a2": _page(URL["face_one"], [], [URL["face_one"]]),
        "https://celeb.test/": _page(
            URL["no_face"], ["/b1", "/b2"], [URL["no_face"], URL["alpha"]]
        ),
        "https://celeb.test/b1": _page(URL["face_one_dup"], [], [URL["face_one_dup"]]),
        "https://celeb.test/b2": _page(URL["no_face"], [], [URL["error"]]),
        URL["face_one"]: Reply(status=200, body=FACE_ONE, headers=JPEG),
        URL["face_one_dup"]: Reply(status=200, body=FACE_ONE, headers=JPEG),
        URL["face_two"]: Reply(status=200, body=FACE_TWO, headers=JPEG),
        URL["no_face"]: Reply(status=200, body=NO_FACE, headers=JPEG),
        URL["alpha"]: Reply(
            status=200, body=images.rgba_png(), headers={"content-type": "image/png"}
        ),
        URL["svg"]: Reply(status=200, body=images.SVG, headers={"content-type": "image/svg+xml"}),
        URL["icon"]: Reply(status=200, body=images.photo(40, 40), headers=JPEG),
        URL["corrupt"]: Reply(status=200, body=images.corrupt_jpeg(), headers=JPEG),
        URL["error"]: Reply(status=500),
    }


def mock_site() -> MockSite:
    return MockSite(routes())


def _size_key(jpeg: bytes) -> str:
    with Image.open(io.BytesIO(jpeg)) as image:
        return f"{image.width}x{image.height}"


def embedder(fail_sizes: set[str] | None = None) -> FakeEmbedder:
    return FakeEmbedder(FACES_BY_SIZE, default_faces=0, fail_on=fail_sizes, lookup=_size_key)
