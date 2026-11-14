"""Offline smoke test: the whole pipeline, run repeatedly, on a tiny mock web (research R13).

Runs in seconds with no network and no model. Use `pytest -m smoke --durations=10` to watch speed;
there is deliberately no timing assertion (constitution IV).
"""

import logging
from typing import LiteralString

import psycopg
import pytest

from face_index.pipeline import RunParams
from tests.fixtures.minisite import minisite
from tests.fixtures.minisite.minisite import URL
from tests.smoke.runner import run_once

pytestmark = pytest.mark.smoke


def scalar(sql: psycopg.Connection, query: LiteralString, *args: object) -> object:
    row = sql.execute(query, args).fetchone()
    assert row is not None
    return row[0]


def test_first_run_builds_the_index(
    db_dsn: str, sql: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    outcome = run_once(db_dsn, minisite.mock_site(), minisite.embedder(), run_id="run-1")
    counts = outcome.summary

    # FR-008 / SC-003: one format, bounded size.
    assert (
        scalar(
            sql,
            "SELECT count(*) FROM image WHERE status = 'indexed' "
            "AND (greatest(width, height) > 1024 "
            "OR substring(jpeg from 1 for 3) <> '\\xffd8ff'::bytea)",
        )
        == 0
    )
    # FR-012 / FR-016: one row per face (1 + 2), each with a 512-d embedding.
    assert scalar(sql, "SELECT count(*) FROM face") == 3
    assert scalar(sql, "SELECT min(vector_dims(embedding)) FROM face") == 512
    assert counts["faces_stored"] == 3
    # SC-006: the same picture under two URLs, on pages of both sites, is stored once.
    assert (
        scalar(
            sql,
            "SELECT count(*) FROM image_url WHERE image_id = "
            "(SELECT image_id FROM image_url WHERE url = %s)",
            URL["face_one"],
        )
        == 2
    )
    assert (
        scalar(
            sql,
            "SELECT count(DISTINCT p.url) FROM page_image pi JOIN page p ON p.id = pi.page_id "
            "WHERE pi.image_id = (SELECT image_id FROM image_url WHERE url = %s)",
            URL["face_one"],
        )
        == 3
    )
    # FR-006a: one lead per page that declares one.
    assert (
        scalar(
            sql,
            "SELECT count(*) FROM (SELECT page_id FROM page_image WHERE is_lead "
            "GROUP BY page_id HAVING count(*) > 1) x",
        )
        == 0
    )
    assert scalar(sql, "SELECT count(*) FROM page_image WHERE is_lead") == 6
    # FR-010: skips are recorded with a reason; FR-014: no faces → no face rows.
    reasons = dict(sql.execute("SELECT skip_reason, count(*) FROM image GROUP BY 1").fetchall())
    assert reasons["unsupported_format"] == 1
    assert reasons["too_small"] == 1
    assert scalar(sql, "SELECT count(*) FROM image WHERE status = 'no_faces'") == 2
    assert (
        scalar(sql, "SELECT count(*) FROM image WHERE status = 'no_faces' AND jpeg IS NOT NULL")
        == 0
    )
    # SC-005: every face links to an image with at least one page.
    assert (
        scalar(
            sql,
            "SELECT count(*) FROM face f LEFT JOIN page_image pi USING (image_id) "
            "WHERE pi.page_id IS NULL",
        )
        == 0
    )
    # FR-023 / FR-024: failures counted and logged with URL, run still completes.
    assert counts["failures.download"] == 2
    assert f"url={URL['corrupt']}" in caplog.text
    assert f"url={URL['error']}" in caplog.text
    assert f"image skipped url={URL['svg']} reason=unsupported_format" in caplog.text
    # FR-025: the summary agrees with the tables.
    assert counts["pages_crawled.cnn"] == 3
    assert counts["pages_crawled.people"] == 3
    assert counts["images_indexed"] == 2
    assert "run summary" in caplog.text


def test_reruns_skip_known_work_and_cap_attempts(
    db_dsn: str, sql: psycopg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    site = minisite.mock_site()
    embedder = minisite.embedder(fail_sizes={"440x300"})  # detection fails on the no-face photo
    run_once(db_dsn, site, embedder, run_id="run-1")
    faces_after_first = scalar(sql, "SELECT count(*) FROM face")

    # A new page appears that shows an already-indexed image.
    site.routes["https://news.test/a2"] = minisite._page(
        URL["face_two"], ["/a3"], [URL["face_one"]]
    )
    site.routes["https://news.test/a3"] = minisite._page(URL["face_two"], [], [URL["face_two"]])
    site.requests.clear()
    second = run_once(
        db_dsn, site, embedder, run_id="run-2", params=RunParams(depth=2, page_cap=None)
    ).summary

    # SC-007: no known image downloaded again, no new embeddings, new page linked.
    downloaded_again = [u for u, _ in site.requests if u in {URL["face_one"], URL["face_two"]}]
    assert downloaded_again == []
    assert scalar(sql, "SELECT count(*) FROM face") == faces_after_first
    assert second["reused_by_url"] >= 3
    assert (
        scalar(
            sql,
            "SELECT count(*) FROM page_image pi JOIN page p ON p.id = pi.page_id "
            "WHERE p.url = 'https://news.test/a3'",
        )
        == 1
    )
    # Lead marks follow the page's current og:image.
    assert (
        scalar(
            sql,
            "SELECT i.url FROM page_image pi JOIN page p ON p.id = pi.page_id "
            "JOIN image_url i ON i.image_id = pi.image_id WHERE p.url = 'https://news.test/a2' "
            "AND pi.is_lead",
        )
        == URL["face_two"]
    )

    # FR-023a / SC-012: a failing link is tried in runs 1-3, then abandoned in run 4.
    assert scalar(sql, "SELECT attempts FROM fetch_attempt WHERE url = %s", URL["error"]) == 2
    run_once(db_dsn, site, embedder, run_id="run-3")
    assert scalar(sql, "SELECT attempts FROM fetch_attempt WHERE url = %s", URL["error"]) == 3
    site.requests.clear()
    caplog.set_level(logging.INFO)
    fourth = run_once(db_dsn, site, embedder, run_id="run-4").summary
    assert not site.requested(URL["error"])
    assert fourth["abandoned.image"] >= 2  # the 500 URL and the corrupt JPEG
    assert f"link abandoned url={URL['error']}" in caplog.text
    # Detection on the failing image was tried 3 times, then abandoned; it stays invisible.
    assert scalar(sql, "SELECT detect_attempts FROM image WHERE status = 'pending'") == 3
    assert fourth["abandoned.detect"] == 1


def test_params_bound_the_run(db_dsn: str, sql: psycopg.Connection) -> None:
    site = minisite.mock_site()
    run_once(db_dsn, site, minisite.embedder(), "run-1", RunParams(depth=0, page_cap=None))
    assert scalar(sql, "SELECT count(*) FROM page") == 2  # the two home pages
    run_once(db_dsn, site, minisite.embedder(), "run-2", RunParams(depth=1, page_cap=1))
    assert scalar(sql, "SELECT count(*) FROM page") == 2  # cap of 1 page per site
