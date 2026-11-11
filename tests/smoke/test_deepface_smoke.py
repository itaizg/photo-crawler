"""Real-model smoke test (marker `model`, research R13): the actual DeepFace setup.

Needs the RetinaFace and Facenet512 weights (downloaded on first use, or set DEEPFACE_HOME to a
cache). The face cases use photos placed in tests/fixtures/images/ (see its README); they are
skipped when those files are absent. The stack smoke test (scripts/smoke.sh) covers real faces on
live pages in any case.
"""

from pathlib import Path

import pytest

from face_index import model_profile
from face_index.faces import DeepFaceEmbedder
from tests import images

pytestmark = pytest.mark.model

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "images"


@pytest.fixture(scope="module")
def embedder() -> DeepFaceEmbedder:
    pytest.importorskip("deepface")
    return DeepFaceEmbedder()


def test_no_face_image_yields_no_faces(embedder: DeepFaceEmbedder) -> None:
    # DeepFace returns a whole-image fallback here; narrowing must drop it (FR-014).
    assert embedder.embed(images.photo(320, 240, seed=9)) == []


@pytest.mark.parametrize(("name", "expected"), [("face_one.jpg", 1), ("face_two.jpg", 2)])
def test_real_faces(embedder: DeepFaceEmbedder, name: str, expected: int) -> None:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"{name} not provided (see tests/fixtures/images/README.md)")
    faces = embedder.embed(path.read_bytes())
    assert len(faces) == expected
    for face in faces:
        assert len(face.embedding) == model_profile.DIMENSIONS
        assert face.confidence >= model_profile.MIN_FACE_CONFIDENCE
