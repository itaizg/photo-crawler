# Research: Search Evaluation Set

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md) | **Date**: 2026-11-16

Each entry: Decision, Rationale, Alternatives considered. Facts about existing code were read from the repository on this date.

## R1. Reading CSV with pandas, no type inference

**Decision**: Read every table with `pandas.read_csv(path, dtype="string", keep_default_na=False, na_filter=False)`. Every column then arrives as text, and empty cells stay `""` instead of becoming NaN. The header must equal the expected column tuple exactly; then each row becomes a frozen Pydantic model through `model_validate` in strict mode for enums and dates. A failure becomes one error string, `<file>:<line>: <message>`, where the line is the row index + 2 (counting the header). Errors are returned, not raised (R10).

**Rationale**: The brief requires explicit dtypes and no inference. Reading everything as `string` is the explicit dtype, and Pydantic does the typing (constitution IX: validate once, at the edge). `keep_default_na=False` stops `"NA"` or `""` from silently turning into missing values. The only `try` is the one around `model_validate` for a single row, at the file boundary; it turns `ValidationError` into that row's error string (constitution VIII).

**Alternatives**: A per-column `dtype` map with `bool` and `int` columns was rejected: pandas would then reject or coerce values before Pydantic could name the row. The stdlib `csv` module would be simpler, but the brief mandates pandas as the reader.

## R2. pandas as a direct dependency

**Decision**: Add `"pandas>=3.1,<3.2"` to `[project].dependencies`. The existing `constraint-dependencies` pin (`pandas==3.1.0`) keeps it on Airflow's version. Run `uv lock`; the diff may touch only the project's own dependency list (pandas 3.1.0 is already locked via deepface).

**Rationale**: The brief rules out relying on a transitive dependency. The constraint pin already exists, so the lock gains no package.

**Alternatives**: Adding pandas to a dev group was rejected: the evaluator runs from the project environment, and the brief asks for a direct dependency.

## R3. Where the label sets and photos live, and how they are named

**Decision**:

```text
queries/eval/
├── photos/<label>.jpg   # one photo per neutral label, shared by every set
├── attribution.csv      # one row per photo
├── methods.csv          # one row per label set: how it was labelled
├── runs.csv             # run log, append-only
└── sets/<YYYY-MM-DD>.csv  # one label set per labelling crawl; the file name is the crawl date
```

- A label's photo path is derived as `photos/<label>.jpg` (convention over configuration, constitution VI), so set rows carry no photo column. "One photo per label, shared by every set" then holds by construction.
- A label's role comes from its prefix: `person-…` is present, `absent-…` is absent. "A label never changes role" also holds by construction.
- `.gitignore` gains `!queries/eval/` after `queries/*`; every other query photo stays ignored.

**Rationale**: Deriving the photo path and role removes two whole classes of validation (inconsistent photo per label, role switching between sets) instead of checking for them. Dated file names make "latest set" a sort, with no extra index file.

**Alternatives**: One CSV holding every set, with a `crawl_date` column, was rejected: re-labelling would edit a shared file, and append-only would be harder to review. A `photo` column per row was rejected because it repeats a fact that the label already gives.

**Spec note**: FR-005 says paths are relative to "the label sets' folder". With this layout they are relative to the evaluation folder `queries/eval/`; FR-005 has been reworded to say so.

## R4. Licences and the page-ID source URL form

**Decision**: Accepted licence values: `PD`, `CC0-1.0`, `CC-BY-2.0`, `CC-BY-3.0`, `CC-BY-4.0`, `CC-BY-SA-2.0`, `CC-BY-SA-3.0`, `CC-BY-SA-4.0`. Accepted source URL forms (full match):

| Host | Form |
|---|---|
| Wikimedia Commons | `https://commons.wikimedia.org/w/index.php?curid=<digits>` |
| Flickr | `https://www.flickr.com/photo.gne?id=<digits>` |
| Unsplash | `https://unsplash.com/photos/<11-character id>` |

**Rationale**: These are the licences the brief allows, written as SPDX-style identifiers so they are unambiguous in review. Each URL form identifies the photo by number or ID alone, and resolves to the photo page where the licence can be verified. Flickr's usual `/photos/<user>/<id>` form can embed a user name, and Unsplash's slug form embeds a description of the person, so both are rejected. Non-commercial (NC) and no-derivatives (ND) licences are rejected because they don't allow free redistribution with the repository.

