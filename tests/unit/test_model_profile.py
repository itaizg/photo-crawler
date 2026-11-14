from face_index import model_profile


def test_key_names_the_installed_versions() -> None:
    assert model_profile.current_profile().key == model_profile.KEY
    assert model_profile.KEY.startswith("retinaface+Facenet512+cosine+align+base@deepface")
