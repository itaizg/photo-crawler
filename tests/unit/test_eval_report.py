"""The CI report page (scripts/eval_report.py): built from the committed files only (R12)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

from tests.evalset import ABSENT, PRESENT, page, write_folder
from tests.fakes import FakeEmbedder, FakeSearchIndex
from tests.unit.test_evaluate_search import by_label, ev

SCRIPT: Final[Path] = Path(__file__).resolve().parents[2] / "scripts" / "eval_report.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_report", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_report"] = module
    spec.loader.exec_module(module)
    return module


report: Final[ModuleType] = load()


def logged_folder(root: Path) -> Path:
    """A valid folder with one logged run: person-a has a wrong page beyond the threshold."""
    write_folder(root)
    found = {label: [(page(2 * i), 0.1), (page(2 * i + 1), 0.2)] for i, label in enumerate(PRESENT)}
    found["person-a"].append((page(9), 0.42))
    found["absent-a"] = [(page(8), 0.25)]  # a false match: the run fails
    index = FakeSearchIndex(pages_by_face=by_label(found))
    embedder = FakeEmbedder(default_faces=1)
    assert ev.run(root, None, index, embedder, log_to=root / "runs.csv") == ev.EXIT_FAIL
    return root


def test_the_report_shows_the_latest_run_its_distances_and_history(tmp_path: Path) -> None:
    root = logged_folder(tmp_path)
    out = tmp_path / "report.html"
    summary = tmp_path / "summary.md"
    assert report.main([str(root), "--out", str(out), "--summary", str(summary)]) == 0
    page_html = out.read_text(encoding="utf-8")
    assert page_html.startswith("<!doctype html>")
    assert "FAIL" in page_html
    assert all(label in page_html for label in (*PRESENT, *ABSENT))
    assert page_html.count('class="true"') >= 2 * len(PRESENT)  # one circle per labelled page
    assert "distance 0.420" in page_html
    assert "Run history" in page_html
    text = summary.read_text(encoding="utf-8")
    assert text.startswith("## Search evaluation: FAIL")
    assert "report with page distances is attached" in text
    assert "| absent-a | 0 | 1 | - | 1 |" in text


def test_a_run_logged_before_distances_says_so(tmp_path: Path) -> None:
    root = logged_folder(tmp_path)
    (root / ev.eval_set.DISTANCES_FILE).unlink()
    page_html, summary, valid = report.render(root)
    assert valid
    assert "logged before page distances were recorded" in page_html
    assert "logged before page distances were recorded" in summary


def test_invalid_files_are_listed_and_exit_2(tmp_path: Path) -> None:
    root = write_folder(tmp_path)
    (root / "methods.csv").unlink()
    out = tmp_path / "report.html"
    assert report.main([str(root), "--out", str(out)]) == 2
    assert "methods.csv: missing" in out.read_text(encoding="utf-8")


def test_a_stale_run_is_flagged(tmp_path: Path) -> None:
    root = logged_folder(tmp_path)
    (root / "photos" / "person-a.jpg").write_bytes((root / "photos" / "person-b.jpg").read_bytes())
    page_html, summary, _ = report.render(root)
    assert "results are stale" in page_html
    assert "results are stale" in summary
