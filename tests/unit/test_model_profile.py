from importlib.metadata import version

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
