"""PostgresIndexStore against loopback pgvector (FR-009, FR-015 to FR-022b, research R11)."""

import threading
import time
from typing import LiteralString

import numpy as np
import psycopg
import pytest

from face_index.errors import ItemError
from face_index.models import Box, DetectedFace, NormalizedImage, PendingImage, Skipped
from face_index.store import PostgresIndexStore
from tests import images


def normalized(seed: int = 1) -> NormalizedImage:
    jpeg = images.photo(64, 64, seed=seed)
    return NormalizedImage(bytes([seed]) * 32, jpeg, 64, 64, "JPEG", 128, 128)


def face(x: int, value: float = 0.5) -> DetectedFace:
    return DetectedFace(Box(x, 1, 10, 10), 0.99, tuple([value] * 512))


def page(store: PostgresIndexStore, url: str = "https://people.com/a", depth: int = 1) -> int:
    return store.upsert_page(url, "people", depth)


def one(sql: psycopg.Connection, query: LiteralString, *args: object) -> object:
    row = sql.execute(query, args).fetchone()
    assert row is not None
    return row[0]


def test_stage_image_writes_image_url_and_link(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = page(store)
    image_id, created = store.stage_image(normalized(), "https://img/a.jpg", pid, True)
    assert created
    assert one(sql, "SELECT status FROM image WHERE id = %s", image_id) == "pending"
    assert store.find_image_by_url("https://img/a.jpg") == image_id
    assert one(sql, "SELECT is_lead FROM page_image WHERE image_id = %s", image_id) is True


def test_stage_skipped_has_no_bytes(store: PostgresIndexStore, sql: psycopg.Connection) -> None:
    image_id, _ = store.stage_image(
        Skipped(b"\x09" * 32, "too_small", "JPEG", 40, 40), "https://img/i.jpg", page(store), False
    )
    row = sql.execute(
        "SELECT status, skip_reason, jpeg, width FROM image WHERE id = %s", (image_id,)
    ).fetchone()
    assert row == ("skipped", "too_small", None, None)


def test_commit_faces_marks_indexed(store: PostgresIndexStore, sql: psycopg.Connection) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    assert store.process_pending(image_id, lambda p: [face(1), face(30)], profile_id=1) == 2
    row = sql.execute(
        "SELECT status, indexed_at IS NOT NULL FROM image WHERE id = %s", (image_id,)
    ).fetchone()
    assert row == ("indexed", True)
    assert one(sql, "SELECT count(*) FROM face WHERE image_id = %s", image_id) == 2
    # Already done: a second call is a no-op.
    assert store.process_pending(image_id, lambda p: [face(1)], profile_id=1) is None


def test_no_faces_keeps_dimensions_drops_bytes(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    assert store.process_pending(image_id, lambda p: [], profile_id=1) == 0
    row = sql.execute(
        "SELECT status, jpeg IS NULL, width, height, indexed_at IS NOT NULL "
        "FROM image WHERE id = %s",
        (image_id,),
    ).fetchone()
    assert row == ("no_faces", True, 64, 64, True)


def test_failure_inside_detection_leaves_image_pending(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)

    def boom(_: PendingImage) -> list[DetectedFace]:
        raise ItemError("model exploded")

    with pytest.raises(ItemError):
        store.process_pending(image_id, boom, profile_id=1)
    assert one(sql, "SELECT status FROM image WHERE id = %s", image_id) == "pending"
    assert one(sql, "SELECT count(*) FROM face") == 0


def test_box_outside_image_is_rejected_atomically(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    with pytest.raises(ItemError, match="outside"):
        store.process_pending(image_id, lambda p: [face(1), face(60)], profile_id=1)
    assert one(sql, "SELECT count(*) FROM face") == 0


def test_cosine_query_finds_the_face(store: PostgresIndexStore, sql: psycopg.Connection) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    store.process_pending(image_id, lambda p: [face(1, 0.5)], profile_id=1)
    from pgvector.psycopg import register_vector

    register_vector(sql)
    query = np.asarray([0.5] * 512, dtype=np.float32)
    row = sql.execute(
        "SELECT image_id, embedding <=> %s FROM face ORDER BY embedding <=> %s LIMIT 1",
        (query, query),
    ).fetchone()
    assert row is not None
    assert row[0] == image_id
    assert row[1] < 1e-6


# -- re-runs (US2) -------------------------------------------------------------------------------


def test_page_upsert_refreshes_time_and_keeps_smallest_depth(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = page(store, depth=2)
    first = one(sql, "SELECT last_crawled_at FROM page WHERE id = %s", pid)
    assert page(store, depth=1) == pid
    assert page(store, depth=3) == pid
    row = sql.execute(
        "SELECT depth, last_crawled_at > %s FROM page WHERE id = %s", (first, pid)
    ).fetchone()
    assert row == (1, True)


def test_relink_refreshes_last_seen(store: PostgresIndexStore, sql: psycopg.Connection) -> None:
    pid = page(store)
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", pid, False)
    before = one(sql, "SELECT last_seen_at FROM page_image WHERE image_id = %s", image_id)
    store.link(pid, image_id, False)
    assert one(
        sql, "SELECT last_seen_at > %s FROM page_image WHERE image_id = %s", before, image_id
    )


def test_lead_reset_moves_and_clears_mark(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = page(store)
    a, _ = store.stage_image(normalized(1), "https://img/a.jpg", pid, True)
    store.reset_lead(pid)  # next crawl: the page now declares image b as its lead
    b, _ = store.stage_image(normalized(2), "https://img/b.jpg", pid, True)
    store.link(pid, a, False)
    leads = [r[0] for r in sql.execute("SELECT image_id FROM page_image WHERE is_lead").fetchall()]
    assert leads == [b]
    store.reset_lead(pid)  # lead disappeared from the page
    assert one(sql, "SELECT count(*) FROM page_image WHERE is_lead") == 0


def test_new_lead_replaces_old_lead_without_a_reset(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = page(store)
    a, _ = store.stage_image(normalized(1), "https://img/a.jpg", pid, True)
    b, _ = store.stage_image(normalized(2), "https://img/b.jpg", pid, True)
    leads = [r[0] for r in sql.execute("SELECT image_id FROM page_image WHERE is_lead").fetchall()]
    assert leads == [b]
    store.link(pid, a, True)
    leads = [r[0] for r in sql.execute("SELECT image_id FROM page_image WHERE is_lead").fetchall()]
    assert leads == [a]


def test_faces_below_the_confidence_floor_are_not_stored(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    weak = DetectedFace(Box(1, 1, 10, 10), 0.5, tuple([0.5] * 512))
    assert store.process_pending(image_id, lambda p: [weak, face(30)], profile_id=1) == 1
    assert one(sql, "SELECT min(confidence) FROM face") == pytest.approx(0.99)


def test_size_skips_keep_original_dimensions(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    assert store is not None  # creates the schema
    with pytest.raises(ValueError, match="original dimensions"):
        Skipped(b"\x09" * 32, "too_small", "JPEG")
    with pytest.raises(psycopg.errors.CheckViolation):
        sql.execute(
            "INSERT INTO image (sha256, status, skip_reason, original_format) "
            "VALUES (%s, 'skipped', 'too_small', 'JPEG')",
            (b"\x0a" * 32,),
        )


def blocked_on_lock(sql: psycopg.Connection) -> bool:
    query = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    return one(sql, query) != 0


def test_concurrent_staging_of_same_content_links_to_winner(
    db_dsn: str, store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    pid = page(store)
    other = PostgresIndexStore(db_dsn)
    blocker = psycopg.connect(db_dsn)
    # The blocker inserts the same sha256 inside an open transaction, so `store` must wait.
    blocker.execute(
        "INSERT INTO image (sha256, status, skip_reason, original_format, original_width, "
        "original_height) VALUES (%s, 'skipped', 'too_small', 'JPEG', 40, 40)",
        (normalized().sha256,),
    )
    results: list[tuple[int, bool]] = []
    worker = threading.Thread(
        target=lambda: results.append(
            other.stage_image(normalized(), "https://img/race.jpg", pid, False)
        )
    )
    worker.start()
    # Wait until the worker's insert is actually blocked on a lock, not just slow to start.
    deadline = time.monotonic() + 10
    while not blocked_on_lock(sql) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert blocked_on_lock(sql), "the second insert never waited on the unique index"
    assert worker.is_alive()
    blocker.commit()
    worker.join(timeout=5)
    blocker.close()
    other.close()
    ((image_id, created),) = results
    assert created is False
    assert store.find_image_by_url("https://img/race.jpg") == image_id
    assert store.find_image_by_sha256(normalized().sha256) == image_id


# -- attempt cap (FR-023a) ----------------------------------------------------------------------


def test_record_failure_counts_once_per_run(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    url = "https://img/broken.jpg"
    store.record_failure(url, "image", "http 500", "run-1")
    store.record_failure(url, "image", "http 500", "run-1")  # task retry, same run
    assert one(sql, "SELECT attempts FROM fetch_attempt WHERE url = %s", url) == 1
    assert not store.is_abandoned(url, "image", 3)
    store.record_failure(url, "image", "http 500", "run-2")
    store.record_failure(url, "image", "timeout", "run-3")
    assert store.is_abandoned(url, "image", 3)
    assert one(sql, "SELECT last_error FROM fetch_attempt WHERE url = %s", url) == "timeout"
    store.record_failure(url, "image", "x", "run-4")
    assert one(sql, "SELECT attempts FROM fetch_attempt WHERE url = %s", url) == 3
    store.record_success(url, "image")
    assert not store.is_abandoned(url, "image", 3)


def test_kind_is_part_of_the_key(store: PostgresIndexStore) -> None:
    for run in ("r1", "r2", "r3"):
        store.record_failure("https://x/", "page", "http 500", run)
    assert store.is_abandoned("https://x/", "page", 3)
    assert not store.is_abandoned("https://x/", "image", 3)


def test_detect_attempts_cap(store: PostgresIndexStore) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    for run in ("r1", "r1", "r2", "r3"):
        store.record_detect_failure(image_id, run)
    assert store.pending_batches(50, 3) == []
    assert store.abandoned_pending(3) == 1


def test_stored_faces_reports_only_finished_images(store: PostgresIndexStore) -> None:
    image_id, _ = store.stage_image(normalized(), "https://img/a.jpg", page(store), False)
    assert store.stored_faces(image_id) is None  # still pending
    store.process_pending(image_id, lambda p: [face(1), face(30)], profile_id=1)
    assert store.stored_faces(image_id) == 2


def test_outdated_links_counts_one_pages_stale_links_and_deletes_nothing(
    store: PostgresIndexStore, sql: psycopg.Connection
) -> None:
    """FR-018: a link not refreshed by the page's latest crawl is counted, never removed."""
    store.init_schema()
    a = store.upsert_page("https://cnn.test/a", "cnn", 1)
    b = store.upsert_page("https://cnn.test/b", "cnn", 1)
    first, _ = store.stage_image(_outdated_image(1), "https://img.test/1.jpg", a, False)
    second, _ = store.stage_image(_outdated_image(2), "https://img.test/2.jpg", a, False)
    store.link(b, first, False)
    assert store.outdated_links(a) == 0  # fresh crawl: every link was just seen

    store.upsert_page("https://cnn.test/a", "cnn", 1)  # re-crawled ...
    store.link(a, second, False)  # ... and only the second image is still there
    assert store.outdated_links(a) == 1
    assert store.outdated_links(b) == 0
    row = sql.execute("SELECT count(*) FROM page_image").fetchone()
    assert row is not None
    assert row[0] == 3


def _outdated_image(seed: int) -> NormalizedImage:
    return NormalizedImage(
        bytes([100 + seed]) * 32, images.photo(64, 64, seed=seed), 64, 64, "JPEG", 64, 64
    )
