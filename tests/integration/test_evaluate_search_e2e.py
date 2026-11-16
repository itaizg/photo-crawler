"""The evaluator end to end on seeded pgvector, with a fake face model (feature 003, research R9).

Proves the evaluator can FAIL, not only PASS (constitution IV): each planted defect must turn the
clean PASS into FAIL (or "cannot evaluate"), and no run may change the database.
"""

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from pathlib import Path

import psycopg
import pytest
from psycopg.sql import SQL, Identifier

from face_index.models import Box, DetectedFace, NormalizedImage
from face_index.normalize import PillowNormalizer
from face_index.search_store import PostgresSearchIndex
from face_index.store import PostgresIndexStore
from tests import images
from tests.evalset import ABSENT, PRESENT, default_rows, page, photo_bytes, write_folder
from tests.fakes import IdentityEmbedder
from tests.unit.test_evaluate_search import ev

TABLES = ("page", "image", "image_url", "page_image", "face")


def clean_pages() -> dict[str, list[str]]:
    """Each present label is shown on both of its labelled pages; one page shows a stranger."""
    pages = {page(2 * i + k): [label] for i, label in enumerate(PRESENT) for k in (0, 1)}
    pages[page(40)] = ["stranger"]
    return pages


def seed(
    store: PostgresIndexStore, embedder: IdentityEmbedder, pages: Mapping[str, Sequence[str]]
) -> None:
    profile_id = store.init_schema()
    n = 0
    for url, identities in pages.items():
        page_id = store.upsert_page(url, "cnn", 1)
        for identity in identities:
            n += 1
            image = NormalizedImage(
                sha256=hashlib.sha256(f"{url}#{n}".encode()).digest(),
                jpeg=images.photo(64, 64, seed=n),
                width=64,
                height=64,
                original_format="JPEG",
                original_width=64,
                original_height=64,
            )
            image_id, _ = store.stage_image(image, f"https://img.test/{n}.jpg", page_id, False)
            face = DetectedFace(
                box=Box(x=4, y=4, w=16, h=16), confidence=0.99, embedding=embedder.vector(identity)
            )
            store.process_pending(image_id, lambda _p, f=face: [f], profile_id)


def query_embedder(identity_of: Mapping[str, str | None] | None = None) -> IdentityEmbedder:
    """Each label's photo shows that label's identity, unless ``identity_of`` says otherwise."""
    mapping = {label: label for label in (*PRESENT, *ABSENT)} | dict(identity_of or {})
    normalizer = PillowNormalizer()
    identities = {}
    for label, identity in mapping.items():
        if identity is not None:
            normalized = normalizer.normalize(photo_bytes(label), None)
            assert isinstance(normalized, NormalizedImage)
            identities[hashlib.sha256(normalized.jpeg).hexdigest()] = identity
    return IdentityEmbedder(identities)


def counts(sql: psycopg.Connection) -> dict[str, int]:
    found = {}
    for table in TABLES:
        row = sql.execute(SQL("SELECT count(*) FROM {}").format(Identifier(table))).fetchone()
        assert row is not None
        found[table] = int(row[0])
    return found


@pytest.fixture
def index(db_dsn: str) -> Iterator[PostgresSearchIndex]:
    with closing(PostgresSearchIndex(db_dsn)) as idx:
        yield idx


def evaluate(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    *,
    pages: Mapping[str, Sequence[str]] | None = None,
    identity_of: Mapping[str, str | None] | None = None,
    rows: Sequence[tuple[str, ...]] | None = None,
) -> int:
    embedder = query_embedder(identity_of)
    seed(store, embedder, clean_pages() if pages is None else pages)
    root = write_folder(tmp_path, rows)
    before = counts(sql)
    status = ev.run(root, None, index, embedder, log_to=None)
    assert counts(sql) == before  # the evaluator only reads
    assert (root / "runs.csv").read_text(encoding="utf-8").count("\n") == 1  # mock runs never log
    return status


def test_clean_fixture_passes(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert evaluate(tmp_path, store, index, sql) == ev.EXIT_PASS
    out = capsys.readouterr().out
    assert out.strip().endswith("PASS")
    assert "index: 11 faces, 11 images, 11 pages" in out


def test_planted_miss_fails(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pages = clean_pages()
    pages[page(5)] = ["stranger"]  # person-c is labelled on page 5, but no image of them is there
    assert evaluate(tmp_path, store, index, sql, pages=pages) == ev.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"missed: {page(5)}" in out
    assert "0.50" in out


def test_absent_look_alike_fails(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        evaluate(tmp_path, store, index, sql, identity_of={"absent-a": "person-a"}) == ev.EXIT_FAIL
    )
    out = capsys.readouterr().out
    assert f"extra:  {page(0)}" in out
    assert out.strip().endswith("FAIL")


def test_onsite_look_alike_pair_fails(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = evaluate(tmp_path, store, index, sql, identity_of={"person-b": "person-a"})
    assert status == ev.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"extra:  {page(0)}" in out  # person-a's page returned for person-b


def test_label_error_fails_and_is_not_a_miss(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [*default_rows(), ("person-d", page(99), "none", "")]
    assert evaluate(tmp_path, store, index, sql, rows=rows) == ev.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"label error (page not indexed): {page(99)}" in out
    assert f"missed: {page(99)}" not in out


def test_photo_without_a_face_cannot_be_evaluated(
    tmp_path: Path,
    store: PostgresIndexStore,
    index: PostgresSearchIndex,
    sql: psycopg.Connection,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = evaluate(tmp_path, store, index, sql, identity_of={"person-e": None})
    assert status == ev.EXIT_CANNOT_EVALUATE
    assert "photos/person-e.jpg: no face detected" in capsys.readouterr().err
