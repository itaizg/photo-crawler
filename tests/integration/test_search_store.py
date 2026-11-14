"""``PostgresSearchIndex`` against loopback Postgres (feature 002, contracts/search-query.md).

Vectors are built in the plane of the first two axes, so a stored face's cosine distance to the
query ``e0`` is exactly ``1 - cos(angle)``.
"""

import math
import re
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import numpy as np
import psycopg
import pytest

from face_index import model_profile
from face_index.errors import IndexNotReady, InfrastructureError
from face_index.models import Box, DetectedFace, NormalizedImage
from face_index.search_store import PostgresSearchIndex, search_sql
from face_index.store import PostgresIndexStore
from tests import images

CONTRACT = Path(__file__).resolve().parents[2] / "specs/002-face-search/contracts/search-query.md"
QUERY = (1.0,) + (0.0,) * 511


def at_distance(d: float) -> tuple[float, ...]:
    """A unit vector whose cosine distance to QUERY is ``d`` (0 ≤ d ≤ 1)."""
    c = 1.0 - d
    return (c, math.sqrt(max(0.0, 1.0 - c * c))) + (0.0,) * 510


ORTHOGONAL = (0.0, 1.0) + (0.0,) * 510  # distance exactly 1.0


class Seeder:
    def __init__(self, store: PostgresIndexStore) -> None:
        self.store = store
        self.profile_id = store.init_schema()
        self._seed = 0

    def page(self, url: str) -> int:
        return self.store.upsert_page(url, "cnn", 1)

    def image(
        self,
        page_id: int,
        vectors: list[tuple[float, ...]],
        url: str | None = None,
        lead: bool = False,
        index: bool = True,
    ) -> int:
        self._seed += 1
        seed = self._seed
        image = NormalizedImage(
            sha256=bytes([seed]) * 32,
            jpeg=images.photo(64, 64, seed=seed),
            width=64,
            height=64,
            original_format="JPEG",
            original_width=64,
            original_height=64,
        )
        image_id, _ = self.store.stage_image(
            image, url or f"https://img.test/{seed}.jpg", page_id, lead
        )
        if index:
            faces = [
                DetectedFace(box=Box(x=1 + 10 * i, y=1, w=8, h=8), confidence=0.99, embedding=vec)
                for i, vec in enumerate(vectors)
            ]
            self.store.process_pending(image_id, lambda _p: faces, self.profile_id)
        return image_id


@pytest.fixture
def seed(store: PostgresIndexStore) -> Seeder:
    return Seeder(store)


@pytest.fixture
def index(db_dsn: str) -> Iterator[PostgresSearchIndex]:
    idx = PostgresSearchIndex(db_dsn)
    yield idx
    idx.close()


def urls(index: PostgresSearchIndex, seed: Seeder, threshold: float = 0.30) -> list[str]:
    return [m.url for m in index.matching_pages(QUERY, threshold, seed.profile_id)]


def test_packaged_sql_equals_the_contract() -> None:
    if not CONTRACT.exists():
        pytest.skip("contract not in this checkout")
    match = re.search(r"```sql\n(.*?)```", CONTRACT.read_text(encoding="utf-8"), re.S)
    assert match, "no SQL block in the contract"
    assert search_sql() == match.group(1)


def test_threshold_is_strict(seed: Seeder, index: PostgresSearchIndex) -> None:
    near = seed.page("https://cnn.test/near")
    far = seed.page("https://cnn.test/far")
    orth = seed.page("https://cnn.test/orthogonal")
    seed.image(near, [at_distance(0.29)])
    seed.image(far, [at_distance(0.31)])
    seed.image(orth, [ORTHOGONAL])
    assert urls(index, seed) == ["https://cnn.test/near"]
    assert "https://cnn.test/orthogonal" not in urls(index, seed, 1.0)  # distance == threshold
    assert "https://cnn.test/orthogonal" in urls(index, seed, 1.0001)


def test_other_profile_and_pending_images_are_ignored(
    seed: Seeder, index: PostgresSearchIndex, sql: psycopg.Connection
) -> None:
    pending_page = seed.page("https://cnn.test/pending")
    seed.image(pending_page, [], index=False)  # stays pending; give it a face by hand
    other_page = seed.page("https://cnn.test/other-profile")
    other_img = seed.image(other_page, [QUERY])
    other_profile = sql.execute(
        "INSERT INTO model_profile (key, detector, model, dimensions, distance, align, "
        "normalization, min_face_confidence, deepface_version, tensorflow_version) "
        "VALUES ('old', 'x', 'y', 512, 'cosine', true, 'base', 0.9, '0', '0') RETURNING id"
    ).fetchone()
    assert other_profile is not None
    sql.execute(
        "UPDATE face SET model_profile_id = %s WHERE image_id = %s", (other_profile[0], other_img)
    )
    pending_id = sql.execute(
        "SELECT i.id FROM image i JOIN page_image pi ON pi.image_id = i.id "
        "JOIN page p ON p.id = pi.page_id WHERE p.url = 'https://cnn.test/pending'"
    ).fetchone()
    assert pending_id is not None
    sql.execute(
        "INSERT INTO face (image_id, face_index, x, y, w, h, confidence, embedding, "
        "model_profile_id) VALUES (%s, 0, 1, 1, 8, 8, 0.99, %s::vector, %s)",
        (pending_id[0], str(list(QUERY)), seed.profile_id),
    )
    assert urls(index, seed) == []


