# Contract: `evaluate_search.py` Command Line

The interface a contributor and CI use, and what the tests assert. Implemented in `scripts/evaluate_search.py`; loading and validation live in `src/face_index/eval_set.py`.

## Invocation

```text
# Evaluate a label set against the Compose database (read-only role):
FACE_INDEX_DSN=postgresql://faces_reader:…@127.0.0.1:5432/faces \
  uv run python scripts/evaluate_search.py [FOLDER] [--set YYYY-MM-DD] [--log]

# Validate every evaluation file, with no database and no model (CI):
uv run python scripts/evaluate_search.py [FOLDER] --check
```

`FOLDER` defaults to `queries/eval`. Arguments are parsed with `argparse`, as `face_matcher` and `export_image` do.

## Options

| Option | Default | Meaning | Validation |
|---|---|---|---|
| `FOLDER` | `queries/eval` | Evaluation folder ([eval-set-files.md](eval-set-files.md)) | Must be a directory. A `.toml` file gets exit 64 with "labels are now CSV label sets under queries/eval/sets/". |
| `--set D` | latest set | The label set `sets/D.csv` to evaluate | ISO date of an existing set, or exit 64 listing the available sets |
| `--log` | off | Append this run to `runs.csv` (real model only) | Not allowed with `--check` (exit 64) |
| `--check` | off | Validate all files and exit; no database, no model | – |
| `-h, --help` | | Usage and exit statuses | – |

There is no `--threshold`: the evaluator always uses `model_profile.MATCH_THRESHOLD` (spec FR-016).

## Order of work

1. Parse the arguments; load and validate **every** file in `FOLDER` (the same checks as `--check`). Any error → exit 2.
2. Connect; read the profile id (none → exit 2) and the index size.
3. Call `known_pages` with every labelled URL of the selected set. A missing URL becomes a label error for its label.
4. Embed every photo. Each photo with no detected face is listed, then exit 2. The largest face of each photo is used.
5. Search each label's face with `search_faces` at the default threshold.
6. Print the report and the verdict; with `--log`, append to `runs.csv`.

## Output

stdout, UTF-8. Paths are relative to `FOLDER`; labels are neutral.

```text
label set 2026-11-20 (labels a1b2c3d4e5f6), profile facenet512-retinaface-cosine, threshold 0.30
index: 4812 faces, 2960 images, 61 pages

label          expected returned  recall  extra  errors
person-a              4        4    1.00      0       0
person-b              3        2    0.67      0       0
    missed: https://edition.cnn.com/2026/11/20/politics/…
person-c              2        2    1.00      0       1
    label error (page not indexed): https://edition.cnn.com/2026/11/19/world/…
absent-a              0        0       –      0       0
absent-b              0        1       –      1       0
    extra:  https://edition.cnn.com/2026/11/20/us/…
FAIL
```

stderr: progress (`loading face model…`) and errors. Never the DSN.

`--check` prints one line per validated file and `ok`, or one line per error, each in the form `<file>:<line>: <message>` (line 1 is the header).

## Exit statuses

| Exit | Meaning |
|---|---|
| 0 | PASS ([data-model.md](../data-model.md#label-report-in-memory)) |
| 1 | FAIL: verdict computed; any label error, recall < 0.80, extra page, or absent match |
| 2 | Cannot evaluate: invalid files, photo with no face (all listed), no model profile, database unreachable |
| 64 | Usage error |

With `--check`: 0 when all files are valid, 2 otherwise.

## Logging

- Only `main()` with `--log` writes, and `main()` always uses `DeepFaceEmbedder`. Tests call `run(…, log_to=None)`.
- Rows are appended only for exit 0 or 1, never for exit 2.
- The file is created with its header if missing; existing lines are never rewritten.
- `run_at` is the UTC time at step 2.

## Read-only guarantee

The evaluator only reads the database, through `SearchIndex` (`profile_id`, `index_size`, `known_pages`, `matching_pages`). It writes nothing but `runs.csv`, and only when `--log` is given.
