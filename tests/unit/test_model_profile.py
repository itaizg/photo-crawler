from importlib.metadata import version

import pytest

from face_index import model_profile


def test_key_is_exact_for_the_pinned_versions() -> None:
    assert (
        model_profile.profile_key("0.0.95", "2.20.3")
        == "retinaface+Facenet512+cosine+align+base@deepface0.0.95/tf2.20"
    )


def test_key_names_the_installed_versions() -> None:
    profile = model_profile.current_profile()
    assert profile.key == model_profile.KEY
    assert profile.key == model_profile.profile_key(version("deepface"), version("tensorflow"))


@pytest.mark.model
def test_match_threshold_matches_deepface() -> None:
    from deepface.modules.verification import find_threshold

    recommended = find_threshold(model_profile.MODEL, model_profile.DISTANCE)
    assert recommended == model_profile.MATCH_THRESHOLD
