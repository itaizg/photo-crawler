"""Face detection and embedding with DeepFace (FR-011 to FR-014, research R9).

``narrow_results`` turns DeepFace's untyped output into ``DetectedFace`` values. It drops the
whole-image fallback DeepFace returns when no face is found (``face_confidence == 0``) and
anything below the detector's standard confidence.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from face_index import model_profile
from face_index.errors import InfrastructureError, ItemError
from face_index.models import Box, DetectedFace


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ItemError(f"unexpected box value {value!r}")
    return round(value)


def narrow_results(raw: Sequence[Mapping[str, Any]], width: int, height: int) -> list[DetectedFace]:
    faces: list[DetectedFace] = []
    for entry in raw:
        confidence = float(entry.get("face_confidence") or 0.0)
        if confidence < model_profile.MIN_FACE_CONFIDENCE:
            continue
        area = entry.get("facial_area")
        embedding = entry.get("embedding")
        if not isinstance(area, Mapping) or not isinstance(embedding, Sequence):
            raise ItemError("malformed DeepFace result")
        if len(embedding) != model_profile.DIMENSIONS:
            raise ItemError(f"embedding has {len(embedding)} dimensions, expected 512")
        x, y = max(0, _as_int(area.get("x"))), max(0, _as_int(area.get("y")))
        w = min(_as_int(area.get("w")), width - x)
        h = min(_as_int(area.get("h")), height - y)
        if w <= 0 or h <= 0:
            continue
        faces.append(
            DetectedFace(
                box=Box(x, y, w, h),
                confidence=confidence,
                embedding=tuple(float(v) for v in embedding),
            )
        )
    # Left to right, then top to bottom (matches the search script's face numbering).
    return sorted(faces, key=lambda f: (f.box.x, f.box.y))


class DeepFaceEmbedder:
    """``FaceEmbedder`` adapter. DeepFace and TensorFlow are imported on first use."""

    def __init__(self) -> None:
        self._ready = False

    def _load(self) -> None:
        if self._ready:
            return
        try:
            from deepface import DeepFace

            DeepFace.build_model(model_profile.MODEL)
        except Exception as exc:  # any failure to load the model is infrastructure
            raise InfrastructureError(f"face model unavailable: {type(exc).__name__}") from exc
        self._ready = True

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        self._load()
        import cv2
        import numpy as np
        from deepface import DeepFace

        array = np.frombuffer(jpeg, dtype=np.uint8)
        bgr = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ItemError("stored JPEG could not be decoded")
        try:
            raw = DeepFace.represent(
                img_path=bgr,
                model_name=model_profile.MODEL,
                detector_backend=model_profile.DETECTOR,
                enforce_detection=False,
                align=model_profile.ALIGN,
                normalization=model_profile.NORMALIZATION,
            )
        except ValueError as exc:
            raise ItemError(f"detection failed: {exc}") from exc
        height, width = bgr.shape[:2]
        entries = [entry for entry in raw if isinstance(entry, dict)]  # one image → flat list
        return narrow_results(entries, width, height)
