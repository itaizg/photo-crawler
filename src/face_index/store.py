"""``PostgresIndexStore``: the ``IndexStore`` adapter (research R10, R11).

Every public method runs inside ``_guard()``, which turns driver connection errors into
``InfrastructureError``. No ``psycopg`` exception leaves this module.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib.resources import files

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from face_index import attempts, db, model_profile
from face_index.errors import InfrastructureError, ItemError
from face_index.models import (
    DetectedFace,
    NormalizedImage,
    PendingImage,
    RequestKind,
    Skipped,
)

_UPSERT_PAGE = """
INSERT INTO page (url, site, depth) VALUES (%s, %s, %s)
ON CONFLICT (url) DO UPDATE
  SET last_crawled_at = now(), depth = least(page.depth, EXCLUDED.depth)
RETURNING id
"""

_LINK = """
INSERT INTO page_image (page_id, image_id, is_lead) VALUES (%s, %s, %s)
ON CONFLICT (page_id, image_id) DO UPDATE
  SET last_seen_at = now(), is_lead = page_image.is_lead OR EXCLUDED.is_lead
"""

_UPSERT_PROFILE = """
INSERT INTO model_profile (key, detector, model, dimensions, distance, align, normalization,
                           min_face_confidence, deepface_version, tensorflow_version)
VALUES (%(key)s, %(detector)s, %(model)s, %(dimensions)s, %(distance)s, %(align)s,
        %(normalization)s, %(min_conf)s, %(deepface)s, %(tensorflow)s)
ON CONFLICT (key) DO UPDATE
  SET deepface_version = EXCLUDED.deepface_version,
      tensorflow_version = EXCLUDED.tensorflow_version
RETURNING id
"""

_INSERT_PENDING = """
INSERT INTO image (sha256, status, original_format, original_width, original_height,
                   width, height, jpeg)
