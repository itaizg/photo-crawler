from pathlib import Path

import psycopg
import pytest
from psycopg.sql import SQL, Identifier, Placeholder

from face_index import model_profile
from face_index.errors import InfrastructureError
from face_index.store import PostgresIndexStore, schema_sql

ROOT = Path(__file__).resolve().parents[2]
SHA = b"\x01" * 32


def test_schema_file_matches_contract() -> None:
    contract = ROOT / "specs/001-image-indexer/contracts/index-schema.sql"
    if not contract.exists():
        pytest.skip("contract not in this checkout")
    assert schema_sql() == contract.read_text(encoding="utf-8")


def test_init_schema_is_idempotent_and_profile_stable(db_dsn: str) -> None:
    store = PostgresIndexStore(db_dsn)
    first = store.init_schema()
    second = store.init_schema()
    assert first == second
    store.close()


def test_profile_row_matches_module(store: PostgresIndexStore, sql: psycopg.Connection) -> None:
    row = sql.execute("SELECT key, dimensions, distance FROM model_profile").fetchone()
    assert row == (model_profile.current_profile().key, 512, "cosine")


def _insert(conn: psycopg.Connection, columns: str, values: tuple[object, ...]) -> None:
    query = SQL("INSERT INTO image ({}) VALUES ({})").format(
        SQL(", ").join(Identifier(c) for c in columns.split(", ")),
        SQL(", ").join(Placeholder() for _ in values),
    )
    conn.execute(query, values)


@pytest.mark.usefixtures("store")
@pytest.mark.parametrize(
    ("columns", "values"),
    [
        # half-set dimensions
        ("sha256, status, jpeg, width", (SHA, "pending", b"x", 10)),
        # width above 1024
        ("sha256, status, jpeg, width, height", (SHA, "pending", b"x", 1025, 10)),
        # jpeg kept on a no_faces row
        (
            "sha256, status, jpeg, width, height, indexed_at",
            (SHA, "no_faces", b"x", 10, 10, "now"),
        ),
        # skip_reason on a non-skipped row
        (
            "sha256, status, skip_reason, jpeg, width, height",
            (SHA, "pending", "too_small", b"x", 1, 1),
        ),
        # indexed without indexed_at
        ("sha256, status, jpeg, width, height", (SHA, "indexed", b"x", 10, 10)),
        # detect_attempts above the cap
        ("sha256, status, jpeg, width, height, detect_attempts", (SHA, "pending", b"x", 1, 1, 4)),
        # half-set original dimensions
        ("sha256, status, skip_reason, original_width", (SHA, "skipped", "too_small", 10)),
    ],
)
def test_check_constraints_reject_invalid_rows(
    sql: psycopg.Connection, columns: str, values: tuple[object, ...]
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(sql, columns, values)


@pytest.mark.usefixtures("store")
def test_page_depth_is_capped(sql: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute(
            "INSERT INTO page (url, site, depth) VALUES ('https://people.com/', 'people', 11)"
        )


@pytest.mark.usefixtures("store")
def test_only_one_lead_per_page(sql: psycopg.Connection) -> None:
    sql.execute("INSERT INTO page (url, site, depth) VALUES ('https://people.com/', 'people', 0)")
    for i in (1, 2):
        sql.execute(
            "INSERT INTO image (sha256, status, skip_reason, original_format) "
            "VALUES (%s, 'skipped', 'too_small', 'PNG')",
            (bytes([i]) * 32,),
        )
    sql.execute(
        "INSERT INTO page_image (page_id, image_id, is_lead) SELECT 1, id, true FROM image LIMIT 1"
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        sql.execute(
            "INSERT INTO page_image (page_id, image_id, is_lead) "
            "SELECT 1, id, true FROM image ORDER BY id DESC LIMIT 1"
        )


def test_driver_errors_become_infrastructure_errors() -> None:
    bad = PostgresIndexStore("postgresql://nobody@127.0.0.1:1/none?connect_timeout=1")
    with pytest.raises(InfrastructureError):
        bad.upsert_page("https://people.com/", "people", 0)


def test_dropped_connection_becomes_infrastructure_error(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = store._connection().info.backend_pid
    sql.execute("SELECT pg_terminate_backend(%s)", (pid,))
    with pytest.raises(InfrastructureError):
        store.upsert_page("https://people.com/", "people", 0)
    # The store reconnects on the next call.
    assert store.upsert_page("https://people.com/", "people", 0) > 0
