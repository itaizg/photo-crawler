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
import sys
from collections.abc import Sequence
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


def reembed(crawl_date: date, model: str) -> int:
    base = snapshot.Snapshot.load(snapshot.path(crawl_date, "Facenet512"))
    images = sorted(set(base.face_image.tolist()) | set(base.image_pages))
    target = snapshot.path(crawl_date, model)
    parts = _parts(target)
    parts.mkdir(parents=True, exist_ok=True)
    done = _done(parts)
    todo = [i for i in images if i not in done]
    print(f"{len(images)} images, {len(done)} already done, {len(todo)} to embed", file=sys.stderr)
    embedder = snapshot.ModelEmbedder(model)
    failed = 0
    number = len(list(parts.glob("*.json")))
    with closing(snapshot.connect()) as conn:
        for start in range(0, len(todo), _BATCH):
            batch = todo[start : start + _BATCH]
            rows: list[tuple[int, DetectedFace]] = []
            for image_id, jpeg in conn.execute(_JPEGS, (batch,)).fetchall():
                try:  # one image's failure is counted, the others still run
                    faces = embedder.embed(bytes(jpeg))
                except ItemError:
                    failed += 1
                    continue
                rows.extend((int(image_id), f) for f in faces)
            _save_part(parts, number, batch, rows)
            number += 1
            print(f"  {min(start + _BATCH, len(todo))}/{len(todo)}", file=sys.stderr)
    snap = _assemble(parts, base, target)
    print(f"wrote {target}: {len(snap.face_image)} faces ({failed} images failed)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reembed", description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="crawl_date", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--model", choices=[m for m in snapshot.MODELS if m != "Facenet512"], required=True
    )
    args = parser.parse_args(argv)
    if not snapshot.path(args.crawl_date, "Facenet512").is_file():
        print("error: run export_index.py first", file=sys.stderr)
        return 2
    try:
        return reembed(args.crawl_date, args.model)
    except (InfrastructureError, RuntimeError, psycopg.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
