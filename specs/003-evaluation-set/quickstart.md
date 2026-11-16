# Quickstart: Search Evaluation Set

Validation guide for feature 003. Formats: [contracts/eval-set-files.md](contracts/eval-set-files.md). CLI: [contracts/evaluate-search-cli.md](contracts/evaluate-search-cli.md).

## Prerequisites

- The repository cloned, with `uv` and Docker installed.
- For sections 3 to 5: the stack running (`docker compose up -d`) and one indexing run finished, as in the README.

## 1. Validate the committed files (no database, no model)

```bash
uv run python scripts/evaluate_search.py --check
```

Expected: one `ok` line per file, exit 0. This is the first step of the CI `eval-set` job.

## 2. Run the mock end-to-end tests (as CI does)

```bash
uv run pytest tests/integration/test_evaluate_search_e2e.py
```

Needs the loopback test database (`FACE_INDEX_TEST_DSN`, see `tests/conftest.py`). Expected results:

| Fixture | Expected |
|---|---|
| clean | PASS, exit 0 |
| planted miss | FAIL, exit 1, the missed URL listed |
| absent look-alike | FAIL, exit 1 |
| on-site pair | FAIL, exit 1, extra pages listed |
| label error | FAIL, exit 1, reported as a label error, not as a miss |
| no face, or undecodable photo | exit 2, each photo path named with its reason |

## 3. Evaluate the latest label set against your index

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py
```

The read-only password comes from the Compose secrets volume at run time; nothing is typed or committed ([research R11](research.md#r11-read-only-database-access-from-the-host)).

Expected: the header line with the set, labels hash, profile and threshold; the index size; one row per label; then `PASS` or `FAIL`.

If your index was crawled on a different date than the set (see `methods.csv`), expect label errors and FAIL: the site has drifted. See section 5.

## 4. Record a real-model run (the labeller, on the labelling-date index)

Restore your local snapshot of the labelling-date index (never committed; `methods.csv` names its holder), then run:

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py --set <YYYY-MM-DD> --log
```

Expected: `runs.csv` gains one row per label. Commit it together with the README results table, which is copied from this run's output and never hand-filled.

## 5. Add a label set after the site drifts

1. Index the site, and keep a local snapshot of that database.
2. Add a new `sets/<crawl date>.csv` and a `methods.csv` row. Reuse existing labels and photos; add new `person-…` or `absent-…` labels with photos and attribution rows as needed. Leave out any absent label whose person now appears on the site.
3. Run section 1, then section 4 with `--set <crawl date>`.

## 5b. Correct an existing set, or take a photo down

1. Edit the set, or remove the photo, its attribution row and its label from every set. If a set falls below its minimums, add a replacement label in the same change.
2. Re-run section 4 for the latest set and commit the new `runs.csv` rows and README numbers with the change. Until then, section 1 fails with a stale-results error.
3. Say in the pull request what changed and why. For a takedown, a maintainer also removes the photo from git history; that step is outside this feature.

## 6. Final check

- The CI `eval-set` job is green and finishes in under 5 minutes.
- The README shows the latest results, labelling method, bias, crawl date and re-labelling steps.
- Feature 002's quickstart §7 points here.
