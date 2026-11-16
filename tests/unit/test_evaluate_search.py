"""The accuracy report for SC-001 to SC-003 (``scripts/evaluate_search.py``, feature 003).

Contract: specs/003-evaluation-set/contracts/evaluate-search-cli.md.
"""

import hashlib
import importlib.util
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from face_index import model_profile
from face_index.models import PageMatch
from tests import images
from tests.evalset import (
    ABSENT,
    PRESENT,
    default_rows,
    page,
    photo_bytes,
    write_csv,
    write_folder,
)
from tests.fakes import FakeEmbedder, FakeSearchIndex

SCRIPT: Final[Path] = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_search.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_search", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_search"] = module
    spec.loader.exec_module(module)
    return module


ev: Final[ModuleType] = load()


def match(url: str) -> PageMatch:
    return PageMatch(url=url, best_distance=0.1, lead=False, image_urls=("https://img.test/i",))


def by_label(pages: dict[str, list[str]]) -> Callable[[Sequence[float], float], list[PageMatch]]:
    """A ``pages_by_face`` that answers per label, recognising each label's photo embedding."""
    embedder = FakeEmbedder(default_faces=1)
    keyed: dict[tuple[float, ...], list[str]] = {}
    for label in (*PRESENT, *ABSENT):
        normalized = ev.PillowNormalizer().normalize(photo_bytes(label), None)
        [face] = embedder.embed(normalized.jpeg)
        keyed[face.embedding] = pages.get(label, [])

    def answer(embedding: Sequence[float], max_distance: float) -> list[PageMatch]:
        return [match(u) for u in keyed.get(tuple(embedding), [])]

    return answer


def all_found() -> dict[str, list[str]]:
    return {label: [page(2 * i), page(2 * i + 1)] for i, label in enumerate(PRESENT)}


def test_recall_and_missed_pages_are_reported_even_when_passing() -> None:
    pages = tuple(f"https://cnn.test/{n}" for n in range(1, 6))
    report = ev.evaluate_present("person-a", pages, pages[:4], known=frozenset(pages))
    assert report.recall == pytest.approx(0.8)
    assert report.missed == ("https://cnn.test/5",)
    assert report.extra == ()
    assert ev.passes([report])


def test_low_recall_fails_sc001() -> None:
    pages = ("https://cnn.test/1", "https://cnn.test/2")
    report = ev.evaluate_present("person-a", pages, pages[:1], known=frozenset(pages))
    assert report.recall == pytest.approx(0.5)
    assert not ev.passes([report])


def test_an_extra_page_fails_sc002() -> None:
    pages = ("https://cnn.test/1",)
    report = ev.evaluate_present(
        "person-a", pages, [*pages, "https://cnn.test/lookalike"], known=frozenset(pages)
    )
    assert report.extra == ("https://cnn.test/lookalike",)
    assert not ev.passes([report])


def test_any_page_for_an_absent_label_fails_sc003() -> None:
    assert ev.passes([ev.evaluate_absent("absent-a", [])])
    report = ev.evaluate_absent("absent-a", ["https://cnn.test/1", "https://cnn.test/1"])
    assert report.returned == 1
    assert report.extra == ("https://cnn.test/1",)
    assert not ev.passes([report])


def test_a_page_missing_from_the_index_is_a_label_error_not_a_miss() -> None:
    pages = ("https://cnn.test/1", "https://cnn.test/gone")
    report = ev.evaluate_present(
        "person-a", pages, ["https://cnn.test/1"], known=frozenset({"https://cnn.test/1"})
    )
    assert report.label_errors == ("https://cnn.test/gone",)
    assert report.expected == 1
    assert report.missed == ()
    assert report.recall == 1.0
    assert not ev.passes([report])


