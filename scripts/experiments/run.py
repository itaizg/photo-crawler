"""Score the pre-registered experiment grid on a label set (docs/briefs/search-experiments.md).

    uv run python scripts/experiments/run.py --set YYYY-MM-DD [--only baseline]

Needs the snapshots from export_index.py (and reembed.py for ArcFace) and the face models; no
database. Every run first checks that the baseline (Facenet512, raw cosine, no re-ranking)
reproduces the latest logged unfiltered run in queries/eval/distances.csv; if it does not,
nothing else is scored. Writes eval-snapshots/experiments/<date>/results.csv (gitignored; it
holds per-label numbers only) and prints the summary table.

Exit 0 when the check passes, 1 when it fails, 2 when an input is missing.
"""

import argparse
import csv
import logging
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import scoring
import snapshot

from face_index import eval_set
from face_index.faces import DeepFaceEmbedder
from face_index.normalize import PillowNormalizer
from face_index.ports import FaceEmbedder
from face_index.search import prepare_query

EVAL: Final[Path] = Path("queries/eval")
CEILING: Final[float] = eval_set.REPORT_CEILING
TOLERANCE: Final[float] = 5e-5  # distances.csv keeps 4 decimals

log = logging.getLogger("run")


def query_vectors(
    label_set: eval_set.LabelSet, embedder: FaceEmbedder, eval_dir: Path = EVAL
) -> scoring.Matrix:
    """Each label's largest face, in label order, as the evaluator searches it."""
    normalizer = PillowNormalizer()
    vectors = []
    for label in label_set.labels():
        data = (eval_dir / eval_set.photo_path(label)).read_bytes()
        _, faces = prepare_query(data, normalizer, embedder)
        vectors.append(max(faces, key=lambda f: f.box.w * f.box.h).embedding)
    return scoring.normalize(np.asarray(vectors))


def _reference(label_set: eval_set.LabelSet, eval_dir: Path = EVAL) -> list[eval_set.DistanceRow]:
    """The distance rows of the latest logged run of this set without a size filter."""
    version = eval_set.labels_sha256(eval_dir, label_set)
    runs, _ = eval_set.read_run_log(eval_dir)
    distances, _ = eval_set.read_distance_log(eval_dir)
    logged = {(d.run_at, d.labels_sha256) for d in distances if d.label_set == label_set.crawl_date}
    candidates = sorted(
        {
            r.run_at
            for r in runs
            if r.label_set == label_set.crawl_date
            and r.labels_sha256 == version
            and r.min_face_px == 0
            and (r.run_at, version) in logged
        }
    )
    if not candidates:
        return []
    return [
        d for d in distances if d.run_at == candidates[-1] and d.label_set == label_set.crawl_date
    ]


def sanity_check(
    label_set: eval_set.LabelSet,
    page_scores: scoring.Matrix,
    urls: Sequence[str],
    eval_dir: Path = EVAL,
) -> list[str]:
    """Differences between the baseline and the reference run; empty when they agree."""
    reference = _reference(label_set, eval_dir)
    if not reference:
        return ["no logged unfiltered run with distances for this set and labels version"]
    column = {u: i for i, u in enumerate(urls)}
    labels = label_set.labels()
    ours: dict[tuple[str, str], float] = {}
    for q, label in enumerate(labels):
        for url, i in column.items():
            d = 1.0 - float(page_scores[q, i])
            if d < CEILING:
                ours[(label, url)] = d
    logged: dict[tuple[str, str], float] = {
        (r.label, r.page_url): r.distance for r in reference if r.distance is not None
    }
    beyond = {(r.label, r.page_url) for r in reference if r.distance is None}
    return [
        *(f"{k}: found at {ours[k]:.4f}, not in the log" for k in sorted(ours.keys() - logged)),
        *(f"{k}: logged at {logged[k]:.4f}, not found" for k in sorted(logged.keys() - ours)),
        *(
            f"{k}: {ours[k]:.6f} against logged {logged[k]:.4f}"
            for k in sorted(ours.keys() & logged.keys())
            if abs(ours[k] - logged[k]) > TOLERANCE
        ),
        *(
            f"{k}: logged beyond the ceiling, found at {ours[k]:.4f}"
            for k in sorted(beyond & ours.keys())
        ),
    ]


