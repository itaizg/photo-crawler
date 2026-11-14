"""The 3-attempt cap, in SQL (FR-023a, research R11a). Used only by ``PostgresIndexStore``."""

import psycopg

from face_index.models import RequestKind

_MAX_ERROR_LEN = 200

_RECORD_FAILURE = """
INSERT INTO fetch_attempt (url, kind, attempts, last_error, last_run_id)
VALUES (%(url)s, %(kind)s, 1, %(error)s, %(run_id)s)
ON CONFLICT (url, kind) DO UPDATE SET
  attempts = least(
    fetch_attempt.attempts
      + (fetch_attempt.last_run_id IS DISTINCT FROM EXCLUDED.last_run_id)::int,
    3),
  last_error = EXCLUDED.last_error,
  last_attempt_at = now(),
  last_run_id = EXCLUDED.last_run_id
"""

_RECORD_DETECT_FAILURE = """
UPDATE image SET
  detect_attempts = least(
    detect_attempts + (detect_last_run_id IS DISTINCT FROM %(run_id)s)::int, 3),
  detect_last_run_id = %(run_id)s
WHERE id = %(image_id)s AND status = 'pending'
"""


def is_abandoned(conn: psycopg.Connection, url: str, kind: RequestKind, max_attempts: int) -> bool:
    row = conn.execute(
        "SELECT attempts FROM fetch_attempt WHERE url = %s AND kind = %s", (url, kind)
    ).fetchone()
    return row is not None and int(row[0]) >= max_attempts


def record_failure(
    conn: psycopg.Connection, url: str, kind: RequestKind, error: str, run_id: str
) -> None:
    conn.execute(
        _RECORD_FAILURE,
        {"url": url, "kind": kind, "error": error[:_MAX_ERROR_LEN], "run_id": run_id},
    )


def record_success(conn: psycopg.Connection, url: str, kind: RequestKind) -> None:
    conn.execute("DELETE FROM fetch_attempt WHERE url = %s AND kind = %s", (url, kind))


def record_detect_failure(conn: psycopg.Connection, image_id: int, run_id: str) -> None:
    conn.execute(_RECORD_DETECT_FAILURE, {"image_id": image_id, "run_id": run_id})


def abandoned_pending(conn: psycopg.Connection, max_attempts: int) -> int:
    row = conn.execute(
        "SELECT count(*) FROM image WHERE status = 'pending' AND detect_attempts >= %s",
        (max_attempts,),
    ).fetchone()
    return int(row[0]) if row else 0
