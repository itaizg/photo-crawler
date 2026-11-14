"""``PostgresSearchIndex``: the read-only ``SearchIndex`` adapter (feature 002, research R3 to R5).

The session is opened with ``default_transaction_read_only = on``, so any write is rejected by
the server (FR-013). The search SQL lives in ``search_query.sql``, which must equal the SQL
block of ``specs/002-face-search/contracts/search-query.md`` (a test checks this). No
``psycopg`` exception leaves this module.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from importlib.resources import files

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from face_index import db
from face_index.errors import IndexNotReady, InfrastructureError
from face_index.models import PageMatch

_PROFILE_ID = "SELECT id FROM model_profile WHERE key = %s"
_FACE_COUNT = """
SELECT count(*) FROM face f JOIN image i ON i.id = f.image_id
WHERE f.model_profile_id = %s AND i.status = 'indexed'
"""


def search_sql() -> str:
    return files("face_index").joinpath("search_query.sql").read_text(encoding="utf-8")


class PostgresSearchIndex:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None
        self._sql = search_sql()

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            conn = db.connect(self._dsn)
            conn.execute("SET default_transaction_read_only = on")
            self._conn = conn
        return self._conn

    @contextmanager
    def _guard(self) -> Generator[psycopg.Connection]:
        try:
            yield self._connection()
        except psycopg.errors.UndefinedTable as exc:
            raise IndexNotReady("the index schema does not exist") from exc
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            self.close()
            raise InfrastructureError(f"database unavailable: {type(exc).__name__}") from exc
        except psycopg.Error as exc:  # anything else is a broken index, not a bad query photo
            raise InfrastructureError(f"database error: {type(exc).__name__}") from exc

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def profile_id(self, key: str) -> int | None:
        with self._guard() as conn:
            row = conn.execute(_PROFILE_ID, (key,)).fetchone()
            return int(row[0]) if row else None

    def indexed_face_count(self, profile_id: int) -> int:
        with self._guard() as conn:
            row = conn.execute(_FACE_COUNT, (profile_id,)).fetchone()
            return int(row[0]) if row else 0

    def matching_pages(
        self, embedding: Sequence[float], max_distance: float, profile_id: int
    ) -> list[PageMatch]:
        with self._guard() as conn:
            register_vector(conn)  # the profile row exists, so the vector type does too
            rows = conn.execute(
                self._sql.encode(),  # trusted SQL file shipped with the package
                {
                    "q": np.asarray(embedding, dtype=np.float32),
                    "max_dist": max_distance,
                    "profile_id": profile_id,
                },
            ).fetchall()
        return [
            PageMatch(
                url=str(url),
                best_distance=float(distance),
                lead=bool(lead),
                image_urls=tuple(str(u) for u in (image_urls or ())),
            )
            for url, distance, lead, image_urls in rows
        ]
