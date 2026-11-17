"""Re-embed the stored images with another model, offline (docs/briefs/search-experiments.md).

    PGPASSWORD=... FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces \\
      uv run python scripts/experiments/reembed.py --set YYYY-MM-DD --model ArcFace

Reads each indexed image's stored JPEG (read-only), detects and embeds its faces with the index's
detector, alignment and confidence filter, and writes eval-snapshots/experiments/<date>/<model>.npz.
The image → page links are copied from the Facenet512 export, so run export_index.py first. Work
is saved every 100 images; an interrupted run resumes where it stopped. Images that fail are
recorded in their part and in the snapshot, so a resumed run still reports them and run.py warns
about them. One run at a time per date and model. The database and the live index are never
changed.

Exit 0 when every image was embedded, 1 when some failed (the snapshot is written and records
them), 2 when an input is missing, the database or model is unavailable, or another run holds
the lock.
"""

import argparse
import fcntl
import functools
import json
import logging
import multiprocessing
import os
from collections.abc import Sequence
from concurrent.futures import Executor, ProcessPoolExecutor
from contextlib import ExitStack, closing
from datetime import date
from pathlib import Path
from typing import Final, LiteralString

import numpy as np
import psycopg
import snapshot

from face_index.errors import InfrastructureError, ItemError
from face_index.faces import DeepFaceEmbedder
from face_index.models import DetectedFace

log = logging.getLogger("reembed")

_JPEGS: Final[LiteralString] = (
    "SELECT id, jpeg FROM image WHERE status = 'indexed' AND id = ANY(%s) ORDER BY id"
)
_BATCH: Final[int] = 100
_DEFAULT_WORKERS: Final[int] = min(4, os.cpu_count() or 1)


@functools.cache
def _embedder(model: str) -> DeepFaceEmbedder:
    """One embedder per process and model: the worker's own in a pool, the main process's alone."""
    return DeepFaceEmbedder(model)


def _init_worker() -> None:
    """Pool initializer. One thread per worker, so N workers don't each use every core."""
    for name in ("OMP_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS"):
        os.environ[name] = "1"  # set before TensorFlow is imported


def _embed(item: tuple[str, int, bytes]) -> tuple[int, list[DetectedFace] | None]:
    """The faces of one image, or ``None`` when this image fails (recorded, not fatal)."""
    model, image_id, jpeg = item
    try:
        return image_id, _embedder(model).embed(jpeg)
    except ItemError as exc:
        log.warning("image %d failed: %s", image_id, exc)
        return image_id, None


def embed_batch(
    items: Sequence[tuple[int, bytes]], model: str, pool: Executor | None
) -> tuple[list[tuple[int, DetectedFace]], list[int]]:
    """Faces of a batch of images in image order, and the ids of the images that failed.

    ``pool.map`` keeps the input order, so the result does not depend on the worker count.
    """
    work = [(model, image_id, jpeg) for image_id, jpeg in items]
    results = pool.map(_embed, work) if pool is not None else map(_embed, work)
    rows: list[tuple[int, DetectedFace]] = []
    failed: list[int] = []
    for image_id, faces in results:
        if faces is None:
            failed.append(image_id)
        else:
            rows.extend((image_id, f) for f in faces)
    return rows, failed


def _parts(target: Path) -> Path:
    return target.with_suffix(".parts")


def _read_sidecar(sidecar: Path) -> tuple[list[int], list[int]] | None:
    """A finished part's (images, failed images), or ``None`` when the sidecar is unreadable."""
    try:  # a file boundary: no other construct tells a truncated file from a valid one
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, list):
        return None
    return images, data.get("failed", [])  # parts written before failures were recorded


def done_parts(parts: Path) -> tuple[set[int], set[int]]:
    """Images in finished parts, and those of them that failed.

    An unreadable sidecar (a run stopped while writing it) is removed, so its part counts as not
    done: its images are embedded again and its faces file is left out of the snapshot.
    """
    done: set[int] = set()
    failed: set[int] = set()
    for sidecar in sorted(parts.glob("*.json")):
        part = _read_sidecar(sidecar)
        if part is None:
            log.warning("ignoring the unfinished part %s; its images are embedded again", sidecar)
            sidecar.unlink()
            continue
        done.update(part[0])
        failed.update(part[1])
    return done, failed