def test_pages_are_grouped_ordered_and_marked(seed: Seeder, index: PostgresSearchIndex) -> None:
    a = seed.page("https://cnn.test/a")
    b = seed.page("https://cnn.test/b")
    c = seed.page("https://cnn.test/c")
    shared = seed.image(a, [at_distance(0.20)], url="https://img.test/shared.jpg", lead=True)
    seed.store.link(b, shared, False)  # same image on two pages
    seed.image(c, [at_distance(0.05)], url="https://img.test/c-close.jpg")
    seed.image(c, [at_distance(0.25), ORTHOGONAL], url="https://img.test/c-far.jpg")

    matches = index.matching_pages(QUERY, 0.30, seed.profile_id)
    assert [m.url for m in matches] == [
        "https://cnn.test/c",
        "https://cnn.test/a",
        "https://cnn.test/b",
    ]
    by_url = {m.url: m for m in matches}
    assert by_url["https://cnn.test/c"].image_urls == (
        "https://img.test/c-close.jpg",
        "https://img.test/c-far.jpg",
    )
    assert by_url["https://cnn.test/c"].best_distance == pytest.approx(0.05, abs=1e-4)
    assert by_url["https://cnn.test/a"].lead is True
    assert by_url["https://cnn.test/b"].lead is False
    assert by_url["https://cnn.test/a"].best_distance == by_url["https://cnn.test/b"].best_distance
    assert len({m.url for m in matches}) == len(matches)


def test_ties_are_ordered_by_url(seed: Seeder, index: PostgresSearchIndex) -> None:
    for name in ("z", "m", "a"):
        seed.image(seed.page(f"https://cnn.test/{name}"), [QUERY])
    assert urls(index, seed) == [f"https://cnn.test/{n}" for n in ("a", "m", "z")]


def test_image_url_is_the_first_one_seen(
    seed: Seeder, index: PostgresSearchIndex, sql: psycopg.Connection
) -> None:
    page = seed.page("https://cnn.test/a")
    image = seed.image(page, [QUERY], url="https://img.test/zz-first.jpg")
    seed.store.add_url_and_link(image, "https://img.test/aa-later.jpg", page, False)
    sql.execute(
        "UPDATE image_url SET first_seen_at = first_seen_at + interval '1 minute' WHERE url = %s",
        ("https://img.test/aa-later.jpg",),
    )
    [match] = index.matching_pages(QUERY, 0.30, seed.profile_id)
    assert match.image_urls == ("https://img.test/zz-first.jpg",)


def test_outdated_links_are_ignored(
    seed: Seeder, index: PostgresSearchIndex, sql: psycopg.Connection
) -> None:
    kept = seed.page("https://cnn.test/kept")
    gone = seed.page("https://cnn.test/gone")
    seed.image(kept, [QUERY])
    seed.image(gone, [QUERY])
    assert urls(index, seed) == ["https://cnn.test/gone", "https://cnn.test/kept"]

    # The page is being crawled again: until that crawl finishes, its old link still counts.
    started = seed.store.clock()
    seed.page("https://cnn.test/gone")
    assert urls(index, seed) == ["https://cnn.test/gone", "https://cnn.test/kept"]

    # The crawl finished without seeing the image: its link is now outdated.
    seed.store.finish_page(gone, started)
    assert urls(index, seed) == ["https://cnn.test/kept"]

    # Same rule stated directly on the timestamps.
    sql.execute(
        "UPDATE page_image pi SET last_seen_at = p.last_crawled_at - interval '1 hour' "
        "FROM page p WHERE p.id = pi.page_id AND p.url = 'https://cnn.test/kept'"
    )
    assert urls(index, seed) == []


def test_profile_lookup_and_face_count(seed: Seeder, index: PostgresSearchIndex) -> None:
    page = seed.page("https://cnn.test/a")
    seed.image(page, [QUERY, ORTHOGONAL])
    seed.image(page, [], index=False)
    assert index.profile_id(model_profile.KEY) == seed.profile_id
    assert index.profile_id("missing") is None
    assert index.indexed_face_count(seed.profile_id) == 2


def test_empty_database_is_not_ready(db_dsn: str) -> None:
    with closing(PostgresSearchIndex(db_dsn)) as idx, pytest.raises(IndexNotReady):
        idx.profile_id(model_profile.KEY)


def test_session_is_read_only(seed: Seeder, index: PostgresSearchIndex) -> None:
    index.profile_id(model_profile.KEY)  # opens the connection
    conn = index._connection()
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("DELETE FROM page")


def test_unreachable_database_hides_the_dsn() -> None:
    dsn = "postgresql://secretuser@127.0.0.1:1/nowhere?connect_timeout=1"
    idx = PostgresSearchIndex(dsn)
    with pytest.raises(InfrastructureError) as caught:
        idx.profile_id(model_profile.KEY)
    assert "secretuser" not in str(caught.value)
    idx.close()


def test_embedding_is_sent_as_float32(seed: Seeder, index: PostgresSearchIndex) -> None:
    seed.image(seed.page("https://cnn.test/a"), [QUERY])
    as_array = np.asarray(QUERY, dtype=np.float64)
    assert [m.url for m in index.matching_pages(list(as_array), 0.3, seed.profile_id)] == [
        "https://cnn.test/a"
    ]
