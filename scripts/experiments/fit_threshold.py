"""Fit the ArcFace match threshold on the label sets' snapshots (docs/briefs/search-experiments.md).

    uv run python scripts/experiments/fit_threshold.py --set 2026-11-16 --confirm 2026-11-17

Needs the ArcFace snapshots (reembed.py) and the face models; no database. Scores each label's
query photo against every page of the set's snapshot, raw cosine and best face per page, and
writes every pair within the report ceiling (and each labelled page with none) to
queries/eval/threshold-fit.csv. ``eval_set.fit_threshold`` picks the cutoff from that file, and a
unit test repeats the fit on it, so the match threshold in model_profile.py cannot drift from
its data. The file holds distances and labels only: no embeddings and no names.

Prints the fitted cutoff and what the three criteria of the evaluator (SC-001 to SC-003) read at
the cutoffs worth comparing. ``--set`` names the sets the cutoff is fitted on; their numbers are
in-sample. ``--confirm`` names sets that are only read at the fitted cutoff: they never enter the
fit or the CSV, so their numbers are an out-of-sample check. A present person's page missing from
the labels counts as a wrong page here, as in the evaluator, so incomplete labels pull the cutoff
down; that is the labelling bias the README states, not something the fit can correct.
Exit 0 on success, 2 when an input is missing.
"""

import argparse
import csv
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Final

import numpy as np
import run
import scoring
import snapshot

from face_index import eval_set
from face_index.eval_set import FitPair
from face_index.faces import DeepFaceEmbedder

MODEL: Final[str] = "ArcFace"
DEEPFACE_CUTOFF: Final[float] = 0.68  # find_threshold("ArcFace", "cosine"), for comparison


def pairs_for(label_set: eval_set.LabelSet, eval_dir: Path = run.EVAL) -> list[FitPair]:
    """Every (label, page) pair of one set within the ceiling, plus labelled pages never found."""
    snap = snapshot.Snapshot.load(snapshot.path(label_set.crawl_date, MODEL))
    index = scoring.normalize(snap.embeddings)
    pages = scoring.Pages.build(snap.face_image.tolist(), snap.image_pages)
    queries = run.query_vectors(label_set, DeepFaceEmbedder(MODEL), eval_dir)
    page_scores = pages.best(scoring.scores(queries, index, "raw"))
    truth = label_set.present()
    out: list[FitPair] = []
    for q, label in enumerate(label_set.labels()):
        true_pages = frozenset(truth.get(label, ()))
        found: set[str] = set()
        for column, url in enumerate(pages.urls):
            value = float(page_scores[q, column])
            distance = 1.0 - value
            if np.isfinite(value) and distance < eval_set.REPORT_CEILING:
                found.add(url)
                out.append(_pair(label_set, label, url, url in true_pages, distance))
        out += [_pair(label_set, label, url, True, None) for url in sorted(true_pages - found)]
    return out


def _pair(
    label_set: eval_set.LabelSet, label: str, url: str, labelled: bool, distance: float | None
) -> FitPair:
    return FitPair(
        model=MODEL,
        label_set=label_set.crawl_date,
        label=label,
        page_url=url,
        labelled=labelled,
        distance=None if distance is None else round(distance, 4),
    )


def write(target: Path, pairs: Sequence[FitPair]) -> None:
    rows = sorted(pairs, key=lambda p: (p.label_set, p.label, p.page_url))
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(eval_set.FIT_COLUMNS)
        for p in rows:
            writer.writerow(
                [
                    p.model,
                    p.label_set.isoformat(),
                    p.label,
                    p.page_url,
                    "true" if p.labelled else "false",
                    "" if p.distance is None else f"{p.distance:.4f}",
                ]
            )


