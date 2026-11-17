"""Export the face index once, read-only, for the search experiments.

See docs/briefs/search-experiments.md.

    PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" \\
      FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces \\
      uv run python scripts/experiments/export_index.py --set YYYY-MM-DD

Writes eval-snapshots/experiments/<date>/facenet512.npz (gitignored). The faces and the page
links are read in one repeatable-read transaction, so they describe one state of the index. An
index whose latest page crawl is after the --set date is refused: it is no longer that crawl.

Exit 0 on success, 2 when the database or the profile is unavailable or the index is from a
later crawl.
"""

import argparse
import logging
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from typing import Any, Final, LiteralString

import numpy as np
import psycopg
import snapshot
from numpy.typing import NDArray
from pgvector.psycopg import register_vector

from face_index import model_profile
from face_index.errors import InfrastructureError

_FACES: Final[LiteralString] = """
SELECT f.image_id, f.x, f.y, f.w, f.h, f.embedding
FROM face f
JOIN image i ON i.id = f.image_id AND i.status = 'indexed'
JOIN model_profile m ON m.id = f.model_profile_id AND m.key = %s
ORDER BY f.id
"""
# The search's visibility rules (src/face_index/search_query.sql): indexed images, and links the
# page's latest crawl refreshed.
_LATEST_CRAWL: Final[LiteralString] = "SELECT max(last_crawled_at) FROM page"
_LINKS: Final[LiteralString] = """
SELECT pi.image_id, p.url
FROM page_image pi
JOIN page p  ON p.id = pi.page_id AND pi.last_seen_at >= p.last_crawled_at
JOIN image i ON i.id = pi.image_id AND i.status = 'indexed'
ORDER BY pi.image_id, p.url
"""


log = logging.getLogger("export_index")


def export(conn: psycopg.Connection) -> tuple[snapshot.Snapshot, datetime | None]:
    """The snapshot, and the index's latest page crawl, from one consistent view of the database."""
    register_vector(conn)
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    with conn.transaction():
        row = conn.execute(_LATEST_CRAWL).fetchone()
        faces = conn.execute(_FACES, (model_profile.KEY,)).fetchall()
        links = conn.execute(_LINKS).fetchall()
    return to_snapshot(faces, links), row[0] if row else None


def later_crawl(latest: datetime | None, crawl_date: date) -> bool:
    """Whether the index has been crawled again after the label set's crawl date (UTC)."""
    return latest is not None and latest.astimezone(UTC).date() > crawl_date


def _vector(value: object) -> NDArray[np.float32]:
    """pgvector 0.5 loads ``vector`` columns as ``Vector`` objects, older versions as arrays."""
    to_numpy = getattr(value, "to_numpy", None)
    return np.asarray(to_numpy() if callable(to_numpy) else value, dtype=np.float32)


def to_snapshot(
    faces: Sequence[Sequence[Any]], links: Sequence[Sequence[Any]]
) -> snapshot.Snapshot:
    """The snapshot from the rows of ``_FACES`` and ``_LINKS``."""
    pages: dict[int, list[str]] = {}
    for image, url in links:
        pages.setdefault(int(image), []).append(str(url))
    return snapshot.Snapshot(
        face_image=np.asarray([r[0] for r in faces], dtype=np.int64),
        boxes=np.asarray([r[1:5] for r in faces], dtype=np.int64).reshape(-1, 4),
        embeddings=np.asarray([_vector(r[5]) for r in faces], dtype=np.float32).reshape(
            -1, model_profile.DIMENSIONS
        ),
        image_pages={i: tuple(u) for i, u in pages.items()},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="export_index", description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="crawl_date", type=date.fromisoformat, required=True)
    args = parser.parse_args(argv)
    snapshot.setup_logging()
    try:
        with closing(snapshot.connect()) as conn:
            snap, latest = export(conn)
    except (InfrastructureError, RuntimeError, psycopg.Error) as exc:
        log.error("%s", exc)  # noqa: TRY400 - an expected outage: its message, no traceback
        return 2
    if later_crawl(latest, args.crawl_date):
        log.error(
            "the index was crawled again on %s, after the %s crawl; export the labeller's "
            "snapshot of that crawl",
            latest.astimezone(UTC).date() if latest else None,
            args.crawl_date,
        )
        return 2
    if len(snap.face_image) == 0:
        log.error("no faces for profile %s", model_profile.KEY)
        return 2
    target = snapshot.path(args.crawl_date, "Facenet512")
    snap.save(target)
    pages = {u for urls in snap.image_pages.values() for u in urls}
    print(
        f"wrote {target}: {len(snap.face_image)} faces, "
        f"{len(set(snap.face_image.tolist()))} images, {len(pages)} pages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
