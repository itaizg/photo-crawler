"""The one face model setup shared by the pipeline and the search script (FR-013, research R9)."""

from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

DETECTOR = "retinaface"
MODEL = "Facenet512"
DIMENSIONS = 512
DISTANCE = "cosine"
ALIGN = True
NORMALIZATION = "base"
MIN_FACE_CONFIDENCE = 0.9


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    key: str
    deepface_version: str
    tensorflow_version: str


def _installed(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "0.0.0"


def _major_minor(full: str) -> str:
    parts = full.split(".")
    # DeepFace uses 0.0.x numbering, so for it the full version is the "major.minor".
    if parts[:2] == ["0", "0"]:
        return ".".join(parts[:3])
    return ".".join(parts[:2])


def profile_key(deepface_version: str, tensorflow_version: str) -> str:
    align = "align" if ALIGN else "noalign"
    return (
        f"{DETECTOR}+{MODEL}+{DISTANCE}+{align}+{NORMALIZATION}"
        f"@deepface{_major_minor(deepface_version)}/tf{_major_minor(tensorflow_version)}"
    )


def current_profile() -> ProfileRecord:
    deepface_version = _installed("deepface")
    tensorflow_version = _installed("tensorflow")
    return ProfileRecord(
        key=profile_key(deepface_version, tensorflow_version),
        deepface_version=deepface_version,
        tensorflow_version=tensorflow_version,
    )


# The key for the installed versions; the search script selects its profile row by it.
KEY = current_profile().key
