"""The search core (``face_index.search``) with fakes: no database, no model."""

import pytest

from face_index import config
from face_index.faces import narrow_results
from face_index.models import Box, DetectedFace, PageMatch, QueryFace
from face_index.normalize import PillowNormalizer
from face_index.search import (
    FaceNumberError,
    NoFaceError,
    QueryImageError,
    prepare_query,
    search_faces,
    select_faces,
)
from tests import images
from tests.fakes import FakeEmbedder, FakeSearchIndex


class RecordingEmbedder:
    def __init__(self, faces: list[DetectedFace]) -> None:
        self.faces = faces
        self.received: list[bytes] = []

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        self.received.append(jpeg)
        return self.faces


def face(x: int, y: int = 4) -> DetectedFace:
    return DetectedFace(box=Box(x=x, y=y, w=16, h=16), confidence=0.99, embedding=(float(x),) * 512)


def page(url: str, distance: float, *image_urls: str) -> PageMatch:
    return PageMatch(
        url=url, best_distance=distance, lead=False, image_urls=image_urls or (f"{url}.jpg",)
    )


# -- prepare_query (FR-003, R6) ---------------------------------------------------------------


def test_the_embedder_receives_the_normalized_jpeg_not_the_original() -> None:
    original = images.photo(2000, 1000, seed=1, fmt="PNG")
    embedder = RecordingEmbedder([face(4)])
    normalized, faces = prepare_query(original, PillowNormalizer(), embedder)
    assert embedder.received == [normalized.jpeg]
    assert normalized.jpeg[:2] == b"\xff\xd8"  # JPEG
    assert max(normalized.width, normalized.height) == config.MAX_SIDE
    assert [f.number for f in faces] == [1]


def test_faces_are_numbered_in_the_embedders_order() -> None:
    embedder = RecordingEmbedder([face(4), face(24), face(44)])
    _, faces = prepare_query(images.photo(200, 100), PillowNormalizer(), embedder)
    assert [(f.number, f.box.x) for f in faces] == [(1, 4), (2, 24), (3, 44)]
    assert faces[0].embedding == (4.0,) * 512


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "SVG"),
        (images.photo(40, 40), "image is 40x40; at least 64 px on the shortest side is needed"),
        (images.bomb_png(), "megapixels"),
        (b"this is not an image", "not a decodable image"),
        (images.corrupt_jpeg(), "not a decodable image"),
    ],
)
def test_unusable_query_files_raise_query_image_error(data: bytes, message: str) -> None:
    with pytest.raises(QueryImageError, match=message):
        prepare_query(data, PillowNormalizer(), FakeEmbedder(default_faces=1))


def test_no_face_raises() -> None:
    with pytest.raises(NoFaceError):
        prepare_query(images.photo(), PillowNormalizer(), FakeEmbedder(default_faces=0))


# -- search_faces (FR-004 to FR-009) ----------------------------------------------------------


def test_each_face_is_searched_once_with_threshold_and_profile() -> None:
    index = FakeSearchIndex(pages=[page("https://cnn.test/a", 0.1)])
    faces = [
        QueryFace(number=1, box=Box(x=1, y=1, w=8, h=8), embedding=(1.0,) * 512),
        QueryFace(number=2, box=Box(x=9, y=1, w=8, h=8), embedding=(2.0,) * 512),
    ]
    results = search_faces(faces, index, profile_id=7, threshold=0.3)
    assert [r.face.number for r in results] == [1, 2]
    assert [(c[1], c[2]) for c in index.calls] == [(0.3, 7), (0.3, 7)]
    assert [c[0][0] for c in index.calls] == [1.0, 2.0]
    assert results[0].pages == (page("https://cnn.test/a", 0.1),)


def test_no_match_gives_an_empty_result() -> None:
    results = search_faces(
        [QueryFace(number=1, box=Box(x=1, y=1, w=8, h=8), embedding=(1.0,) * 512)],
        FakeSearchIndex(),
        1,
        0.3,
    )
    assert results[0].pages == ()


# -- threshold (US2, FR-005) ------------------------------------------------------------------

CANNED = [
    page("https://cnn.test/a", 0.10),
    page("https://cnn.test/b", 0.25),
    page("https://cnn.test/c", 0.29),
]
ONE_FACE = [QueryFace(number=1, box=Box(x=1, y=1, w=8, h=8), embedding=(1.0,) * 512)]


def pages_at(threshold: float) -> list[str]:
    [result] = search_faces(ONE_FACE, FakeSearchIndex(pages=CANNED), 1, threshold)
    return [p.url for p in result.pages]


def test_the_default_threshold_keeps_every_close_page() -> None:
    assert pages_at(0.30) == ["https://cnn.test/a", "https://cnn.test/b", "https://cnn.test/c"]


def test_a_stricter_threshold_returns_a_subset() -> None:
    strict = pages_at(0.20)
    assert strict == ["https://cnn.test/a"]
    assert set(strict) <= set(pages_at(0.30))


def test_the_threshold_is_strict() -> None:
    assert pages_at(0.10) == []


# -- several faces (US3, FR-010, FR-011) ------------------------------------------------------


def test_faces_with_equal_x_are_numbered_top_to_bottom() -> None:
    raw = [
        {
            "face_confidence": 0.99,
            "facial_area": {"x": 30, "y": 50, "w": 10, "h": 10},
            "embedding": [0.0] * 512,
        },
        {
            "face_confidence": 0.99,
            "facial_area": {"x": 30, "y": 5, "w": 10, "h": 10},
            "embedding": [1.0] * 512,
        },
        {
            "face_confidence": 0.99,
            "facial_area": {"x": 2, "y": 70, "w": 10, "h": 10},
            "embedding": [2.0] * 512,
        },
    ]
    embedder = RecordingEmbedder(narrow_results(raw, 100, 100))
    _, faces = prepare_query(images.photo(100, 100), PillowNormalizer(), embedder)
    assert [(f.number, f.box.x, f.box.y) for f in faces] == [(1, 2, 70), (2, 30, 5), (3, 30, 50)]


THREE = [
    QueryFace(number=n, box=Box(x=4 + 20 * (n - 1), y=4, w=16, h=16), embedding=(float(n),) * 512)
    for n in (1, 2, 3)
]


def test_select_all_faces_by_default() -> None:
    assert select_faces(THREE, None) == THREE


def test_select_one_face_by_number() -> None:
    assert select_faces(THREE, 2) == [THREE[1]]


def test_selecting_a_missing_face_reports_the_count() -> None:
    with pytest.raises(FaceNumberError) as caught:
        select_faces(THREE, 4)
    assert caught.value.count == 3