**Alternatives**: Allowing any https URL was rejected because it can't enforce the neutral-name rule.

## R5. Detecting label errors and measuring index size

**Decision**: Extend the read-only `SearchIndex` protocol with two methods, both implemented in `PostgresSearchIndex` as parameterized `SELECT`s that the `faces_reader` role can run:

- `known_pages(urls: Sequence[str]) -> frozenset[str]`: which of the given URLs have a `page` row (`url = ANY(%s)`).
- `index_size(profile_id: int) -> IndexSize`: faces under the profile on indexed images (the existing count), plus the count of indexed images and the count of pages.

The evaluator calls `known_pages` once with every labelled URL in the set, before searching. A labelled URL missing from the result is a label error: it is reported, excluded from recall, and makes the verdict FAIL (FR-018).

**Rationale**: A `page` row exists only once the crawler visited that URL, which is exactly "the page was indexed". One batched query keeps the run fast. Both methods are reads, so SC-007 of feature 002 (the database is left unchanged) still holds.

**Alternatives**: Inferring a label error from "never returned" was rejected, because it can't tell a search miss from a missing page, and that distinction is FR-018's whole point. Querying with raw SQL from the script was rejected because it would bypass the protocol seam (constitution VI).

## R6. Verdict, exit statuses and what counts as "cannot evaluate"

**Decision**:

| Exit | Meaning |
|---|---|
| 0 | PASS: no label errors, every present label's recall ≥ 0.80, no extra pages, no absent matches |
| 1 | FAIL: a verdict was computed and some condition failed (label errors included) |
| 2 | Cannot evaluate: invalid evaluation files, a photo with no detected face or that can't be decoded or detected (all such photos listed, with the reason), no model profile in the index, database unreachable |
| 64 | Usage error |

All photos are embedded before any search, so every unusable photo is reported in one run. `prepare_query` raises `NoFaceError` (no face) or `QueryImageError` (not decodable, rejected by the normalizer, or detection failed); both are caught per photo at this model boundary. The existing "no profile" exit changes from 1 to 2, so that 1 always means a computed FAIL.

**Rationale**: CI tests and contributors need to tell "the search failed the criteria" apart from "the set or the environment is broken". Constitution XII requires reporting everything, not stopping at the first problem.

**Alternatives**: Keeping exit 1 for everything was rejected: the mock tests could then pass on a broken fixture.

## R7. Run log format and append-only enforcement

**Decision**: `queries/eval/runs.csv` is in long format, one row per (run, label), with the run's conditions repeated on each of its rows. Columns are in [data-model.md](data-model.md#run-log-row). Recall is not stored; it is derived from `expected` and `missed` (DRY). `--log` appends a run's rows only for a computed verdict (exit 0 or 1). Only `main()`, which always uses `DeepFaceEmbedder`, can log; `run()` takes `log_to: Path | None`, and tests pass `None`, so mock runs are never logged. Labels version is `labels_sha256`: the first 12 hex characters of the SHA-256 of the set file's bytes followed by each referenced photo's bytes, in label order. It identifies the exact inputs without needing git inside the run.

Append-only is enforced in CI on pull requests: `git diff --unified=0 <base>...HEAD -- queries/eval/runs.csv` must contain no removed lines. The same step rejects a modified or deleted existing label set: `git diff --name-only --diff-filter=MDR <base>...HEAD -- queries/eval/sets/` must be empty (spec FR-001).

**Rationale**: Long format is what pandas reads natively, and it lets a reader filter by label across runs. A content hash ties results to the exact labels and photos, including a replaced photo, which a git commit would also catch but only when committed.

**Alternatives**: One wide row per run was rejected: its columns would vary with the label set. A JSON log was rejected because the brief standardizes on CSV, which is reviewable in a diff.

## R8. The CI job

**Decision**: Add an `eval-set` job to `.github/workflows/ci.yml`. It inherits the workflow's `permissions: contents: read` and `concurrency`, and has `timeout-minutes: 5`, the same pinned `checkout` and `setup-uv` SHAs, `uv sync --locked`, and the same `pgvector/pgvector:pg17` service definition as `test`. Steps:

1. `uv run --locked python scripts/evaluate_search.py --check`: validates every set, attribution, methods and the run log, without a database.
2. On pull requests, the append-only check for the run log and existing label sets (R7). This needs `fetch-depth: 0` on checkout.
3. `uv run --locked pytest tests/integration/test_evaluate_search_e2e.py`: the seeded mock runs.

