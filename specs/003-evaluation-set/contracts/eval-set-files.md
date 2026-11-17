# Contract: Evaluation Files under `queries/eval/`

The committed files a contributor edits and CI validates. Field rules are in [data-model.md](../data-model.md); the reasons for this layout are in [research.md](../research.md) R3, R4 and R7.

## Layout

```text
queries/eval/
├── photos/<label>.jpg     # one per label; file name = neutral label
├── attribution.csv
├── methods.csv
├── runs.csv               # append-only run log
├── distances.csv          # append-only page distances of each logged run (research R12)
├── threshold-fit.csv      # the pairs the match threshold was fitted on (rewritten by each fit)
└── sets/<YYYY-MM-DD>.csv  # one per labelling crawl
```

`.gitignore` keeps `queries/*` ignored except `queries/README.md` and `queries/eval/`.

## Common CSV rules

- UTF-8, comma-separated, `\n` line endings, one header row. The header must equal the column list below, in order.
- Every value is read as text and typed by validation, with no inference. An empty cell is `""`, never "missing".
- An error names `<file>:<line>`, where line 1 is the header. Every error in every file is reported, and nothing is evaluated while any remains.

## `sets/<YYYY-MM-DD>.csv`

```csv
label,page_url,hard_case,lookalike_of
person-a,https://edition.cnn.com/2026/11/20/politics/example-story,none,
person-a,https://edition.cnn.com/2026/11/20/us/another-story,small_face,
person-b,https://edition.cnn.com/2026/11/20/world/third-story,look_alike,person-a
absent-a,,look_alike,person-a
absent-b,,none,
```

- `hard_case`: `none`, `small_face` or `look_alike`.
- A present label has one row per page; an absent label has exactly one row, with an empty `page_url`.

## `attribution.csv`

```csv
path,source_url,author,licence
photos/person-a.jpg,https://commons.wikimedia.org/w/index.php?curid=123456,Jane Photographer,CC-BY-SA-4.0
```

- Paths are relative to `queries/eval/`.
- Accepted source URL forms and licences: research R4.

## `methods.csv`

```csv
crawl_date,found_by,labeller,snapshot_holder,onsite_pair
2026-11-20,both,itaizg,itaizg,false
```

## `runs.csv`

```csv
run_at,label_set,labels_sha256,model_profile,threshold,min_face_px,faces,images,pages,label,expected,returned,missed,extra,label_errors,verdict
2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,facenet512-retinaface-cosine,0.30,0,4812,2960,61,person-a,4,4,0,0,0,PASS
```

- Rows are appended only. CI rejects a pull request in which the base branch's log is not an unchanged prefix of the new one.

## `distances.csv`

```csv
run_at,label_set,labels_sha256,label,page_url,labelled,distance
2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,person-a,https://edition.cnn.com/2026/11/20/politics/example-story,true,0.1834
2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,person-a,https://edition.cnn.com/world,false,0.4127
2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,person-a,https://edition.cnn.com/2026/11/20/us/another-story,true,
```

- Written by the same `--log` run as `runs.csv`: every page found up to the report ceiling (`REPORT_CEILING`: 0.50 for the runs logged with Facenet512, 0.80 from the ArcFace switch on), and each labelled page not found up to it (empty `distance`).
- Every row's `run_at`, `label_set` and `labels_sha256` must match a run in `runs.csv`. Appended only, like `runs.csv`.

## `threshold-fit.csv`

```csv
model,label_set,label,page_url,labelled,distance
ArcFace,2026-11-16,person-a,https://edition.cnn.com/world/middle-east,true,0.3902
ArcFace,2026-11-16,person-a,https://edition.cnn.com/world,false,0.6214
ArcFace,2026-11-16,person-a,https://edition.cnn.com/2026/11/16/us/another-story,true,
```

- Written by `scripts/experiments/fit_threshold.py`, never by hand: one row per (label, page) pair of each fitted set within the report ceiling, scored with the named model (raw cosine, best face per page), plus each labelled page not found within it (empty `distance`). The file is rewritten by each fit; git keeps the history.
- `face_index.eval_set.fit_threshold` reads it and returns the cutoff on a 0.01 grid with the highest pooled pair-level F1 (a match is strictly below the cutoff; a tie goes to the lowest cutoff). A unit test checks that this equals `MATCH_THRESHOLD` and that `model` equals `model_profile.MODEL`, so the threshold cannot drift from its data.
- It holds distances and labels only: no embeddings and no names.

## CI rejections (every one names the file and line, or the path)

| Check | Example |
|---|---|
| Header differs | missing `hard_case` column |
| Wrong type or enum | `hard_case=tiny`; `onsite_pair=yes` |
| Bad page URL | not https; host not an indexed site; `normalize_page_url` would change it |
| Duplicate row | same `label,page_url` twice |
| Role rules | absent row with a page; present label without pages |
| Look-alike rules | `look_alike` without `lookalike_of`; `lookalike_of` not a present label in the set |
| Set minimums | no set at all; fewer than 5 present or 5 absent; no absent look-alike; no small-face page; `onsite_pair` disagrees with the set |
| Methods coverage | set without a `methods.csv` row, or a row without a set |
| Photo and attribution | photo without attribution; attribution without photo; photo used by no set; photo that does not decode |
| Folder layout | any file other than `sets/`, `photos/`, `attribution.csv`, `methods.csv`, `runs.csv`, `distances.csv`, `threshold-fit.csv` (e.g. an index snapshot); any symbolic link (checked before anything is read) |
| Attribution values | licence not allowed; source URL not in page-ID form |
| Run log | header or type wrong; `run_at` not UTC; the base branch's log is not a prefix of the new one (PR) |
| Distance log | header or type wrong; `labelled` not `true`/`false`; `distance` outside [0, 2]; a row whose run is not in `runs.csv`; the base branch's log is not a prefix of the new one (PR) |
| Threshold fit | header or type wrong; `distance` outside [0, report ceiling]; a pair without a distance that is not labelled |
| Field count | a row with more or fewer fields than the header |
| Free text | `author`, `labeller`, `snapshot_holder` or `model_profile` starting with `=`, `+`, `-`, `@` or a tab (spreadsheet formula), or holding control characters; a page URL holding control characters |
| Stale results | the latest set's most recent logged run has a different `labels_sha256` than its current files, e.g. after a correction, or lacks one row per label with one verdict (`--check` only) |