def test_run_prints_the_report_and_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    index = FakeSearchIndex(pages_by_face=by_label(all_found()))
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1)) == ev.EXIT_PASS
    out = capsys.readouterr().out
    assert "label set 2026-11-20" in out
    assert f"threshold {model_profile.MATCH_THRESHOLD:.2f}" in out
    assert "index: 10 faces, 5 images, 3 pages" in out
    assert "person-a" in out
    assert "absent-e" in out
    assert out.strip().endswith("PASS")
    assert {threshold for _, threshold, _ in index.calls} == {model_profile.MATCH_THRESHOLD}
    assert len(index.calls) == len(PRESENT) + len(ABSENT)


def test_run_reports_a_label_error_and_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    known = {u for _, u, _, _ in default_rows() if u and u != page(3)}
    index = FakeSearchIndex(pages_by_face=by_label(all_found()), known=known)
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1)) == ev.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"label error (page not indexed): {page(3)}" in out
    assert "missed:" not in out
    assert out.strip().endswith("FAIL")


def test_run_reports_misses_and_extras(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = write_folder(tmp_path)
    found = all_found()
    found["person-b"] = [page(2)]
    found["absent-c"] = [page(9)]
    index = FakeSearchIndex(pages_by_face=by_label(found))
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1)) == ev.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"missed: {page(3)}" in out
    assert f"extra:  {page(9)}" in out


def test_every_unusable_photo_is_named_before_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    (root / "photos" / "absent-e.jpg").write_bytes(images.corrupt_jpeg())
    no_face = {
        hashlib.sha256(ev.PillowNormalizer().normalize(photo_bytes(label), None).jpeg).hexdigest()
        for label in ("person-b", "absent-c")
    }
    embedder = FakeEmbedder(faces_by_prefix=dict.fromkeys(no_face, 0), default_faces=1)
    index = FakeSearchIndex()
    assert ev.run(root, None, index, embedder) == ev.EXIT_CANNOT_EVALUATE
    err = capsys.readouterr().err
    assert "photos/person-b.jpg: no face detected" in err
    assert "photos/absent-c.jpg: no face detected" in err
    assert "photos/absent-e.jpg: not a decodable image" in err
    assert index.calls == []


def test_a_missing_profile_cannot_be_evaluated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    index = FakeSearchIndex(profiles={})
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1)) == ev.EXIT_CANNOT_EVALUATE
    assert "no faces for profile" in capsys.readouterr().err


def test_invalid_files_cannot_be_evaluated(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    (root / "photos" / "person-a.jpg").unlink()
    index = FakeSearchIndex()
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1)) == ev.EXIT_CANNOT_EVALUATE
    assert "photos/person-a.jpg" in capsys.readouterr().err
    assert index.calls == []


def test_an_old_toml_labels_file_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = tmp_path / "labels.toml"
    labels.write_text("[[person]]\n", encoding="utf-8")
    assert ev.main([str(labels)]) == ev.EXIT_USAGE
    assert "now CSV label sets" in capsys.readouterr().err


def test_log_and_check_together_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        ev.main([str(write_folder(tmp_path)), "--check", "--log"])
    assert exit_info.value.code == ev.EXIT_USAGE


