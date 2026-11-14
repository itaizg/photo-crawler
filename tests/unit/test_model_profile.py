import pytest

from face_index import model_profile


def test_key_names_the_installed_versions() -> None:
    assert model_profile.current_profile().key == model_profile.KEY
    assert model_profile.KEY.startswith("retinaface+Facenet512+cosine+align+base@deepface")


@pytest.mark.model
def test_match_threshold_matches_deepface() -> None:
    from deepface.modules.verification import find_threshold

    recommended = find_threshold(model_profile.MODEL, model_profile.DISTANCE)
    assert recommended == model_profile.MATCH_THRESHOLD
