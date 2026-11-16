"""Loading and validating the evaluation folder (feature 003, contracts/eval-set-files.md)."""

from datetime import date
from pathlib import Path

import pytest

from face_index import eval_set
from tests.evalset import CRAWL, SET_HEADER, Row, default_rows, page, write_csv, write_folder

SET_FILE = f"sets/{CRAWL.isoformat()}.csv"


def errors_for(root: Path) -> tuple[str, ...]:
    folder, errors = eval_set.read_folder(root)
    assert folder is None
    assert errors
    return errors


def with_row(index: int, row: Row) -> list[Row]:
    rows = default_rows()
    rows[index] = row
    return rows


def test_a_valid_folder_loads_with_derived_photo_and_role(tmp_path: Path) -> None:
    folder, errors = eval_set.read_folder(write_folder(tmp_path))
    assert errors == ()
    assert folder is not None
    label_set = eval_set.latest_set(folder)
    assert label_set.crawl_date == CRAWL
    present = label_set.present()
    assert sorted(present) == [f"person-{c}" for c in "abcde"]
    assert present["person-a"] == (page(0), page(1))
    assert label_set.absent() == tuple(f"absent-{c}" for c in "abcde")
    assert eval_set.photo_path("person-a") == "photos/person-a.jpg"
    assert eval_set.role("person-a") == "present"
    assert eval_set.role("absent-a") == "absent"


def test_the_latest_set_is_the_default_and_others_can_be_chosen(tmp_path: Path) -> None:
    write_folder(tmp_path, crawl_date=date(2026, 11, 1))
    write_folder(tmp_path)  # 2026-11-20, rewrites the shared files for this date
    write_csv(
        tmp_path / "methods.csv",
        ("crawl_date", "found_by", "labeller", "snapshot_holder", "onsite_pair"),
        [
            ("2026-11-01", "browse", "labeller", "labeller", "false"),
            ("2026-11-20", "both", "labeller", "labeller", "false"),
        ],
    )
    folder, errors = eval_set.read_folder(tmp_path)
    assert errors == ()
    assert folder is not None
    assert eval_set.latest_set(folder).crawl_date == CRAWL
    assert folder.set_for(date(2026, 11, 1)) is not None
    assert folder.set_for(date(2026, 10, 1)) is None
    assert folder.dates() == (date(2026, 11, 1), CRAWL)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (("label", "page_url", "hard_case"), "header"),
        (("label", "page_url", "lookalike_of", "hard_case"), "header"),
    ],
)
def test_a_wrong_or_missing_column_is_reported_on_line_1(
    tmp_path: Path, header: tuple[str, ...], expected: str
) -> None:
    write_folder(tmp_path)
    write_csv(tmp_path / SET_FILE, header, [])
    [error] = [e for e in errors_for(tmp_path) if e.startswith(SET_FILE)]
    assert error.startswith(f"{SET_FILE}:1:")
    assert expected in error


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        (("person-a", page(0), "tiny", ""), "hard_case"),
        (("person-a", "http://edition.cnn.com/2026/11/20/story-0", "none", ""), "https"),
        (("person-a", "https://example.com/story", "none", ""), "indexed site"),
        (("person-a", page(0) + "/", "none", ""), "canonical"),
        (("person-a", page(0) + "?utm_source=x", "none", ""), "canonical"),
        (("person-a", "", "none", ""), "page"),
        (("absent-b", page(50), "none", ""), "absent"),
        (("person-a", page(0), "look_alike", ""), "lookalike_of"),
        (("person-a", page(0), "none", "person-b"), "lookalike_of"),
        (("Person-A", page(0), "none", ""), "label"),
        (("absent-b", "", "small_face", ""), "small_face"),
    ],
)
def test_a_bad_row_is_reported_with_its_line(tmp_path: Path, row: Row, fragment: str) -> None:
    index = 0 if row[0] != "absent-b" else 11
    write_folder(tmp_path, with_row(index, row))
    errors = errors_for(tmp_path)
    line = index + 2
    matching = [e for e in errors if e.startswith(f"{SET_FILE}:{line}:")]
    assert matching, errors
    assert fragment in matching[0]


def test_look_alike_must_point_to_a_present_label_of_the_same_set(tmp_path: Path) -> None:
    write_folder(tmp_path, with_row(10, ("absent-a", "", "look_alike", "person-z")))
    errors = errors_for(tmp_path)
    assert any(e.startswith(f"{SET_FILE}:12:") and "person-z" in e for e in errors), errors


def test_a_duplicate_row_is_reported(tmp_path: Path) -> None:
    rows = [*default_rows(), ("person-b", page(2), "none", "")]
    write_folder(tmp_path, rows)
    errors = errors_for(tmp_path)
    assert any(e.startswith(f"{SET_FILE}:{len(rows) + 1}:") and "duplicate" in e for e in errors)


def test_an_absent_label_appears_once(tmp_path: Path) -> None:
    rows = [*default_rows(), ("absent-b", "", "none", "")]
    write_folder(tmp_path, rows)
    errors = errors_for(tmp_path)
    assert any(e.startswith(f"{SET_FILE}:{len(rows) + 1}:") for e in errors), errors


def test_na_and_empty_cells_stay_text(tmp_path: Path) -> None:
    write_folder(tmp_path, with_row(0, ("person-a", page(0), "NA", "")))
    [error] = [e for e in errors_for(tmp_path) if e.startswith(f"{SET_FILE}:2:")]
    assert "'NA'" in error