def reading(
    pairs: Sequence[FitPair], cutoff: float, label_sets: Sequence[eval_set.LabelSet]
) -> str:
    """The pooled pair-level numbers and the evaluator's criteria at one cutoff.

    The people of each set come from the set itself, not from the pairs: an absent person with
    no page within the ceiling has no pair, and must still count in SC-003.
    """
    matched = [p for p in pairs if p.distance is not None and p.distance < cutoff]
    tp = sum(p.labelled for p in matched)
    fp = len(matched) - tp
    positives = sum(p.labelled for p in pairs)
    precision = tp / (tp + fp) if matched else 0.0
    recall = tp / positives
    f1 = 2 * tp / (2 * tp + fp + positives - tp)
    lines = [
        f"  pairs: precision {precision:.3f}, recall {recall:.3f}, F1 {f1:.3f} "
        f"({tp} of {positives} labelled pages, {fp} other pages)"
    ]
    for label_set in sorted(label_sets, key=lambda s: s.crawl_date):
        crawl_date = label_set.crawl_date
        here = [p for p in pairs if p.label_set == crawl_date]
        labels = sorted(label_set.labels())
        present = [x for x in labels if eval_set.role(x) == "present"]
        absent = [x for x in labels if eval_set.role(x) == "absent"]

        def recall_of(label: str, here: Sequence[FitPair] = here) -> float:
            own = [p for p in here if p.label == label and p.labelled]
            hit = [p for p in own if p.distance is not None and p.distance < cutoff]
            return len(hit) / len(own)

        def extras(label: str, here: Sequence[FitPair] = here) -> int:
            return sum(
                1
                for p in here
                if p.label == label
                and not p.labelled
                and p.distance is not None
                and p.distance < cutoff
            )

        lines.append(
            f"  {crawl_date}: SC-001 {sum(recall_of(x) >= eval_set.MIN_RECALL for x in present)}"
            f"/{len(present)} present people with recall >= {eval_set.MIN_RECALL:.2f}; "
            f"SC-002 {sum(extras(x) == 0 for x in present)}/{len(present)} without extra pages; "
            f"SC-003 {sum(extras(x) == 0 for x in absent)}/{len(absent)} absent people with "
            "nothing returned"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fit_threshold", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--set", dest="dates", type=date.fromisoformat, action="append", required=True
    )
    parser.add_argument(
        "--confirm", dest="confirm", type=date.fromisoformat, action="append", default=[]
    )
    parser.add_argument("--eval", dest="eval_dir", type=Path, default=run.EVAL)
    args = parser.parse_args(argv)
    if overlap := set(args.dates) & set(args.confirm):
        print(f"error: a set cannot be fitted and confirmed: {sorted(overlap)}", file=sys.stderr)
        return 2

    folder, errors = eval_set.read_folder(args.eval_dir)
    if folder is None:
        print(f"error: the evaluation folder is not valid: {errors}", file=sys.stderr)
        return 2
    chosen: dict[date, eval_set.LabelSet] = {}
    for crawl_date in [*args.dates, *args.confirm]:
        label_set = folder.set_for(crawl_date)
        if label_set is None:
            print(f"error: no label set {crawl_date}", file=sys.stderr)
            return 2
        if not snapshot.path(crawl_date, MODEL).is_file():
            print(f"error: no {MODEL} snapshot for {crawl_date}; see the brief", file=sys.stderr)
            return 2
        chosen[crawl_date] = label_set
    fitted = [chosen[d] for d in args.dates]
    pairs = [p for s in fitted for p in pairs_for(s, args.eval_dir)]
    target = args.eval_dir / eval_set.FIT_FILE
    write(target, pairs)
    cutoff = eval_set.fit_threshold(pairs)
    print(f"{len(pairs)} pairs written to {target}\n")
    print(f"fitted cutoff {cutoff:.2f} (maximum pooled pair-level F1, lowest on a tie):")
    print(reading(pairs, cutoff, fitted))
    print(f"\nDeepFace's own ArcFace cosine cutoff {DEEPFACE_CUTOFF:.2f}:")
    print(reading(pairs, DEEPFACE_CUTOFF, fitted))
    if args.confirm:
        held_out = [chosen[d] for d in args.confirm]
        checked = [p for s in held_out for p in pairs_for(s, args.eval_dir)]
        print(f"\nout of sample (not fitted, not written) at the fitted cutoff {cutoff:.2f}:")
        print(reading(checked, cutoff, held_out))
    print("\ncurve (cutoff: pooled F1)")
    for step in range(1, round(eval_set.REPORT_CEILING / eval_set.FIT_STEP) + 1):
        t = round(step * eval_set.FIT_STEP, 2)
        matched = [p for p in pairs if p.distance is not None and p.distance < t]
        tp = sum(p.labelled for p in matched)
        positives = sum(p.labelled for p in pairs)
        print(f"  {t:.2f}: {2 * tp / (2 * tp + len(matched) - tp + positives - tp):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
