import hashlib
from pathlib import Path

import pytest

from face_index.export_image import main
from face_index.models import Box, DetectedFace, NormalizedImage, Skipped
from face_index.store import PostgresIndexStore
from tests import images


@pytest.fixture
def seeded(store: PostgresIndexStore) -> dict[str, int]:
    pid = store.upsert_page("https://people.com/a", "people", 0)
    jpeg = images.photo(64, 64, seed=5)
    indexed, _ = store.stage_image(
        NormalizedImage(b"\x05" * 32, jpeg, 64, 64, "JPEG", 64, 64), "https://img/i.jpg", pid, False
    )
    store.process_pending(
        indexed, lambda p: [DetectedFace(Box(2, 2, 20, 20), 0.99, (0.1,) * 512)], 1
    )
    pending, _ = store.stage_image(
        NormalizedImage(b"\x06" * 32, jpeg, 64, 64, "JPEG", 64, 64), "https://img/p.jpg", pid, False
    )
    skipped, _ = store.stage_image(
        Skipped(b"\x07" * 32, "too_small", "JPEG", 9, 9), "https://img/s.jpg", pid, False
    )
    return {"indexed": indexed, "pending": pending, "skipped": skipped}


def test_export_by_id_is_byte_identical(
    seeded: dict[str, int], db_dsn: str, tmp_path: Path
) -> None:
    out = tmp_path / "x.jpg"
    assert main(["--id", str(seeded["indexed"]), "-o", str(out)], dsn=db_dsn) == 0
    assert (
        hashlib.sha256(out.read_bytes()).digest()
        == hashlib.sha256(images.photo(64, 64, seed=5)).digest()
    )


def test_export_by_sha256(seeded: dict[str, int], db_dsn: str, tmp_path: Path) -> None:
    out = tmp_path / "y.jpg"
    assert main(["--sha256", "05" * 32, "-o", str(out)], dsn=db_dsn) == 0
    assert out.read_bytes() == images.photo(64, 64, seed=5)


def test_boxes_writes_a_modified_valid_jpeg(
    seeded: dict[str, int], db_dsn: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "b.jpg"
    assert main(["--id", str(seeded["indexed"]), "--boxes", "-o", str(out)], dsn=db_dsn) == 0
    data = out.read_bytes()
    assert data.startswith(b"\xff\xd8\xff")
    assert data != images.photo(64, 64, seed=5)
    assert "not byte-identical" in capsys.readouterr().err


@pytest.mark.parametrize(("key", "code"), [("pending", 3), ("skipped", 3)])
def test_no_exportable_bytes(
    seeded: dict[str, int], db_dsn: str, tmp_path: Path, key: str, code: int
) -> None:
    assert main(["--id", str(seeded[key]), "-o", str(tmp_path / "z.jpg")], dsn=db_dsn) == code


def test_unknown_image(seeded: dict[str, int], db_dsn: str, tmp_path: Path) -> None:
    assert main(["--id", "999999", "-o", str(tmp_path / "z.jpg")], dsn=db_dsn) == 2


def test_bad_arguments() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--sha256", "nothex", "-o", "x"], dsn="unused")
    assert exc.value.code == 64


def test_database_down(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dsn = "postgresql://nobody@127.0.0.1:1/none?connect_timeout=1"
    assert main(["--id", "1", "-o", str(tmp_path / "z.jpg")], dsn=dsn) == 1
    assert "nobody" not in capsys.readouterr().err
