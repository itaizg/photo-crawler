"""Narrowing of DeepFace-shaped results; no model is loaded."""

import pytest

from face_index.errors import ItemError
from face_index.faces import narrow_results

VEC = [0.1] * 512


def entry(x: int, y: int, w: int = 50, h: int = 50, conf: float = 0.99) -> dict[str, object]:
    return {
        "embedding": VEC,
        "facial_area": {"x": x, "y": y, "w": w, "h": h},
        "face_confidence": conf,
    }


def test_whole_image_fallback_is_dropped() -> None:
    # What DeepFace returns with enforce_detection=False when there is no face.
    fallback = {
        "embedding": VEC,
        "facial_area": {"x": 0, "y": 0, "w": 400, "h": 300},
        "face_confidence": 0,
    }
    assert narrow_results([fallback], 400, 300) == []


def test_low_confidence_is_dropped() -> None:
    assert narrow_results([entry(10, 10, conf=0.89)], 400, 300) == []


def test_faces_sorted_left_to_right_then_top_to_bottom() -> None:
    faces = narrow_results([entry(200, 10), entry(10, 100), entry(10, 5)], 400, 300)
    assert [(f.box.x, f.box.y) for f in faces] == [(10, 5), (10, 100), (200, 10)]


def test_boxes_are_clamped_to_image() -> None:
    (face,) = narrow_results([entry(380, -5, 50, 50)], 400, 300)
    assert (face.box.x, face.box.y, face.box.w, face.box.h) == (380, 0, 20, 50)


def test_wrong_dimension_is_rejected() -> None:
    bad = entry(10, 10)
    bad["embedding"] = [0.1] * 511
    with pytest.raises(ItemError, match="511"):
        narrow_results([bad], 400, 300)


def test_embedding_is_a_float_tuple() -> None:
    (face,) = narrow_results([entry(1, 1)], 400, 300)
    assert len(face.embedding) == 512
    assert isinstance(face.embedding[0], float)
