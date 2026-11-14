"""Shared fixtures. Database tests use loopback Postgres at FACE_INDEX_TEST_DSN.

Each test gets its own schema (``search_path = <schema>, public``); pgvector's types stay in
``public``. Without a reachable database these tests are skipped locally and fail in CI.
"""

import os
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.sql import SQL, Identifier

from face_index.store import PostgresIndexStore
from tests.fakes import FakeClock, FakeEmbedder, FakeResolver

TEST_DSN = os.environ.get("FACE_INDEX_TEST_DSN", "postgresql://faces@127.0.0.1:5432/faces_test")


def _admin_connection() -> psycopg.Connection:
    try:
        return psycopg.connect(TEST_DSN, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"test database unreachable ({type(exc).__name__}); start `postgres`")


@pytest.fixture(scope="session")
def pg_extension() -> None:
    with _admin_connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")


@pytest.fixture
def db_dsn(pg_extension: None) -> Iterator[str]:
    """A DSN whose search_path is a fresh, empty schema (dropped after the test)."""
    schema = f"t_{uuid.uuid4().hex[:12]}"
    with _admin_connection() as conn:
        conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(schema)))
    # pytest runs the code after `yield` even when the test fails.
    yield make_conninfo(TEST_DSN, options=f"-c search_path={schema},public")
    with _admin_connection() as conn:
        conn.execute(SQL("DROP SCHEMA {} CASCADE").format(Identifier(schema)))


@pytest.fixture
def store(db_dsn: str) -> Iterator[PostgresIndexStore]:
    s = PostgresIndexStore(db_dsn)
    s.init_schema()
    yield s
    s.close()


@pytest.fixture
def sql(db_dsn: str) -> Iterator[psycopg.Connection]:
    """A plain autocommit connection on the test schema, for assertions."""
    with psycopg.connect(db_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder(default_faces=1)


@pytest.fixture
def fake_resolver() -> FakeResolver:
    return FakeResolver()
