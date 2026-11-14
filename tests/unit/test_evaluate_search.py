"""The accuracy report for SC-001 to SC-003 (``scripts/evaluate_search.py``, research R8)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from face_index.models import PageMatch
from tests import images
from tests.fakes import FakeEmbedder, FakeSearchIndex

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "evaluate_search.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_search", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_search"] = module
    spec.loader.exec_module(module)
    return module


ev = load()

LABELS = """
[[person]]
name = "person-a"
photo = "a.jpg"
pages = ["https://cnn.test/1", "https://cnn.test/2", "https://cnn.test/3", "https://cnn.test/4", "https://cnn.test/5"]

[[absent]]
photo = "nobody.jpg"
"""


def test_labels_are_parsed() -> None:
    people, absent = ev.parse_labels(LABELS)
    assert people[0].name == "person-a"
    assert people[0].photo == "a.jpg"
    assert len(people[0].pages) == 5
    assert [a.photo for a in absent] == ["nobody.jpg"]


def test_recall_and_missed_pages_are_reported_even_when_passing() -> None:
    [person], _ = ev.parse_labels(LABELS)
    returned = [
        "https://cnn.test/1",
        "https://cnn.test/2",
        "https://cnn.test/3",
        "https://cnn.test/4",
    ]
    report = ev.evaluate_person(person, returned)
    assert report.recall == pytest.approx(0.8)
    assert report.missed == ("https://cnn.test/5",)
    assert report.extra == ()
    assert ev.passes([report], absent_pages=[0])


def test_low_recall_fails_sc001() -> None:
    [person], _ = ev.parse_labels(LABELS)
    report = ev.evaluate_person(person, ["https://cnn.test/1"])
    assert not ev.passes([report], absent_pages=[0])


def test_an_extra_page_fails_sc002() -> None:
    [person], _ = ev.parse_labels(LABELS)
    returned = [f"https://cnn.test/{n}" for n in range(1, 6)] + ["https://cnn.test/lookalike"]
    report = ev.evaluate_person(person, returned)
    assert report.extra == ("https://cnn.test/lookalike",)
    assert not ev.passes([report], absent_pages=[0])


def test_any_page_for_an_absent_person_fails_sc003() -> None:
    [person], _ = ev.parse_labels(LABELS)
    report = ev.evaluate_person(person, [f"https://cnn.test/{n}" for n in range(1, 6)])
    assert ev.passes([report], absent_pages=[0])
    assert not ev.passes([report], absent_pages=[1])


def test_run_prints_a_report_and_returns_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "a.jpg").write_bytes(images.photo(200, 150, seed=1))
    (tmp_path / "nobody.jpg").write_bytes(images.photo(200, 150, seed=2))
    labels = tmp_path / "labels.toml"
    labels.write_text(LABELS, encoding="utf-8")
    pages = [PageMatch(f"https://cnn.test/{n}", 0.1, False, ("i",)) for n in range(1, 6)]

    calls = {"n": 0}

    def pages_for(embedding: object, max_distance: float) -> list[PageMatch]:
        calls["n"] += 1
        return pages if calls["n"] == 1 else []

    index = FakeSearchIndex(pages_by_face=pages_for)
    code = ev.run(labels, index, FakeEmbedder(default_faces=1))
    out = capsys.readouterr().out
    assert code == 0
    assert "person-a" in out
    assert "1.00" in out
    assert out.strip().endswith("PASS")
