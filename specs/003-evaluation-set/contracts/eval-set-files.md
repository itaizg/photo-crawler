# Contract: Evaluation Files under `queries/eval/`

The committed files a contributor edits and CI validates. Field rules are in [data-model.md](../data-model.md); the reasons for this layout are in [research.md](../research.md) R3, R4 and R7.

## Layout

```text
queries/eval/
├── photos/<label>.jpg     # one per label; file name = neutral label
├── attribution.csv
├── methods.csv
├── runs.csv               # append-only run log
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
run_at,label_set,labels_sha256,model_profile,threshold,faces,images,pages,label,expected,returned,missed,extra,label_errors,verdict
2026-11-20T18:04:11Z,2026-11-20,a1b2c3d4e5f6,facenet512-retinaface-cosine,0.30,4812,2960,61,person-a,4,4,0,0,0,PASS
```

- Rows are appended only. CI rejects a pull request that removes or changes an existing line.

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
| Folder layout | any file other than `sets/`, `photos/`, `attribution.csv`, `methods.csv`, `runs.csv` (e.g. an index snapshot); any symbolic link (checked before anything is read) |
| Attribution values | licence not allowed; source URL not in page-ID form |
| Run log | header or type wrong; `run_at` not UTC; existing line removed or changed (PR diff) |
| Free text | `author`, `labeller`, `snapshot_holder` or `model_profile` starting with `=`, `+`, `-`, `@` or a tab (spreadsheet formula), or holding control characters; a page URL holding control characters |
| Stale results | the latest set's most recent logged run has a different `labels_sha256` than its current files, e.g. after a correction, or lacks one row per label with one verdict (`--check` only) |
