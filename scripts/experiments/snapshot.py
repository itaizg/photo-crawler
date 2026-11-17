"""Local index snapshots for the search experiments (docs/briefs/search-experiments.md).

A snapshot is one ``.npz`` file per embedding model under ``eval-snapshots/experiments/<date>/``
(gitignored): every face of an ``indexed`` image with its image id, box and embedding, plus the
image → page links the search would show. It holds embeddings of people who gave no licence,
so it never leaves the labeller's machine.
"""

import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import psycopg
from numpy.typing import NDArray

from face_index import config, db, model_profile
from face_index.errors import InfrastructureError
from face_index.models import DetectedFace

ROOT: Final[Path] = Path("eval-snapshots/experiments")
MODELS: Final[Mapping[str, str]] = {"Facenet512": "facenet512", "ArcFace": "arcface"}
BASELINE: Final[str] = "Facenet512"  # the index's model before ArcFace: the grid's baseline


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
    # Images that could not be embedded (re-embedded snapshots only): their faces are missing.
    failed_images: tuple[int, ...] = ()

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
            failed_image=np.asarray(self.failed_images, dtype=np.int64),
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
                failed_images=(
                    tuple(int(i) for i in data["failed_image"])
                    if "failed_image" in data.files
                    else ()
                ),
            )


def setup_logging() -> None:
    """Progress and errors go to standard error through ``logging``; results to standard output."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)


def connect() -> psycopg.Connection:
    """A read-only connection with the reader DSN, as the evaluator uses."""
    try:
        conn = db.connect(config.database_dsn())
        conn.execute("SET default_transaction_read_only = on")
    except psycopg.Error as exc:
        raise InfrastructureError(f"database unavailable: {type(exc).__name__}") from exc
    return conn


def faces_to_arrays(
    rows: Sequence[tuple[int, DetectedFace]],
) -> tuple[NDArray[np.int64], NDArray[np.int64], NDArray[np.float32]]:
    images = np.asarray([i for i, _ in rows], dtype=np.int64)
    boxes = np.asarray([(f.box.x, f.box.y, f.box.w, f.box.h) for _, f in rows], dtype=np.int64)
    vectors = np.asarray([f.embedding for _, f in rows], dtype=np.float32)
    return images, boxes.reshape(-1, 4), vectors.reshape(-1, model_profile.DIMENSIONS)