def test_a_set_file_name_must_be_an_iso_date(tmp_path: Path) -> None:
    write_folder(tmp_path)
    write_csv(tmp_path / "sets" / "november.csv", SET_HEADER, default_rows())
    assert any(e.startswith("sets/november.csv") for e in errors_for(tmp_path))


def test_only_csv_files_named_by_date_are_sets(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / "sets" / f"{CRAWL.isoformat()}.txt").write_text("notes", encoding="utf-8")
    assert any(e.startswith(f"sets/{CRAWL.isoformat()}.txt") for e in errors_for(tmp_path))


def test_a_folder_without_sets_is_an_error(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / SET_FILE).unlink()
    assert any("no label set" in e for e in errors_for(tmp_path))


def test_a_label_without_its_photo_is_an_error(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / "photos" / "person-c.jpg").unlink()
    assert any("photos/person-c.jpg" in e for e in errors_for(tmp_path))


def test_every_broken_file_is_reported_in_one_pass(tmp_path: Path) -> None:
    write_folder(tmp_path, with_row(0, ("person-a", page(0), "tiny", "")))
    write_csv(
        tmp_path / "sets" / "2026-11-01.csv",
        SET_HEADER,
        [("person-a", "https://example.com/x", "none", "")],
    )
    errors = errors_for(tmp_path)
    assert any(e.startswith(f"{SET_FILE}:2:") for e in errors)
    assert any(e.startswith("sets/2026-11-01.csv:2:") for e in errors)


def test_an_unreadable_csv_is_reported_not_raised(tmp_path: Path) -> None:
    write_folder(tmp_path)
    (tmp_path / SET_FILE).write_bytes(b"\xff\xfe\x00broken")
    assert any(e.startswith(SET_FILE) for e in errors_for(tmp_path))


# --- set minimums, methods and run log (US2) --------------------------------------------------


def test_minimum_counts_are_per_set(tmp_path: Path) -> None:
    rows = [r for r in default_rows() if r[0] not in ("person-e", "absent-e")]
    write_folder(tmp_path, rows)
    errors = errors_for(tmp_path)
    assert any(e.startswith(SET_FILE) and "5 present labels" in e for e in errors), errors
    assert any(e.startswith(SET_FILE) and "5 absent labels" in e for e in errors), errors


def test_an_absent_look_alike_and_a_small_face_page_are_required(tmp_path: Path) -> None:
    rows = [(label, url, "none", "") for label, url, _, _ in default_rows()]
    write_folder(tmp_path, rows)
    errors = errors_for(tmp_path)
    assert any("absent look-alike" in e for e in errors), errors
    assert any("small_face" in e for e in errors), errors


def test_onsite_pair_must_agree_with_the_set(tmp_path: Path) -> None:
    write_folder(tmp_path, onsite_pair=True)
    assert any(e.startswith("methods.csv:2:") and "onsite_pair" in e for e in errors_for(tmp_path))

    pair = with_row(2, ("person-b", page(2), "look_alike", "person-a"))
    write_folder(tmp_path, pair, onsite_pair=False)
    assert any(e.startswith("methods.csv:2:") and "onsite_pair" in e for e in errors_for(tmp_path))

    write_folder(tmp_path, pair, onsite_pair=True)
    assert eval_set.read_folder(tmp_path)[1] == ()


def test_every_set_has_a_methods_row_and_every_row_a_set(tmp_path: Path) -> None:
    write_folder(tmp_path)
    write_csv(
        tmp_path / "methods.csv",
        ("crawl_date", "found_by", "labeller", "snapshot_holder", "onsite_pair"),
        [("2026-10-01", "both", "labeller", "labeller", "false")],
    )
    errors = errors_for(tmp_path)
    assert any(e.startswith(SET_FILE) and "methods.csv" in e for e in errors), errors
    assert any(e.startswith("methods.csv:2:") and "2026-10-01" in e for e in errors), errors


@pytest.mark.parametrize(
    "row",
    [
        ("2026-11-20", "guessed", "labeller", "labeller", "false"),
        ("2026-11-20", "both", "labeller", "labeller", "yes"),
        ("2026-11-20", "both", "", "labeller", "false"),
    ],
)
def test_a_bad_methods_row_is_reported(tmp_path: Path, row: Row) -> None:
    write_folder(tmp_path)
    write_csv(
        tmp_path / "methods.csv",
        ("crawl_date", "found_by", "labeller", "snapshot_holder", "onsite_pair"),
        [row],
    )
    assert any(e.startswith("methods.csv:2:") for e in errors_for(tmp_path))


def test_a_bad_run_log_is_reported(tmp_path: Path) -> None:
    write_folder(tmp_path)
    runs = tmp_path / "runs.csv"
    runs.write_text(runs.read_text(encoding="utf-8").replace("verdict", "result"), encoding="utf-8")
    assert any(e.startswith("runs.csv:1:") for e in errors_for(tmp_path))

    write_folder(tmp_path)
    good = (
        "2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,facenet512,0.30,"
        "10,5,3,person-a,2,2,0,0,0,PASS\n"
    )
    with runs.open("a", encoding="utf-8") as f:
        f.write(good)
        f.write(good.replace(",2,2,0,0,0,PASS", ",two,2,0,0,0,PASS"))
        f.write(good.replace("PASS", "MAYBE"))
    errors = errors_for(tmp_path)
    assert not any(e.startswith("runs.csv:2:") for e in errors), errors
    assert any(e.startswith("runs.csv:3:") for e in errors), errors
    assert any(e.startswith("runs.csv:4:") for e in errors), errors
