"""Local index snapshots for the search experiments (docs/briefs/search-experiments.md).

A snapshot is one ``.npz`` file per embedding model under ``eval-snapshots/experiments/<date>/``
(gitignored): every face of an ``indexed`` image with its image id, box and embedding, plus the
image → page links the search would show. It holds embeddings of people who gave no licence,
so it never leaves the labeller's machine.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import psycopg
from numpy.typing import NDArray

from face_index import config, db, model_profile
from face_index.errors import InfrastructureError, ItemError
from face_index.faces import narrow_results
from face_index.models import DetectedFace

ROOT: Final[Path] = Path("eval-snapshots/experiments")
MODELS: Final[Mapping[str, str]] = {"Facenet512": "facenet512", "ArcFace": "arcface"}


def folder(crawl_date: date) -> Path:
    return ROOT / crawl_date.isoformat()


def path(crawl_date: date, model: str) -> Path:
    return folder(crawl_date) / f"{MODELS[model]}.npz"


@dataclass(frozen=True)
class Snapshot:
    face_image: NDArray[np.int64]
    boxes: NDArray[np.int64]  # x, y, w, h
    embeddings: NDArray[np.float32]
    image_pages: Mapping[int, tuple[str, ...]]

    def save(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        links = [(i, u) for i, urls in self.image_pages.items() for u in urls]
        np.savez_compressed(
            target,
            face_image=self.face_image,
            boxes=self.boxes,
            embeddings=self.embeddings,
            link_image=np.asarray([i for i, _ in links], dtype=np.int64),
            link_url=np.asarray([u for _, u in links], dtype=np.str_),
        )

    @classmethod
    def load(cls, source: Path) -> "Snapshot":
        with np.load(source, allow_pickle=False) as data:
            pages: dict[int, list[str]] = {}
            for image, url in zip(data["link_image"], data["link_url"], strict=True):
                pages.setdefault(int(image), []).append(str(url))
            return cls(
                face_image=data["face_image"],
                boxes=data["boxes"],
                embeddings=data["embeddings"],
                image_pages={i: tuple(u) for i, u in pages.items()},
            )


def connect() -> psycopg.Connection:
    """A read-only connection with the reader DSN, as the evaluator uses."""
    try:
        conn = db.connect(config.database_dsn())
        conn.execute("SET default_transaction_read_only = on")
    except psycopg.Error as exc:
        raise InfrastructureError(f"database unavailable: {type(exc).__name__}") from exc
    return conn


class ModelEmbedder:
    """``FaceEmbedder`` for any DeepFace model, with the index's detector, alignment and filter."""

    def __init__(self, model: str) -> None:
        self.model = model
        self._ready = False

    def embed(self, jpeg: bytes) -> list[DetectedFace]:
        import cv2
        from deepface import DeepFace

        if not self._ready:
            try:
                DeepFace.build_model(self.model)
            except Exception as exc:  # any failure to load the model is infrastructure
                raise InfrastructureError(f"face model unavailable: {exc}") from exc
            self._ready = True
        bgr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ItemError("JPEG could not be decoded")
        try:
            raw = DeepFace.represent(
                img_path=bgr,
                model_name=self.model,
                detector_backend=model_profile.DETECTOR,
                enforce_detection=False,
                align=model_profile.ALIGN,
                normalization=model_profile.NORMALIZATION,
            )
        except ValueError as exc:
            raise ItemError(f"detection failed: {exc}") from exc
        height, width = bgr.shape[:2]
        return narrow_results([e for e in raw if isinstance(e, dict)], width, height)


def faces_to_arrays(
    rows: Sequence[tuple[int, DetectedFace]],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float32]]:
    images = np.asarray([i for i, _ in rows], dtype=np.int64)
    boxes = np.asarray([(f.box.x, f.box.y, f.box.w, f.box.h) for _, f in rows], dtype=np.int64)
    vectors = np.asarray([f.embedding for _, f in rows], dtype=np.float32)
    return images, boxes.reshape(-1, 4), vectors.reshape(-1, model_profile.DIMENSIONS)
