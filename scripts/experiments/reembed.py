"""Re-embed the stored images with another model, offline (docs/briefs/search-experiments.md).

    PGPASSWORD=... FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces \\
      uv run python scripts/experiments/reembed.py --set YYYY-MM-DD --model ArcFace

Reads each indexed image's stored JPEG (read-only), detects and embeds its faces with the index's
detector, alignment and confidence filter, and writes eval-snapshots/experiments/<date>/<model>.npz.
The image → page links are copied from the Facenet512 export, so run export_index.py first. Work
is saved every 100 images; an interrupted run resumes where it stopped. The database and the
live index are never changed.
"""

import argparse
import json
import multiprocessing
import os
import sys
from collections.abc import Sequence
from concurrent.futures import Executor, ProcessPoolExecutor
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Final, LiteralString

import numpy as np
import psycopg
import snapshot

from face_index.errors import InfrastructureError, ItemError
from face_index.models import DetectedFace

_JPEGS: Final[LiteralString] = (
    "SELECT id, jpeg FROM image WHERE status = 'indexed' AND id = ANY(%s) ORDER BY id"
)
_BATCH: Final[int] = 100
_DEFAULT_WORKERS: Final[int] = min(4, os.cpu_count() or 1)

# One embedder per process: the worker's own in a pool, the main process's when running alone.
_EMBEDDER: snapshot.ModelEmbedder | None = None


def _init_worker(model: str) -> None:
    """Pool initializer. One thread per worker, so N workers don't each use every core."""
    for name in ("OMP_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS"):
        os.environ[name] = "1"  # set before TensorFlow is imported
    global _EMBEDDER
    _EMBEDDER = snapshot.ModelEmbedder(model)


def _embed(item: tuple[int, bytes]) -> tuple[int, list[DetectedFace] | None]:
    """The faces of one image, or ``None`` when this image fails (counted, not fatal)."""
    image_id, jpeg = item
    assert _EMBEDDER is not None
    try:
        return image_id, _EMBEDDER.embed(jpeg)
    except ItemError:
        return image_id, None


def embed_batch(
    items: Sequence[tuple[int, bytes]], pool: Executor | None
) -> tuple[list[tuple[int, DetectedFace]], int]:
    """Faces of a batch of images in image order, and the number of images that failed.

    ``pool.map`` keeps the input order, so the result does not depend on the worker count.
    """
    results = pool.map(_embed, items) if pool is not None else map(_embed, items)
    rows: list[tuple[int, DetectedFace]] = []
    failed = 0
    for image_id, faces in results:
        if faces is None:
            failed += 1
        else:
            rows.extend((image_id, f) for f in faces)
    return rows, failed


def _parts(target: Path) -> Path:
    return target.with_suffix(".parts")


def _done(parts: Path) -> set[int]:
    done: set[int] = set()
    for part in sorted(parts.glob("*.json")):
        done.update(json.loads(part.read_text(encoding="utf-8"))["images"])
    return done


def _save_part(
    parts: Path, number: int, images: list[int], rows: list[tuple[int, DetectedFace]]
) -> None:
    image_ids, boxes, vectors = snapshot.faces_to_arrays(rows)
    np.savez_compressed(
        parts / f"{number:05d}.npz", face_image=image_ids, boxes=boxes, embeddings=vectors
    )
    # Written last: a part counts as done only once its faces are on disk.
    (parts / f"{number:05d}.json").write_text(json.dumps({"images": images}), encoding="utf-8")


def _assemble(parts: Path, pages: snapshot.Snapshot, target: Path) -> snapshot.Snapshot:
    chunks = [np.load(p) for p in sorted(parts.glob("*.npz")) if p.with_suffix(".json").exists()]
    snap = snapshot.Snapshot(
        face_image=np.concatenate([c["face_image"] for c in chunks]),
        boxes=np.concatenate([c["boxes"] for c in chunks]),
        embeddings=np.concatenate([c["embeddings"] for c in chunks]),
        image_pages=pages.image_pages,
    )
    snap.save(target)
    return snap


def reembed(crawl_date: date, model: str, workers: int) -> int:
    base = snapshot.Snapshot.load(snapshot.path(crawl_date, "Facenet512"))
    images = sorted(set(base.face_image.tolist()) | set(base.image_pages))
    target = snapshot.path(crawl_date, model)
    parts = _parts(target)
    parts.mkdir(parents=True, exist_ok=True)
    done = _done(parts)
    todo = [i for i in images if i not in done]
    print(
        f"{len(images)} images, {len(done)} already done, {len(todo)} to embed, "
        f"{workers} worker(s)",
        file=sys.stderr,
    )
    failed = 0
    number = len(list(parts.glob("*.json")))
    pool: ProcessPoolExecutor | None = None
    if workers > 1:  # spawn: TensorFlow is not fork-safe
        pool = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_worker,
            initargs=(model,),
        )
    else:
        _init_worker(model)
    try:
        with closing(snapshot.connect()) as conn:
            for start in range(0, len(todo), _BATCH):
                batch = todo[start : start + _BATCH]
                items = [
                    (int(image_id), bytes(jpeg))
                    for image_id, jpeg in conn.execute(_JPEGS, (batch,)).fetchall()
                ]
                rows, bad = embed_batch(items, pool)
                failed += bad
                _save_part(parts, number, batch, rows)
                number += 1
                print(f"  {min(start + _BATCH, len(todo))}/{len(todo)}", file=sys.stderr)
    finally:
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
    snap = _assemble(parts, base, target)
    print(f"wrote {target}: {len(snap.face_image)} faces ({failed} images failed)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
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
        print("error: run export_index.py first", file=sys.stderr)
        return 2
    try:
        return reembed(args.crawl_date, args.model, args.workers)
    except (InfrastructureError, RuntimeError, psycopg.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