def _next_number(parts: Path) -> int:
    numbers = [int(p.stem) for p in parts.glob("*.npz") if p.stem.isdigit()]
    return max(numbers, default=-1) + 1


def save_part(
    parts: Path,
    number: int,
    images: list[int],
    failed: list[int],
    rows: list[tuple[int, DetectedFace]],
) -> None:
    image_ids, boxes, vectors = snapshot.faces_to_arrays(rows)
    np.savez_compressed(
        parts / f"{number:05d}.npz", face_image=image_ids, boxes=boxes, embeddings=vectors
    )
    # Written last, and atomically: a part counts as done only once its faces are on disk.
    sidecar = parts / f"{number:05d}.json"
    partial = sidecar.with_suffix(".json.partial")
    partial.write_text(json.dumps({"images": images, "failed": failed}), encoding="utf-8")
    partial.replace(sidecar)


def _assemble(
    parts: Path, pages: snapshot.Snapshot, failed: set[int], target: Path
) -> snapshot.Snapshot:
    chunks = [np.load(p) for p in sorted(parts.glob("*.npz")) if p.with_suffix(".json").exists()]
    snap = snapshot.Snapshot(
        face_image=np.concatenate([c["face_image"] for c in chunks]),
        boxes=np.concatenate([c["boxes"] for c in chunks]),
        embeddings=np.concatenate([c["embeddings"] for c in chunks]),
        image_pages=pages.image_pages,
        failed_images=tuple(sorted(failed)),
    )
    snap.save(target)
    return snap


def _lock(parts: Path, stack: ExitStack) -> bool:
    """Hold an exclusive lock on this date and model until ``stack`` closes; False if taken."""
    handle = stack.enter_context((parts / ".lock").open("w"))
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def reembed(crawl_date: date, model: str, workers: int) -> int:
    base = snapshot.Snapshot.load(snapshot.path(crawl_date, "Facenet512"))
    images = sorted(set(base.face_image.tolist()) | set(base.image_pages))
    target = snapshot.path(crawl_date, model)
    parts = _parts(target)
    parts.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        if not _lock(parts, stack):
            log.error("another reembed run is writing %s; wait for it to finish", parts)
            return 2
        done, failed = done_parts(parts)
        todo = [i for i in images if i not in done]
        log.info(
            "%d images, %d already done (%d of them failed), %d to embed, %d worker(s)",
            len(images),
            len(done),
            len(failed),
            len(todo),
            workers,
        )
        pool: ProcessPoolExecutor | None = None
        if workers > 1:  # spawn: TensorFlow is not fork-safe
            pool = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker,
            )
            stack.callback(pool.shutdown, wait=True, cancel_futures=True)
        else:
            _init_worker()
        conn = stack.enter_context(closing(snapshot.connect()))
        number = _next_number(parts)
        for start in range(0, len(todo), _BATCH):
            batch = todo[start : start + _BATCH]
            items = [
                (int(image_id), bytes(jpeg))
                for image_id, jpeg in conn.execute(_JPEGS, (batch,)).fetchall()
            ]
            rows, bad = embed_batch(items, model, pool)
            failed.update(bad)
            save_part(parts, number, batch, bad, rows)
            number += 1
            log.info("%d/%d", min(start + _BATCH, len(todo)), len(todo))
        snap = _assemble(parts, base, failed, target)
    print(f"wrote {target}: {len(snap.face_image)} faces ({len(failed)} images failed)")
    if failed:
        log.warning("incomplete snapshot: %d images have no faces for %s", len(failed), model)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    snapshot.setup_logging()
    parser = argparse.ArgumentParser(prog="reembed", description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="crawl_date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--model", choices=[m for m in snapshot.MODELS if m != "Facenet512"], required=True
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=_DEFAULT_WORKERS,
        help="parallel processes, each holding the models (about 2 GB each); 1 = in-process",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if not snapshot.path(args.crawl_date, "Facenet512").is_file():
        log.error("run export_index.py first")
        return 2
    try:
        return reembed(args.crawl_date, args.model, args.workers)
    except (InfrastructureError, RuntimeError, psycopg.Error) as exc:
        log.error("%s", exc)  # noqa: TRY400 - an expected outage: its message, no traceback
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