VALUES (%s, 'pending', %s, %s, %s, %s, %s, %s)
ON CONFLICT (sha256) DO NOTHING
RETURNING id
"""

_INSERT_SKIPPED = """
INSERT INTO image (sha256, status, skip_reason, original_format, original_width, original_height)
VALUES (%s, 'skipped', %s, %s, %s, %s)
ON CONFLICT (sha256) DO NOTHING
RETURNING id
"""

_INSERT_FACE = """
INSERT INTO face (image_id, face_index, x, y, w, h, confidence, embedding, model_profile_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (image_id, face_index) DO NOTHING
"""


@dataclass(frozen=True, slots=True)
class ExportedImage:
    id: int
    status: str
    jpeg: bytes | None
    width: int | None
    height: int | None
    boxes: tuple[tuple[int, int, int, int, int], ...]  # (face_index, x, y, w, h)


def schema_sql() -> str:
    return files("face_index").joinpath("schema.sql").read_text(encoding="utf-8")


def _link(conn: psycopg.Connection, page_id: int, image_id: int, is_lead: bool) -> None:
    """Link a page to an image. A new lead replaces the old one in the same transaction, so a
    page is never left without its lead between two statements (FR-009a)."""
    if is_lead:
        conn.execute(
            "UPDATE page_image SET is_lead = false "
            "WHERE page_id = %s AND is_lead AND image_id <> %s",
            (page_id, image_id),
        )
    conn.execute(_LINK, (page_id, image_id, is_lead))


class PostgresIndexStore:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg.Connection | None = None

    # -- connection handling ----------------------------------------------------------------

    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            conn = db.connect(self._dsn)
            # Before init_schema creates the extension this fails; init_schema registers it then.
            with suppress(psycopg.ProgrammingError):
                register_vector(conn)
            self._conn = conn
        return self._conn

    @contextmanager
    def _guard(self) -> Generator[psycopg.Connection]:
        try:
            yield self._connection()
        except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
            self.close()
            raise InfrastructureError(f"database unavailable: {type(exc).__name__}") from exc
        except psycopg.Error as exc:  # the database rejected this item's data
            raise ItemError(f"database rejected: {type(exc).__name__}") from exc

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    # -- schema -----------------------------------------------------------------------------

    def init_schema(self) -> int:
        profile = model_profile.current_profile()
        with self._guard() as conn:
            with conn.transaction():
                conn.execute(schema_sql().encode())  # trusted DDL file shipped with the package
            register_vector(conn)
            row = conn.execute(
                _UPSERT_PROFILE,
                {
                    "key": profile.key,
                    "detector": model_profile.DETECTOR,
                    "model": model_profile.MODEL,
                    "dimensions": model_profile.DIMENSIONS,
                    "distance": model_profile.DISTANCE,
                    "align": model_profile.ALIGN,
                    "normalization": model_profile.NORMALIZATION,
                    "min_conf": model_profile.MIN_FACE_CONFIDENCE,
                    "deepface": profile.deepface_version,
                    "tensorflow": profile.tensorflow_version,
                },
            ).fetchone()
            assert row is not None
            return int(row[0])

    # -- pages and links --------------------------------------------------------------------

    def upsert_page(self, url: str, site: str, depth: int) -> int:
        with self._guard() as conn:
            row = conn.execute(_UPSERT_PAGE, (url, site, depth)).fetchone()
            assert row is not None
            return int(row[0])

    def reset_lead(self, page_id: int) -> None:
        """Clear the page's lead mark; used when this crawl linked no lead image."""
        with self._guard() as conn:
            conn.execute(
                "UPDATE page_image SET is_lead = false WHERE page_id = %s AND is_lead", (page_id,)
            )

    def link(self, page_id: int, image_id: int, is_lead: bool) -> None:
        with self._guard() as conn, conn.transaction():
            _link(conn, page_id, image_id, is_lead)

    # -- images -----------------------------------------------------------------------------

    def find_image_by_url(self, url: str) -> int | None:
        with self._guard() as conn:
            row = conn.execute("SELECT image_id FROM image_url WHERE url = %s", (url,)).fetchone()
            return int(row[0]) if row else None

    def find_image_by_sha256(self, sha256: bytes) -> int | None:
        with self._guard() as conn:
            row = conn.execute("SELECT id FROM image WHERE sha256 = %s", (sha256,)).fetchone()
            return int(row[0]) if row else None

    def add_url_and_link(self, image_id: int, url: str, page_id: int, is_lead: bool) -> None:
        with self._guard() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO image_url (url, image_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (url, image_id),
            )
            _link(conn, page_id, image_id, is_lead)

    def stage_image(
        self, image: NormalizedImage | Skipped, url: str, page_id: int, is_lead: bool
    ) -> tuple[int, bool]:
        with self._guard() as conn, conn.transaction():
            if isinstance(image, NormalizedImage):
                row = conn.execute(
                    _INSERT_PENDING,
                    (
                        image.sha256,
                        image.original_format,
                        image.original_width,
                        image.original_height,
                        image.width,
                        image.height,
                        image.jpeg,
                    ),
                ).fetchone()
            else:
                row = conn.execute(
                    _INSERT_SKIPPED,
                    (
                        image.sha256,
                        image.reason,
                        image.original_format,
                        image.original_width,
                        image.original_height,
                    ),
                ).fetchone()
            created = row is not None
            if row is None:  # another writer stored the same content first (research R11)
                row = conn.execute(
                    "SELECT id FROM image WHERE sha256 = %s", (image.sha256,)
                ).fetchone()
                assert row is not None
            image_id = int(row[0])
            conn.execute(
                "INSERT INTO image_url (url, image_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (url, image_id),
            )
            _link(conn, page_id, image_id, is_lead)
            return image_id, created

    # -- detection --------------------------------------------------------------------------

    def pending_batches(self, batch_size: int, max_attempts: int) -> list[list[int]]:
        with self._guard() as conn:
            ids = [
                int(r[0])
                for r in conn.execute(
                    "SELECT id FROM image WHERE status = 'pending' AND detect_attempts < %s "
                    "ORDER BY id",
                    (max_attempts,),
                ).fetchall()
            ]
        return [ids[i : i + batch_size] for i in range(0, len(ids), batch_size)]

    def abandoned_pending(self, max_attempts: int) -> int:
        with self._guard() as conn:
            return attempts.abandoned_pending(conn, max_attempts)

    def process_pending(
        self,
        image_id: int,
        detect: Callable[[PendingImage], list[DetectedFace]],
        profile_id: int,
    ) -> int | None:
        with self._guard() as conn, conn.transaction():
            row = conn.execute(
                "SELECT id, jpeg, width, height FROM image "
                "WHERE id = %s AND status = 'pending' FOR UPDATE SKIP LOCKED",
                (image_id,),
            ).fetchone()
            if row is None:
                return None
            pending = PendingImage(int(row[0]), bytes(row[1]), int(row[2]), int(row[3]))
            # Only faces at or above the profile's confidence floor are stored and searchable.
            faces = [
                f for f in detect(pending) if f.confidence >= model_profile.MIN_FACE_CONFIDENCE
            ]
            for face in faces:
                b = face.box
                if b.x < 0 or b.y < 0 or b.x + b.w > pending.width or b.y + b.h > pending.height:
                    raise ItemError(f"face box outside image: {b}")
            if faces:
                with conn.cursor() as cur:
                    cur.executemany(
                        _INSERT_FACE,
                        [
                            (
                                image_id,
                                index,
                                f.box.x,
                                f.box.y,
                                f.box.w,
                                f.box.h,
                                f.confidence,
                                np.asarray(f.embedding, dtype=np.float32),
                                profile_id,
                            )
                            for index, f in enumerate(faces)
                        ],
                    )
                conn.execute(
                    "UPDATE image SET status = 'indexed', indexed_at = now() WHERE id = %s",
                    (image_id,),
                )
            else:
                conn.execute(
                    "UPDATE image SET status = 'no_faces', jpeg = NULL, indexed_at = now() "
                    "WHERE id = %s",
                    (image_id,),
                )
            return len(faces)

    def stored_faces(self, image_id: int) -> int | None:
        with self._guard() as conn:
            row = conn.execute(
                "SELECT i.status, (SELECT count(*) FROM face f WHERE f.image_id = i.id) "
                "FROM image i WHERE i.id = %s",
                (image_id,),
            ).fetchone()
        if row is None or row[0] not in ("indexed", "no_faces"):
            return None
        return int(row[1])

    # -- attempt cap ------------------------------------------------------------------------

    def is_abandoned(self, url: str, kind: RequestKind, max_attempts: int) -> bool:
        with self._guard() as conn:
            return attempts.is_abandoned(conn, url, kind, max_attempts)

    def record_failure(self, url: str, kind: RequestKind, error: str, run_id: str) -> None:
        with self._guard() as conn:
            attempts.record_failure(conn, url, kind, error, run_id)

    def record_success(self, url: str, kind: RequestKind) -> None:
        with self._guard() as conn:
            attempts.record_success(conn, url, kind)

    def record_detect_failure(self, image_id: int, run_id: str) -> None:
        with self._guard() as conn:
            attempts.record_detect_failure(conn, image_id, run_id)

    # -- export (operator CLI, not part of IndexStore) ---------------------------------------

    def export_image(self, image_id: int | None, sha256: bytes | None) -> ExportedImage | None:
        with self._guard() as conn:
            if image_id is not None:
                row = conn.execute(
                    "SELECT id, status, jpeg, width, height FROM image WHERE id = %s", (image_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, status, jpeg, width, height FROM image WHERE sha256 = %s",
                    (sha256,),
                ).fetchone()
            if row is None:
                return None
            boxes = conn.execute(
                "SELECT face_index, x, y, w, h FROM face WHERE image_id = %s ORDER BY face_index",
                (row[0],),
            ).fetchall()
            return ExportedImage(
                id=int(row[0]),
                status=str(row[1]),
                jpeg=bytes(row[2]) if row[2] is not None else None,
                width=row[3],
                height=row[4],
                boxes=tuple((int(b[0]), int(b[1]), int(b[2]), int(b[3]), int(b[4])) for b in boxes),
            )
