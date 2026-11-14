"""The ``face_matcher`` command line (contracts/face-matcher-cli.md) with fakes.

No database and no model: the index is ``FakeSearchIndex`` and faces come from ``FakeEmbedder``
or a recording embedder. The real ``PillowNormalizer`` is used (pure Pillow).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from face_index import face_matcher, model_profile
from face_index.errors import InfrastructureError
from face_index.models import Box, DetectedFace, PageMatch
from face_index.search_store import PostgresSearchIndex
from tests import images
from tests.fakes import FakeEmbedder, FakeSearchIndex

SRC = Path(__file__).resolve().parents[2] / "src"


class OrderLog:
    """Records the order in which the index and the embedder are used."""

    def __init__(self, faces: int = 1) -> None:
        self.events: list[str] = []
        self.index = FakeSearchIndex(
            pages=[
                PageMatch(
                    url="https://cnn.test/a", best_distance=0.1, lead=False, image_urls=("i",)
                )
            ]
        )
        self._embedder = FakeEmbedder(default_faces=faces)

    def index_factory(self) -> FakeSearchIndex:
        self.events.append("index")
        return self.index

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        self.events.append("embed")
        return self._embedder.embed(jpeg)


def photo_file(tmp_path: Path, name: str = "one.jpg", data: bytes | None = None) -> Path:
    path = tmp_path / name
    path.write_bytes(data if data is not None else images.photo(200, 150, seed=4))
    return path


def run(
    argv: list[str],
    index: FakeSearchIndex | None = None,
    faces: int = 1,
) -> int:
    fake = index if index is not None else FakeSearchIndex()
    return face_matcher.main(
        argv, index_factory=lambda: fake, embedder=FakeEmbedder(default_faces=faces)
    )


PAGES = [
    PageMatch(
        url="https://cnn.test/close",
        best_distance=0.112,
        lead=True,
        image_urls=("https://img.test/a.jpg",),
    ),
    PageMatch(
        url="https://cnn.test/mid",
        best_distance=0.187,
        lead=False,
        image_urls=("https://img.test/b.jpg", "https://img.test/c.jpg"),
    ),
    PageMatch(
        url="https://cnn.test/far",
        best_distance=0.241,
        lead=False,
        image_urls=("https://img.test/d.jpg",),
    ),
]


# -- default output (FR-006 to FR-009) --------------------------------------------------------


def test_default_output_lists_page_urls_under_a_face_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = photo_file(tmp_path)
    assert run([str(path)], FakeSearchIndex(pages=PAGES)) == face_matcher.EXIT_OK
    out = capsys.readouterr().out
    assert out == (
        "# face 1 x=4 y=4 w=16 h=16: 3 pages\n"
        "https://cnn.test/close\n"
        "https://cnn.test/mid\n"
        "https://cnn.test/far\n"
    )


def test_one_page_is_singular(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([str(photo_file(tmp_path))], FakeSearchIndex(pages=PAGES[:1])) == 0
    assert capsys.readouterr().out.splitlines()[0] == "# face 1 x=4 y=4 w=16 h=16: 1 page"


def test_no_match_is_reported_and_succeeds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(photo_file(tmp_path))]) == face_matcher.EXIT_OK
    assert capsys.readouterr().out == "# face 1 x=4 y=4 w=16 h=16: no matching pages\n"


def test_default_threshold_is_the_model_profiles(tmp_path: Path) -> None:
    index = FakeSearchIndex()
    run([str(photo_file(tmp_path))], index)
    assert [c[1] for c in index.calls] == [model_profile.MATCH_THRESHOLD]
    assert index.closed


# -- query file problems: exit 2 (FR-014) -----------------------------------------------------


def test_missing_file_hints_at_the_queries_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(tmp_path / "missing.jpg")]) == face_matcher.EXIT_BAD_IMAGE
    err = capsys.readouterr().err
    assert "missing.jpg" in err
    assert "queries/" in err


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any file")
def test_unreadable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], request: pytest.FixtureRequest
) -> None:
    path = photo_file(tmp_path)
    path.chmod(0o000)
    request.addfinalizer(lambda: path.chmod(0o644))  # so pytest can remove tmp_path
    assert run([str(path)]) == face_matcher.EXIT_BAD_IMAGE
    assert "cannot read" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not an image at all", "not a decodable image"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "SVG"),
        (images.photo(40, 40), "image is 40x40; at least 64 px"),
    ],
)
def test_unusable_files_exit_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], data: bytes, message: str
) -> None:
    assert run([str(photo_file(tmp_path, "q.jpg", data))]) == face_matcher.EXIT_BAD_IMAGE
    err = capsys.readouterr().err
    assert err.splitlines()[-1].startswith("error: ")  # after the progress line
    assert message in err


def test_no_face_exits_3(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([str(photo_file(tmp_path))], faces=0) == face_matcher.EXIT_NO_FACE
    assert "no face detected" in capsys.readouterr().err


# -- index problems: exit 1 and 4 -------------------------------------------------------------


def test_database_unavailable_exits_1_without_the_dsn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    index = FakeSearchIndex(raise_on_connect=InfrastructureError("database unavailable: X"))
    assert run([str(photo_file(tmp_path))], index) == face_matcher.EXIT_UNAVAILABLE
    err = capsys.readouterr().err
    assert "database unavailable" in err
    assert "postgresql://" not in err


def test_real_adapter_on_an_unreachable_database_exits_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dsn = "postgresql://secretuser@127.0.0.1:1/nowhere?connect_timeout=1"
    code = face_matcher.main(
        [str(photo_file(tmp_path))],
        index_factory=lambda: PostgresSearchIndex(dsn),
        embedder=FakeEmbedder(default_faces=1),
    )
    assert code == face_matcher.EXIT_UNAVAILABLE
    assert "secretuser" not in capsys.readouterr().err


def test_missing_profile_exits_4(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    index = FakeSearchIndex(profiles={"some-other-profile": 1})
    assert run([str(photo_file(tmp_path))], index) == face_matcher.EXIT_INDEX_NOT_READY
    err = capsys.readouterr().err
    assert model_profile.KEY in err
    assert "run the image_indexer pipeline first" in err


def test_empty_index_exits_4(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run([str(photo_file(tmp_path))], FakeSearchIndex(face_count=0))
        == face_matcher.EXIT_INDEX_NOT_READY
    )
    assert "no faces" in capsys.readouterr().err


def test_the_index_is_checked_before_the_model_runs(tmp_path: Path) -> None:
    log = OrderLog()
    face_matcher.main([str(photo_file(tmp_path))], index_factory=log.index_factory, embedder=log)
    assert log.events == ["index", "embed"]


def test_unexpected_errors_propagate_and_still_close_the_index(tmp_path: Path) -> None:
    class Broken:
        def embed(self, jpeg: bytes) -> list[DetectedFace]:
            raise ZeroDivisionError("boom")

    index = FakeSearchIndex()
    with pytest.raises(ZeroDivisionError, match="boom"):
        face_matcher.main(
            [str(photo_file(tmp_path))], index_factory=lambda: index, embedder=Broken()
        )
    assert index.closed


def test_the_script_does_not_import_airflow() -> None:
    code = "import sys, face_index.face_matcher; assert 'airflow' not in sys.modules"
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)


def test_box_header_uses_the_face_box(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    class One:
        def embed(self, jpeg: bytes) -> list[DetectedFace]:
            return [
                DetectedFace(
                    box=Box(x=212, y=96, w=118, h=151), confidence=0.99, embedding=(0.0,) * 512
                )
            ]

    face_matcher.main([str(photo_file(tmp_path))], index_factory=FakeSearchIndex, embedder=One())
    assert capsys.readouterr().out.startswith("# face 1 x=212 y=96 w=118 h=151: ")


# -- US2: threshold and details (FR-005, FR-008) ----------------------------------------------


class NeverCalled:
    def __init__(self) -> None:
        self.used = False

    def index(self) -> FakeSearchIndex:
        self.used = True
        return FakeSearchIndex()

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        self.used = True
        return []


@pytest.mark.parametrize("value", ["0", "-1", "2.5", "abc", "nan"])
def test_bad_thresholds_are_usage_errors_before_any_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    probe = NeverCalled()
    with pytest.raises(SystemExit) as exited:
        face_matcher.main(
            [str(photo_file(tmp_path)), "--threshold", value],
            index_factory=probe.index,
            embedder=probe,
        )
    assert exited.value.code == face_matcher.EXIT_USAGE
    assert "(0, 2]" in capsys.readouterr().err
    assert not probe.used


def test_threshold_two_is_accepted_and_passed_on(tmp_path: Path) -> None:
    index = FakeSearchIndex()
    assert run([str(photo_file(tmp_path)), "--threshold", "2"], index) == 0
    assert [c[1] for c in index.calls] == [2.0]


def test_details_show_distance_lead_and_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(photo_file(tmp_path)), "--details"], FakeSearchIndex(pages=PAGES)) == 0
    assert capsys.readouterr().out == (
        "# face 1 x=4 y=4 w=16 h=16: 3 pages\n"
        "0.112 lead https://cnn.test/close\n"
        "    image: https://img.test/a.jpg\n"
        "0.187      https://cnn.test/mid\n"
        "    image: https://img.test/b.jpg\n"
        "    image: https://img.test/c.jpg\n"
        "0.241      https://cnn.test/far\n"
        "    image: https://img.test/d.jpg\n"
    )


def test_without_details_no_scores_or_images(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run([str(photo_file(tmp_path))], FakeSearchIndex(pages=PAGES))
    out = capsys.readouterr().out
    assert "0.1" not in out
    assert "img.test" not in out
    assert "lead" not in out


# -- US3: several faces, --face and --list-faces (FR-010 to FR-012) ---------------------------


def test_two_faces_give_two_groups(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run([str(photo_file(tmp_path))], FakeSearchIndex(pages=PAGES[:1]), faces=2) == 0
    assert capsys.readouterr().out == (
        "# face 1 x=4 y=4 w=16 h=16: 1 page\n"
        "https://cnn.test/close\n"
        "\n"
        "# face 2 x=24 y=4 w=16 h=16: 1 page\n"
        "https://cnn.test/close\n"
    )


def test_face_option_searches_one_face(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    index = FakeSearchIndex()
    assert run([str(photo_file(tmp_path)), "--face", "2"], index, faces=3) == 0
    assert capsys.readouterr().out == "# face 2 x=24 y=4 w=16 h=16: no matching pages\n"
    assert len(index.calls) == 1


def test_face_beyond_the_count_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run([str(photo_file(tmp_path)), "--face", "9"], faces=2) == face_matcher.EXIT_USAGE
    assert "--face 9, but only 2 faces were found" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_face_must_be_a_positive_integer(tmp_path: Path, value: str) -> None:
    with pytest.raises(SystemExit) as exited:
        run([str(photo_file(tmp_path)), "--face", value])
    assert exited.value.code == face_matcher.EXIT_USAGE


def test_list_faces_prints_boxes_and_saves_a_numbered_copy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "queries").mkdir()
    photo_file(tmp_path / "queries", "group.jpg")
    index = FakeSearchIndex()
    assert run(["queries/group.jpg", "--list-faces"], index, faces=2) == 0
    assert capsys.readouterr().out == (
        "# 2 faces in queries/group.jpg (normalized to 200x150)\n"
        "1 x=4 y=4 w=16 h=16\n"
        "2 x=24 y=4 w=16 h=16\n"
        "# saved queries/group.faces.jpg\n"
    )
    assert index.calls == []
    saved = tmp_path / "queries" / "group.faces.jpg"
    with Image.open(saved) as picture:
        assert picture.size == (200, 150)
    first = saved.read_bytes()

    assert run(["queries/group.jpg", "--list-faces"], index, faces=1) == 0
    assert sorted(p.name for p in (tmp_path / "queries").iterdir()) == [
        "group.faces.jpg",
        "group.jpg",
    ]
    assert saved.read_bytes() != first  # overwritten (one box instead of two)


@pytest.mark.parametrize("extra", [["--face", "1"], ["--details"], ["--threshold", "0.3"]])
def test_list_faces_conflicts_with_search_options(tmp_path: Path, extra: list[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        run([str(photo_file(tmp_path)), "--list-faces", *extra])
    assert exited.value.code == face_matcher.EXIT_USAGE


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_unwritable_folder_prints_the_list_then_exits_5(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], request: pytest.FixtureRequest
) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    path = photo_file(folder, "group.jpg")
    folder.chmod(0o555)
    request.addfinalizer(lambda: folder.chmod(0o755))  # so pytest can remove tmp_path
    assert run([str(path), "--list-faces"], faces=1) == face_matcher.EXIT_CANNOT_WRITE
    captured = capsys.readouterr()
    assert "1 x=4 y=4 w=16 h=16" in captured.out
    assert "cannot write" in captured.err
