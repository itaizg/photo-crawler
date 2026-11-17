# Data Model: Search Evaluation Set

**Feature**: [spec.md](spec.md) | **Research**: [research.md](research.md)

All records are frozen Pydantic models (`face_index.models.Record`). CSV files are read as text (research R1) and validated into these models once, at load. Files live under `queries/eval/` (research R3). File formats are specified in [contracts/eval-set-files.md](contracts/eval-set-files.md).

## Label

A neutral identifier: `person-<a-z>+` (present) or `absent-<a-z>+` (absent).

- `role` is derived from the prefix: `present | absent`. It is fixed for every set (spec FR-001).
- `photo` is derived: `photos/<label>.jpg`, relative to `queries/eval/` (spec FR-005).

## Label set row

One row of `sets/<crawl_date>.csv`.

| Field | Type | Rules |
|---|---|---|
| `label` | Label | required |
| `page_url` | str | present: required, https, canonical (`normalize_page_url(u) == u`), host in an enabled site's `page_hosts`. Absent: must be empty. |
| `hard_case` | `none \| small_face \| look_alike` | `small_face` only on present rows. `look_alike` needs `lookalike_of`. |
| `lookalike_of` | Label or empty | required if and only if `hard_case = look_alike`. It must be a present label in the same set, different from `label`. |

Because absent rows carry no page, a page can't be shared between a present and an absent label, and the brief's "no page shared by a person and an absent row" holds by construction.

## Label set

`sets/<YYYY-MM-DD>.csv`, where `crawl_date` comes from the file name.

- **Uniqueness**: (`label`, `page_url`) is unique. An absent label appears on exactly one row.
- **Minimums** (spec FR-001a to FR-003), over distinct labels:
  - ≥ 5 present labels and ≥ 5 absent labels
  - ≥ 1 absent row with `hard_case = look_alike`
  - ≥ 1 present row with `hard_case = small_face`
  - if `methods.onsite_pair` is true: ≥ 1 present row with `hard_case = look_alike`; if false: none
- **Lifecycle**: a set is added per labelling crawl. It may later be corrected (spec FR-001b); a correction changes its `labels_sha256`, so logged runs before and after it stay distinguishable. **Stale-results rule** (checked by `--check` only, research R10): the most recent logged run (the last one in file order; the log is append-only) for the latest set must have that set's current labels version.
- **Selection**: the latest `crawl_date` by default, or one named with `--set`.
- **At least one set** must exist; an empty `sets/` folder is an error.

## Attribution row

`attribution.csv`, keyed by `path`.

| Field | Type | Rules |
|---|---|---|
| `path` | str | `photos/<label>.jpg`; unique |
| `source_url` | str | one of the page-ID forms in research R4 |
| `author` | str | non-empty; the photographer |
| `licence` | enum | one of the licences in research R4 |

**Cross-file rule**: the set of `path` values equals the set of files in `photos/` exactly, and every photo is used by a label in at least one set (spec FR-014).

## Labelling method row

`methods.csv`, keyed by `crawl_date`.

| Field | Type | Rules |
|---|---|---|
| `crawl_date` | date | equals one set file's date; one row per set |
| `found_by` | `browse \| search \| both` | |
| `labeller` | str | the contributor's handle (not a labelled person) |
| `snapshot_holder` | str | who keeps the local index snapshot (spec FR-025a) |
| `onsite_pair` | `true \| false` | whether the crawl had an on-site look-alike pair |

## Index size

Returned by the new `SearchIndex.index_size(profile_id)` (research R5).

| Field | Type | Meaning |
|---|---|---|
| `faces` | int | faces on indexed images under the profile |
| `images` | int | images with status `indexed` |
| `pages` | int | page rows |

## Label report (in memory)

Extends the existing `PersonReport`, for one label in one run.

| Field | Type | Meaning |
|---|---|---|
| `label` | Label | |
| `expected` | int | labelled pages that are indexed (present); 0 (absent) |
| `returned` | int | distinct pages returned |
| `missed` | tuple[str, ...] | expected pages not returned |
| `extra` | tuple[str, ...] | returned pages not labelled for this label (all returned pages, for absent) |
| `label_errors` | tuple[str, ...] | labelled pages with no `page` row |
| `recall` | property | `(expected − missed) / expected`, or 1.0 when `expected = 0` |

**Verdict**: PASS when every report has no `label_errors` and no `extra`, and every present report has `recall ≥ 0.80`. Otherwise FAIL.

## Run log row

`runs.csv`, one row per (run, label). It is append-only (research R7).

| Field | Type | Meaning |
|---|---|---|
| `run_at` | datetime (UTC, ISO 8601, seconds) | identifies the run; all of a run's rows share it |
| `label_set` | date | the set's crawl date |
| `labels_sha256` | 12 hex characters | hash of the set file and its photos |
| `model_profile` | str | `model_profile.KEY` |
| `threshold` | float | `model_profile.MATCH_THRESHOLD` |
| `faces`, `images`, `pages` | int | index size |
| `label` | Label | |
| `expected`, `returned`, `missed`, `extra`, `label_errors` | int | counts from the label report |
| `verdict` | `PASS \| FAIL` | the run's verdict, repeated on each row |

**Privacy** (spec FR-028): the log holds labels and counts only, with no URLs, names or face data.

## Distance log row

`distances.csv`, one row per (run, label, page), written by the same `--log` run (research R12). It is append-only, and every row must belong to a run in `runs.csv`.

| Field | Type | Meaning |
|---|---|---|
| `run_at`, `label_set`, `labels_sha256` | as in the run log | the run the row belongs to |
| `label` | Label | |
| `page_url` | str | a page found for this label up to the report ceiling (0.50), or a labelled page not found up to it |
| `labelled` | `true \| false` | the page is labelled for this label in the set |
| `distance` | float in [0, 2], or empty | the page's best cosine distance to the label's query face; empty for a labelled page not found up to the ceiling |

**Privacy**: like the label sets, it pairs neutral labels with public page URLs. A distance is a number, not face data.