def _per_label(
    model: str,
    score: str,
    rerank: str,
    page_scores: scoring.Matrix,
    urls: Sequence[str],
    labels: Sequence[str],
    truth: Mapping[str, frozenset[str]],
) -> list[dict[str, object]]:
    rows = list(scoring.pairs(page_scores, urls, labels, truth))
    best_wrong = max((s for _, t, s in rows if not t), default=-np.inf)
    worst_true = min((s for _, t, s in rows if t), default=np.inf)
    out: list[dict[str, object]] = []
    for label in labels:
        true = [s for lab, t, s in rows if lab == label and t]
        wrong = [s for lab, t, s in rows if lab == label and not t]
        out.append(
            {
                "model": model,
                "score": score,
                "rerank": rerank,
                "label": label,
                "true_pages": len(true),
                "true_above_every_wrong": sum(s > best_wrong for s in true),
                "separable": bool(true) and min(true) > max(wrong, default=-np.inf),
                "below_every_true": (not true) and max(wrong, default=-np.inf) < worst_true,
            }
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run", description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="crawl_date", type=date.fromisoformat, required=True)
    parser.add_argument("--only", choices=["baseline"], help="only the sanity check")
    parser.add_argument(
        "--eval",
        dest="eval_dir",
        type=Path,
        default=EVAL,
        help="the label-set folder to read (default queries/eval); a clean copy of the commit "
        "keeps photos added since from being rejected",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        choices=list(snapshot.MODELS),
        default=list(snapshot.MODELS),
        help="score only these models' half of the grid (default: both); an early look",
    )
    args = parser.parse_args(argv)
    snapshot.setup_logging()

    folder, errors = eval_set.read_folder(args.eval_dir)
    label_set = folder.set_for(args.crawl_date) if folder else None
    if label_set is None:
        log.error("no valid label set %s: %s", args.crawl_date, errors)
        return 2
    models = ["Facenet512"] if args.only else args.models
    if "Facenet512" not in models:  # the sanity check runs on it, before anything is reported
        log.error("--models must include Facenet512 (the sanity check)")
        return 2
    missing = [m for m in models if not snapshot.path(args.crawl_date, m).is_file()]
    if missing:
        log.error("no snapshot for %s; see the brief", ", ".join(missing))
        return 2
    labels = label_set.labels()
    truth = {label: frozenset(pages) for label, pages in label_set.present().items()}
    embedders: dict[str, FaceEmbedder] = {
        "Facenet512": DeepFaceEmbedder(),  # exactly the evaluator's query embedding
        "ArcFace": DeepFaceEmbedder("ArcFace"),
    }

    incomplete: dict[str, int] = {}
    results: list[dict[str, object]] = []
    summary: list[tuple[str, str, str, scoring.Metrics]] = []
    for model in models:
        snap = snapshot.Snapshot.load(snapshot.path(args.crawl_date, model))
        if snap.failed_images:
            incomplete[model] = len(snap.failed_images)
            log.warning(
                "%s snapshot is incomplete: %d images could not be embedded and have no faces",
                model,
                len(snap.failed_images),
            )
        index = scoring.normalize(snap.embeddings)
        pages = scoring.Pages.build(snap.face_image.tolist(), snap.image_pages)
        queries = query_vectors(label_set, embedders[model], args.eval_dir)
        if model == "Facenet512":
            baseline = pages.best(scoring.scores(queries, index, "raw"))
            problems = sanity_check(label_set, baseline, pages.urls, args.eval_dir)
            for problem in problems:
                log.error("sanity check: %s", problem)
            if problems:
                log.error("sanity check FAILED: no results are reported")
                return 1
            print("sanity check passed: the baseline reproduces the logged run")
            if args.only:
                return 0
        r_index = scoring.neighbour_means(index, index, skip_same_photo=True)
        for rerank in scoring.RERANKS:
            q = scoring.expand(queries, index) if rerank == "qe" else queries
            for score in scoring.SCORES:
                page_scores = pages.best(scoring.scores(q, index, score, r_index))
                m = scoring.metrics(page_scores, pages.urls, labels, truth)
                summary.append((model, score, rerank, m))
                results += _per_label(model, score, rerank, page_scores, pages.urls, labels, truth)

    target = snapshot.folder(args.crawl_date) / "results.csv"
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(
        f"\n{'model':<11} {'score':<6} {'rerank':<6} {'AUC':>6} {'recall@0':>9} "
        f"{'separable':>9} {'absent ok':>9}"
    )
    for model, score, rerank, m in summary:
        print(
            f"{model:<11} {score:<6} {rerank:<6} {m.auc:6.3f} {m.recall_at_zero_false:9.2f} "
            f"{m.separable_people:>7}/{len(truth)} "
            f"{m.absent_below_all_true:>7}/{len(label_set.absent())}"
        )
    for model, count in incomplete.items():
        print(f"note: {model} is missing the faces of {count} images that could not be embedded")
    print(f"\nper-label results: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
