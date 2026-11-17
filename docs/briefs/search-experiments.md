# Mission Brief — Search Experiments

Origin: the first evaluation (README, [Search accuracy evaluation](../../README.md#search-accuracy-evaluation)). On label set 2026-11-16 the search fails SC-001 and SC-003 at the 0.30 threshold. The 40 px face-size filter did not help: the 4 false matches are clear look-alike faces, and for person-d a wrong page is closer than both true pages. This brief measures, offline, whether standard retrieval techniques separate true from wrong matches better than raw cosine distance, alone or combined.

**This brief is written before any experiment is run.** The grid, the hyperparameters and the metrics below are fixed here and are not changed after seeing results. Every combination is reported, not only the best one.

## Goals

1. Score every combination of the grid against label set 2026-11-16 with threshold-free metrics.
2. Report all of them in the README, with what they show and how far they can be trusted.
3. Name at most one combination worth confirming on a second label set.

## Grid (12 combinations)

| Factor | Levels |
|---|---|
| Embedding | Facenet512 (the current index); ArcFace (the index re-embedded from the stored JPEGs with `retinaface` + `ArcFace`, `align=True`) |
| Score | raw cosine; CSLS; T-norm |
| Re-ranking | none; average query expansion |

- **Raw cosine:** `cos(q, f)`, as the search uses today (distance = 1 − cos).
- **CSLS** (Lample et al. 2018): `2·cos(q, f) − r_q − r_f`, where `r_x` is the mean cosine of `x` to its k = 10 nearest index faces. One adaptation: neighbours at a distance below 0.02 are skipped when computing `r_f`. The index stores each size of the same photo as a separate image by design, so without this a face would be counted as "close to everything" just for appearing in several renditions.
- **T-norm:** `(cos(q, f) − μ_q) / σ_q`, with the mean and standard deviation of the query's cosine over all index faces. Within one query it keeps the order, but it makes scores from different queries comparable, which matters for absent people.
- **Average query expansion:** the query is replaced by the normalized mean of itself and its top n = 2 index faces (by raw cosine), then scored with the chosen score.

A page's score is its best face score among the images the search would show for it: `indexed` images whose link the page's latest crawl refreshed, as in `src/face_index/search_query.sql`. Every face is used (no size filter). Query photos use their largest face, as the evaluator does.

## Metrics (threshold-free)

The score scales differ between methods, so a fixed threshold is not comparable. The pairs come from the label set: each present label's labelled pages are true pairs; every other page for that label, and every page for an absent label, is a wrong pair. Pages with no face are not pairs, and labelled pages with no face count as never found.

- **AUC:** the probability that a random true pair outscores a random wrong pair (ties count one half).
- **Recall at zero false matches:** the share of true pairs that outscore every wrong pair. 1.0 means one cutoff passes SC-001 to SC-003.
- **Separable people:** present labels whose worst true page outscores their best wrong page (0 to 5).
- **Absent people below every true page:** absent labels whose best page scores below every true pair (0 to 5).

## Sanity check

Facenet512 with raw cosine and no re-ranking must reproduce the logged run of 2026-11-17 13:19 UTC (no size filter): the same pages below a distance of 0.50 as `queries/eval/distances.csv`, each distance equal to the logged value at its logged precision (4 decimals, so within 5e-5), and the same labelled pages beyond that ceiling. If it does not, the harness is wrong and no results are reported.

## Constraints

- **Offline and read-only.** The index is exported once with the read-only role. ArcFace re-embeds the stored JPEGs into a local file. The live index and the search do not change.
- **Embeddings are never committed.** Exports live under `eval-snapshots/experiments/` (gitignored), because they describe people who gave no licence. Only aggregate numbers go into the README.
- **No default changes from these results.** With 10 people, one person moves recall by 20 points, and comparing 12 combinations can find a winner by luck. A combination is worth confirming only if it improves both AUC and recall at zero false matches over the baseline, and its gain is not carried by one person. Confirmation needs a second label set, labelled before the combination is chosen.

## Non-Goals

- Several query photos per person (needs extra licensed photos; a next step if nothing here helps).
- Tuning k, n, or any threshold.
- Changing the search, the index or the evaluator.

## How to run

On the labeller's machine, with the read-only DSN of the evaluation commands (`FACE_INDEX_DSN` and `PGPASSWORD`):

```bash
uv run python scripts/experiments/export_index.py --set 2026-11-16
uv run python scripts/experiments/run.py --set 2026-11-16 --only baseline
uv run python scripts/experiments/reembed.py --set 2026-11-16 --model ArcFace
uv run python scripts/experiments/run.py --set 2026-11-16
```

The second command is the sanity check; the full run refuses to start until it passes.

## Choice for confirmation (recorded 2026-11-17, before any run on set 2026-11-17)

Scored on set 2026-11-16 (sanity check passed). Only one combination meets the rule above: **ArcFace, raw cosine, no re-ranking**. AUC 0.999 against the baseline's 0.994, recall at zero false matches 0.88 against 0.62, separable people 5/5 against 4/5. The gain is +1 true page for person-a and +3 for person-b; without person-b it still improves recall at zero false matches (11/13 against 10/13). ArcFace with T-norm scores close to it but is not chosen: this brief allows at most one.

The confirmation compares this combination with the baseline (Facenet512, raw, none) on set 2026-11-17, with the same metrics. It is confirmed if it improves both AUC and recall at zero false matches there too. The other combinations are also scored on 2026-11-17, but only as a description; they cannot be chosen from that set.