The job runs on every pull request, not only those touching the set.

**Rationale**: A path-filtered workflow that doesn't run leaves a required check pending, which the Quality Gates forbid ("failing or skipped required check"). The job takes well under a minute, so running it always costs little and keeps the brief's guarantee ("every pull request that touches either") as a subset. The integration tests also run in `test`, which is an accepted duplication of a few seconds.

**Alternatives**: A separate path-filtered workflow was rejected (pending required check). Folding everything into `test` was rejected because the brief asks for a job with its own timeout and visible status (SC-003).

## R9. Mock fixtures that prove the evaluator can FAIL

**Decision**: `tests/integration/test_evaluate_search_e2e.py` seeds pgvector through `PostgresIndexStore` with generated images from `tests/images.py`, and embeds them with a `FakeEmbedder` whose `lookup` maps image bytes to a chosen identity key. Two images with the same key get identical embeddings, so they match. The tests write small fixture evaluation folders into `tmp_path`, using the real file formats:

- **clean**: 5 present labels, 5 absent labels, an absent look-alike whose key differs from everyone's, a small-face page → PASS (exit 0).
- **planted miss**: one person labelled on 2 pages, one of which carries no image of them → recall 0.50 → FAIL (exit 1), and the missed URL is printed.
- **planted look-alike**: an absent label whose key equals `person-a`'s → it returns `person-a`'s pages → FAIL. An on-site pair (`person-b`'s key equals `person-a`'s) → extra pages → FAIL.
- **label error**: a labelled URL with no `page` row → FAIL, reported as a label error, not as a miss.
- **no face**: a photo whose key maps to 0 faces → exit 2, the path is named.

The fixture minimums (5 + 5) are real, so the same loader and validation run as on the committed set.

**Rationale**: This follows the existing integration-test pattern (constitution IV), needs no model weights, and is deterministic.

**Alternatives**: Committing fixture CSVs under `tests/fixtures/` was rejected: the fake keys depend on generated image bytes, so building fixtures in the test keeps the keys and files in one place.

## R10. Validation of the committed set in CI without a database

**Decision**: One function, `read_folder(folder) -> tuple[EvalFolder | None, tuple[str, ...]]`, loads everything and returns the parsed folder (None if any error) and every error found. Each loader and rule is a plain function that returns its error strings; nothing raises for bad data, so collecting errors across files needs no exception handling (constitution VIII forbids exceptions as ordinary control flow). The evaluator and `--check` both call it: any error means print them all and exit 2, so no partial set is ever evaluated (spec FR-010). It validates, with every error carrying file and line:

- table headers and types (R1)
- canonical page URLs: `normalize_page_url(u) == u`, https, host in an enabled site's `page_hosts`
- duplicate (label, page) rows
- present labels with at least one page; absent rows with an empty `page_url`
- per-set minimum counts and hard cases; the `onsite_pair` flag in `methods.csv` agreeing with the set's present look-alike rows
- a `methods.csv` row for every set and a set for every row
- the photo set equal to the attribution set, and every photo used by at least one set
- licence and source URL form (R4)
- run-log header and types
- at least one label set exists (otherwise every per-set rule would pass on an empty folder)

**Rationale**: These are the brief's consistency checks plus the ones the label-set design adds. All are file-only, so CI needs no secrets (SC-003).

## R11. Read-only database access from the host

**Decision**: The documented command reads the `faces_reader` password from the Compose secrets volume at run time and passes it through libpq's `PGPASSWORD`, with a DSN that holds no secret:

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py
```

The `postgres` service already mounts that volume read-only at `/run/db-secrets`, and the password files there are readable.

**Rationale**: It is one command (spec FR-020, SC-004), needs no file or secret beyond what `docker compose up` generated, uses the read-only role (constitution XIV), and never puts the password in a URL, in git, or in the evaluator's output. The shell history keeps the `$(…)` expression, not the value.

**Alternatives**: The README's host example for `face_matcher` uses `postgresql://faces@127.0.0.1:5432/faces`, the admin role without a password. It is not read-only and fails unless the host has its own password setup, so it was not copied; fixing that example is outside this feature. Running the evaluator as a Compose service was rejected: `scripts/` is not in the image, and a new service is extra scope (constitution I).
