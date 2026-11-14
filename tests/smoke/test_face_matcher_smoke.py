"""Real-model search (marker ``model``): index a fixture face, then find it from another copy.

Needs ``tests/fixtures/images/face_one.jpg`` (see that folder's README) and loopback Postgres.
The query is a resized, re-encoded copy, so the match proves the embedding, not byte identity.
"""

import io
from pathlib import Path

import pytest
from PIL import Image

from face_index import face_matcher
from face_index.faces import DeepFaceEmbedder
from face_index.models import NormalizedImage
from face_index.normalize import PillowNormalizer
from face_index.search_store import PostgresSearchIndex
from face_index.store import PostgresIndexStore

pytestmark = pytest.mark.model

FACE_ONE = Path(__file__).resolve().parents[1] / "fixtures" / "images" / "face_one.jpg"


def test_a_resized_copy_finds_the_indexed_page(
    store: PostgresIndexStore, db_dsn: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not FACE_ONE.exists():
        pytest.skip("face_one.jpg not provided (see tests/fixtures/images/README.md)")
    pytest.importorskip("deepface")
    embedder = DeepFaceEmbedder()
    normalized = PillowNormalizer().normalize(FACE_ONE.read_bytes(), "image/jpeg")
    assert isinstance(normalized, NormalizedImage), "fixture photo was skipped by the normalizer"

    profile_id = store.init_schema()
    page = store.upsert_page("https://cnn.test/person", "cnn", 1)
    image_id, _ = store.stage_image(normalized, "https://img.test/person.jpg", page, True)
    store.process_pending(image_id, lambda p: embedder.embed(p.jpeg), profile_id)

    original = Image.open(FACE_ONE).convert("RGB")
    smaller = original.resize((original.width * 3 // 4, original.height * 3 // 4))
    buffer = io.BytesIO()
    smaller.save(buffer, format="JPEG", quality=80)
    query = tmp_path / "query.jpg"
    query.write_bytes(buffer.getvalue())

    code = face_matcher.main(
        [str(query)], index_factory=lambda: PostgresSearchIndex(db_dsn), embedder=embedder
    )
    assert code == face_matcher.EXIT_OK
    assert "https://cnn.test/person" in capsys.readouterr().out.splitlines()