def test_an_unknown_set_lists_the_available_ones(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    assert ev.main([str(root), "--set", "2026-01-01"]) == ev.EXIT_USAGE
    assert "2026-11-20" in capsys.readouterr().err


# --- run log (US4) ------------------------------------------------------------------------------


def passing_run(root: Path, log_to: Path | None) -> int:
    index = FakeSearchIndex(pages_by_face=by_label(all_found()))
    return ev.run(root, None, index, FakeEmbedder(default_faces=1), log_to=log_to)


def test_a_logged_run_appends_one_row_per_label(tmp_path: Path) -> None:
    root = write_folder(tmp_path)
    runs = root / "runs.csv"
    runs.unlink()
    assert passing_run(root, runs) == ev.EXIT_PASS
    rows, errors = ev.eval_set.read_run_log(root)
    assert errors == []
    assert [r.label for r in rows] == [*PRESENT, *ABSENT]
    first = rows[0]
    assert first.label_set.isoformat() == "2026-11-20"
    assert first.model_profile == model_profile.KEY
    assert first.threshold == model_profile.MATCH_THRESHOLD
    assert (first.faces, first.images, first.pages) == (10, 5, 3)
    assert (first.expected, first.returned, first.missed, first.extra) == (2, 2, 0, 0)
    assert {r.verdict for r in rows} == {"PASS"}
    assert len({(r.run_at, r.labels_sha256) for r in rows}) == 1
    assert "http" not in runs.read_text(encoding="utf-8")


def test_a_second_run_appends_without_touching_the_first(tmp_path: Path) -> None:
    root = write_folder(tmp_path)
    runs = root / "runs.csv"
    passing_run(root, runs)
    before = runs.read_bytes()
    found = all_found()
    found["absent-b"] = [page(9)]
    index = FakeSearchIndex(pages_by_face=by_label(found))
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1), log_to=runs) == ev.EXIT_FAIL
    after = runs.read_bytes()
    assert after.startswith(before)
    rows, _ = ev.eval_set.read_run_log(root)
    assert len(rows) == 2 * (len(PRESENT) + len(ABSENT))
    assert {r.verdict for r in rows[len(PRESENT) + len(ABSENT) :]} == {"FAIL"}


def test_a_run_that_cannot_evaluate_logs_nothing(tmp_path: Path) -> None:
    root = write_folder(tmp_path)
    runs = root / "runs.csv"
    before = runs.read_bytes()
    index = FakeSearchIndex(profiles={})
    assert ev.run(root, None, index, FakeEmbedder(default_faces=1), log_to=runs) == 2
    assert runs.read_bytes() == before


def test_labels_version_follows_the_set_and_its_photos(tmp_path: Path) -> None:
    root = write_folder(tmp_path)
    folder, _ = ev.eval_set.read_folder(root)
    assert folder is not None
    label_set = ev.eval_set.latest_set(folder)
    version = ev.eval_set.labels_sha256(root, label_set)
    assert len(version) == 12
    assert ev.eval_set.labels_sha256(root, label_set) == version
    (root / "photos" / "absent-c.jpg").write_bytes(images.photo(160, 120, seed=999))
    assert ev.eval_set.labels_sha256(root, label_set) != version


def test_check_rejects_stale_results_until_a_fresh_run_is_logged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = write_folder(tmp_path)
    assert ev.main([str(root), "--check"]) == ev.EXIT_CANNOT_EVALUATE  # no run logged yet
    assert "2026-11-20" in capsys.readouterr().out

    passing_run(root, root / "runs.csv")
    assert ev.main([str(root), "--check"]) == ev.EXIT_PASS

    (root / "photos" / "absent-c.jpg").write_bytes(images.photo(160, 120, seed=999))  # a fix
    assert ev.main([str(root), "--check"]) == ev.EXIT_CANNOT_EVALUATE
    assert "stale" in capsys.readouterr().out

    assert passing_run(root, root / "runs.csv") == ev.EXIT_PASS  # the corrected set still runs
    assert ev.main([str(root), "--check"]) == ev.EXIT_PASS


def test_stale_results_only_concern_the_latest_set(tmp_path: Path) -> None:
    write_folder(tmp_path, crawl_date=date(2026, 11, 1))
    write_folder(tmp_path)
    write_csv(
        tmp_path / "methods.csv",
        ("crawl_date", "found_by", "labeller", "snapshot_holder", "onsite_pair"),
        [
            ("2026-11-01", "browse", "labeller", "labeller", "false"),
            ("2026-11-20", "both", "labeller", "labeller", "false"),
        ],
    )
    passing_run(tmp_path, tmp_path / "runs.csv")  # logs the latest set only
    assert ev.main([str(tmp_path), "--check"]) == ev.EXIT_PASS
