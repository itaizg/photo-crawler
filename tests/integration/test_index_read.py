"""The search script's read contract (SC-009, contracts/index-read.md).

The reference query is loaded verbatim from the contract, so the document and this test cannot
drift apart.
"""

import re
from pathlib import Path

import numpy as np
import psycopg
import pytest
from pgvector.psycopg import register_vector

from face_index.models import Box, DetectedFace, NormalizedImage
from face_index.store import PostgresIndexStore
from tests import images

INSERT_FACE = (
    "INSERT INTO face (image_id, face_index, x, y, w, h, confidence, embedding, model_profile_id) "
    "VALUES (%s, 0, 1, 1, 10, 10, 0.99, %s, %s)"
)
CONTRACT = Path(__file__).resolve().parents[2] / "specs/001-image-indexer/contracts/index-read.md"


def reference_query() -> str:
    if not CONTRACT.exists():
        pytest.skip("contract not in this checkout")
    match = re.search(r"```sql\n(.*?)```", CONTRACT.read_text(encoding="utf-8"), re.S)
    assert match, "no SQL block in the contract"
    return match.group(1)


def vec(value: float) -> tuple[float, ...]:
    v = np.zeros(512, dtype=np.float32)
    v[0], v[1] = 1.0, value
    return tuple(float(x) for x in v)


def image(seed: int) -> NormalizedImage:
    return NormalizedImage(
        sha256=bytes([seed]) * 32,
        jpeg=images.photo(64, 64, seed=seed),
        width=64,
        height=64,
        original_format="JPEG",
        original_width=64,
        original_height=64,
    )


def test_reference_query_returns_each_matching_page_once(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    profile_id = store.init_schema()
    p1 = store.upsert_page("https://people.com/a", "people", 1)
    p2 = store.upsert_page("https://people.com/b", "people", 1)
    p3 = store.upsert_page("https://people.com/c", "people", 1)

    # One indexed image with the person's face, shown on two pages (lead on page a).
    shared, _ = store.stage_image(image(1), "https://img/shared.jpg", p1, True)
    store.link(p2, shared, False)
    store.process_pending(
        shared,
        lambda p: [
            DetectedFace(box=Box(x=1, y=1, w=10, h=10), confidence=0.99, embedding=vec(0.0))
        ],
        profile_id,
    )

    # A pending image with the same face vector: must stay invisible.
    pending, _ = store.stage_image(image(2), "https://img/pending.jpg", p3, False)
    with psycopg.connect(sql.info.dsn, autocommit=True) as raw:
        register_vector(raw)
        raw.execute(
            INSERT_FACE,
            (pending, np.asarray(vec(0.0), dtype=np.float32), profile_id),
        )
        # A face from another model profile: must be ignored.
        other_profile = raw.execute(
            "INSERT INTO model_profile (key, detector, model, dimensions, distance, align, "
            "normalization, min_face_confidence, deepface_version, tensorflow_version) "
            "VALUES ('other', 'x', 'y', 512, 'cosine', true, 'base', 0.9, '0', '0') RETURNING id"
        ).fetchone()
        assert other_profile is not None
        other_img, _ = store.stage_image(image(3), "https://img/other.jpg", p3, False)
        raw.execute(
            "UPDATE image SET status = 'indexed', indexed_at = now() WHERE id = %s", (other_img,)
        )
        raw.execute(
            INSERT_FACE,
            (other_img, np.asarray(vec(0.0), dtype=np.float32), other_profile[0]),
        )

        rows = raw.execute(
            reference_query().encode(),  # SQL text read from the contract file
            {
                "q": np.asarray(vec(0.05), dtype=np.float32),
                "max_dist": 0.30,
                "profile_id": profile_id,
            },
        ).fetchall()

    by_url = {r[0]: r for r in rows}
    assert set(by_url) == {"https://people.com/a", "https://people.com/b"}
    assert all(r[1] < 0.30 for r in rows)
    assert by_url["https://people.com/a"][2] is True  # lead mark reported
    assert by_url["https://people.com/b"][2] is False
    assert all(r[3] == 1 for r in rows)  # one matching image per page


def test_reference_query_ignores_outdated_links(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    """Guarantee 6: a link the page's latest crawl did not refresh no longer counts."""
    profile_id = store.init_schema()
    kept = store.upsert_page("https://people.com/kept", "people", 1)
    gone = store.upsert_page("https://people.com/gone", "people", 1)
    img, _ = store.stage_image(image(4), "https://img/four.jpg", kept, False)
    store.link(gone, img, False)
    store.process_pending(
        img,
        lambda p: [
            DetectedFace(box=Box(x=1, y=1, w=10, h=10), confidence=0.99, embedding=vec(0.0))
        ],
        profile_id,
    )
    started = store.clock()  # re-crawled, image not linked
    store.upsert_page("https://people.com/gone", "people", 1)
    store.finish_page(gone, started)

    with psycopg.connect(sql.info.dsn, autocommit=True) as raw:
        register_vector(raw)
        rows = raw.execute(
            reference_query().encode(),
            {
                "q": np.asarray(vec(0.0), dtype=np.float32),
                "max_dist": 0.30,
                "profile_id": profile_id,
            },
        ).fetchall()
    assert [r[0] for r in rows] == ["https://people.com/kept"]
