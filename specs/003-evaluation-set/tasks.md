---

description: "Task list for feature 003: Search Evaluation Set"
---

# Tasks: Search Evaluation Set

**Input**: Design documents from `specs/003-evaluation-set/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: Included. Constitution IV requires every behaviour change to ship with a test that fails without it. Within each story, tests come first and must fail before the implementation.

**Organization**: Grouped by user story (spec.md). US1 is P1; US2 and US3 are both P2; US4 is P3.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: The user story the task belongs to
- Every Python command goes through `uv run` (constitution, Project Constraints)
- **[HUMAN]** in a description marks manual work that an agent can't do: finding licensed photos, labelling pages, running the real model on the labeller's snapshot

---

## Phase 1: Setup

**Purpose**: Dependency and repository plumbing

- [X] T001 Add `"pandas>=3.1,<3.2"` to `[project].dependencies` in `pyproject.toml`, keeping the existing comment style, then run `uv lock`. Verify that the `uv.lock` diff adds no `[[package]]` entry, only the project's dependency reference (research R2).
- [X] T002 [P] Add `!queries/eval/` after the `!queries/README.md` line in `.gitignore`. Verify with `git check-ignore -v queries/eval/sets/x.csv`, which should report not ignored, while `queries/other.jpg` stays ignored.
- [X] T003 [P] Write the header-only files `queries/eval/attribution.csv`, `queries/eval/methods.csv` and `queries/eval/runs.csv`, with the exact headers from `specs/003-evaluation-set/contracts/eval-set-files.md`. Don't create empty `photos/` or `sets/` folders: git doesn't track them, and a placeholder file in `photos/` would fail T026's naming rule. T016 and T017 create them with real files.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The file contract, and the two read-only index queries that every story uses

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Tests (write first, see them fail)

- [X] T004 [P] Write tests for `PostgresSearchIndex.known_pages` and `PostgresSearchIndex.index_size` in `tests/integration/test_search_store.py`, using the existing `seed` fixture:
  - `known_pages` returns exactly the given URLs that have a `page` row, returns an empty set for an empty input, and doesn't fail on a URL containing `'`;
  - `index_size` returns faces (the same number as `indexed_face_count`), images with status `indexed`, and pages.
- [X] T005 [P] Write loader tests in `tests/unit/test_eval_set.py` that build folders in `tmp_path`, with tiny JPEGs from `tests.images.photo`. Assert that `eval_set.read_folder` returns `None` for the folder and an error starting with `<file>:<line>:` (header = line 1) for each case, and that two broken files in one folder produce both errors:
  - a wrong header, and a missing column;
  - a bad `hard_case` enum;
  - a non-https URL, a host not in an enabled site's `page_hosts`, and a non-canonical URL (e.g. a trailing `/` or `utm_` parameter that `normalize_page_url` removes);
  - a duplicate `label,page_url`;
  - an absent row with a page, and a present label with no pages;
  - `look_alike` without `lookalike_of`, and `lookalike_of` not a present label in the set;
  - a label not matching `person-[a-z]+`/`absent-[a-z]+`;
  - a set file name that isn't an ISO date.

  Also assert:
  - a valid folder returns the parsed folder and no errors, with derived `photo` and `role`;
  - a folder with no set file returns an error;
  - `"NA"` and empty cells stay strings, not NaN;
  - the latest set is selected by default, and `--set`-style selection by date works.

### Implementation

- [X] T006 Add a frozen `IndexSize(Record)` model with fields `faces: int`, `images: int` and `pages: int` to `src/face_index/models.py`, next to `PageMatch`.
- [X] T007 Add `known_pages(self, urls: Sequence[str]) -> frozenset[str]` and `index_size(self, profile_id: int) -> IndexSize` to the `SearchIndex` Protocol in `src/face_index/ports.py`, with docstrings that say both are read-only.
- [X] T008 Implement both methods in `PostgresSearchIndex` in `src/face_index/search_store.py`, as parameterized SQL constants next to `_FACE_COUNT`:
  - `SELECT url FROM page WHERE url = ANY(%s)`;
  - one `SELECT` returning the three counts. Faces use the same predicate as `_FACE_COUNT`; images are those with `status = 'indexed'`; pages are all `page` rows.

  Both go through `_guard()`. This makes T004 pass.
- [X] T009 [P] Extend `FakeSearchIndex` in `tests/fakes.py` with constructor arguments `known: Collection[str] | None = None` (None means every URL is known) and `size: IndexSize = IndexSize(faces=10, images=5, pages=3)`, plus the two methods, so the fake still satisfies `SearchIndex` under ty.
- [X] T010 Create `src/face_index/eval_set.py` with a module docstring that points to `contracts/eval-set-files.md`. It contains:
  - **Models** (frozen `Record`s):
    - `Label`, a constrained `str` with `role` and `photo` derived through functions or properties;
    - `HardCase`, a `StrEnum` with `none`, `small_face` and `look_alike`;
    - `SetRow` (`label`, `page_url`, `hard_case`, `lookalike_of`);
    - `LabelSet` (`crawl_date: date`, `rows: tuple[SetRow, ...]`, plus helpers `present() -> dict[Label, tuple[str, ...]]` and `absent() -> tuple[Label, ...]`).
  - **`_read_csv(path, columns) -> tuple[list[tuple[int, dict[str, str]]], list[str]]`**: `pandas.read_csv(path, dtype="string", keep_default_na=False, na_filter=False)`. It returns rows with their line numbers, or a header error if the header isn't exactly `columns` (research R1).
  - **`_parse_row(model, row, path, line) -> M | str`**: calls `model_validate`; the one boundary `try` turns a `ValidationError` into the string `f"{path}:{line}: …"` (constitution VIII). Bad data never raises out of this module.
  - **`_set_errors(path, rows) -> list[str]`**, the row and set rules from `data-model.md`:
    - canonical URL via `face_index.urls.normalize_page_url`;
    - hosts via `face_index.config.ENABLED_SITES`;
    - duplicates, role rules and look-alike rules.
  - **`EvalFolder`** (frozen: `sets: dict[date, LabelSet]`), and **`read_folder(folder) -> tuple[EvalFolder | None, tuple[str, ...]]`**, which runs every loader and rule, concatenates all error lists (no `try`), requires at least one set, and returns `None` with the errors if there are any (research R10). Later tasks add their files' loaders and rules to it.
  - **`latest_set(folder: EvalFolder) -> date`**.

  This makes T005 pass.

**Checkpoint**: `uv run pytest tests/unit/test_eval_set.py tests/integration/test_search_store.py` and `uv run ty check` pass.

---

## Phase 3: User Story 1 - Measure the search against a real labelled set (Priority: P1) 🎯 MVP

**Goal**: A contributor runs one documented command against their index and gets the per-label table and PASS or FAIL against feature 002's SC-001 to SC-003, with label errors and no-face photos reported (FR-001 to FR-008, FR-016 to FR-020).

**Independent Test**: With the committed set and an indexed site, `uv run python scripts/evaluate_search.py` prints the header, the index size, one row per label and `PASS` or `FAIL` (quickstart §3). On a test index it gives exit 0 or 1.

### Tests for User Story 1 (write first, see them fail)

- [X] T011 [P] [US1] Rewrite `tests/unit/test_evaluate_search.py` from the TOML fixture to label-set folders in `tmp_path`, using `FakeSearchIndex` and `FakeEmbedder`. Assert:
  - recall and missed pages are reported (keep the existing 4-of-5 = 0.80 PASS case);
  - an extra page makes the verdict FAIL;
  - an absent label with any page makes the verdict FAIL;
  - a labelled URL not in `FakeSearchIndex(known=…)` is printed as `label error (page not indexed)`, is excluded from `expected` and `missed`, and makes the verdict FAIL with exit 1;
  - two photos with zero faces, and one file that isn't a decodable image (`tests.images.corrupt_jpeg()`), are all named with their reasons before exit 2;
  - a missing profile gives exit 2;
  - `main(["queries/eval/labels.toml"])` gives exit 64 with the "now CSV label sets" message;
  - `--log` together with `--check` gives exit 64;
  - an unknown `--set` date gives exit 64 listing the available sets;
  - every `matching_pages` call uses `model_profile.MATCH_THRESHOLD`.

### Implementation for User Story 1

- [X] T012 [US1] In `scripts/evaluate_search.py`:
  - **Remove** `tomllib`, `Person`, `Absent`, `Labels` and `parse_labels`.
  - **Rename** `PersonReport` to `LabelReport`, with fields `label`, `expected`, `returned`, `missed`, `extra`, `label_errors` (data-model.md).
  - **Update** `evaluate_person(label, pages, returned, known)` so that unknown pages become `label_errors` and are excluded from `expected` and `missed`. Add `evaluate_absent(label, returned)`.
  - **Change** `passes()` to the verdict in data-model.md. Keep `MIN_RECALL = 0.80`.
- [X] T013 [US1] Rewrite `run(folder: Path, crawl_date: date, index: SearchIndex, embedder: FaceEmbedder, *, log_to: Path | None = None) -> int` in `scripts/evaluate_search.py`, following the order of work in `contracts/evaluate-search-cli.md`:
  - load with `eval_set.read_folder` (print every error and exit 2 if any), pick the set, then read `profile_id` (exit 2 if missing) and `index_size`;
  - call `known_pages` once with all labelled URLs;
  - embed every photo via `prepare_query`, catching `NoFaceError` and `QueryImageError` per photo (the model boundary, constitution VIII), collecting each path with its reason, and exiting 2 if there are any;
  - search the largest face with `search_faces` at `MATCH_THRESHOLD`;
  - print exactly the format in the contract.

  `log_to` is accepted and ignored until T030.
- [X] T014 [US1] Replace `main()` in `scripts/evaluate_search.py` with an `argparse` parser for `FOLDER` (default `queries/eval`), `--set`, `--log` and `--check`, with the exit 64 cases from the contract. It opens `PostgresSearchIndex(config.database_dsn())` with `closing` and `DeepFaceEmbedder()`, and calls `run`, passing `log_to=folder / "runs.csv"` only when `--log` is given. Catch `InfrastructureError` at this boundary to print a DSN-free error and exit 2. Update the module docstring to the new usage. This makes T011 pass.
- [ ] T015 [US1] [HUMAN] Label the first set:
  - Run the indexer at default depth.
  - Take a local snapshot of the database (`docker compose exec postgres pg_dump …` to a file outside the repo).
  - Browse and/or search the indexed pages to find at least 5 people on the site. For each, list every page they appear on. Copy each URL as `normalize_page_url` returns it (`uv run python -c "from face_index.urls import normalize_page_url as n; print(n('<url>'))"`).
  - Choose at least 5 absent people, including at least 1 look-alike of a present person, and mark at least 1 small-face page.
  - Note whether the crawl has an on-site look-alike pair.
- [ ] T016 [US1] [HUMAN] For every label from T015, find one photo with one clear face that is not from the indexed site and not a copy or resized version of an indexed image (spec FR-007; this is checked in review, so the reviewer compares the attribution's source page with the indexed images), licensed PD, CC0, CC BY or CC BY-SA, on Wikimedia Commons, Flickr or Unsplash. Save it as `queries/eval/photos/<label>.jpg`, and add its row (page-ID source URL per research R4, photographer, licence) to `queries/eval/attribution.csv`.
- [ ] T017 [US1] Write `queries/eval/sets/<crawl date>.csv` from T015's labels, and the matching row in `queries/eval/methods.csv` (`found_by`, `labeller`, `snapshot_holder`, `onsite_pair`). Load it with `uv run python -c "from pathlib import Path; from face_index import eval_set as e; f=Path('queries/eval'); print(e.load_set(f, e.latest_set(f)))"` and fix any reported line.
- [ ] T018 [US1] [HUMAN] Run the quickstart §3 command (reader role, password read from the Compose secrets volume) against the labelling-date index, and confirm the output matches the contract's shape (quickstart §3). Record surprises (no-face photos, label errors) in `specs/003-evaluation-set/notes.md` (constitution XX).

**Checkpoint**: US1 is usable. The set exists and evaluates with one command.

---

## Phase 4: User Story 2 - Catch bad labels and a broken evaluator on every pull request (Priority: P2)

**Goal**: A CI job validates every evaluation file and proves, on seeded pgvector with `FakeEmbedder`, that the evaluator PASSes clean data and FAILs planted defects, in under 5 minutes and without secrets (FR-009 to FR-011, FR-021 to FR-024).

**Independent Test**: In a pull request that adds a row with a non-canonical URL, the `eval-set` job fails and names `sets/<date>.csv:<line>`. An unrelated pull request passes the job in under 5 minutes.

### Tests for User Story 2 (write first, see them fail)

- [X] T019 [P] [US2] Add set-level validation tests to `tests/unit/test_eval_set.py`. `eval_set.read_folder` must return each of these errors with its file and line:
  - fewer than 5 present labels, and fewer than 5 absent labels;
  - no absent look-alike, and no small-face row;
  - `onsite_pair=true` with no present look-alike row, and `onsite_pair=false` with one;
  - a set without a `methods.csv` row, and a `methods.csv` row without a set;
  - a `methods.csv` type error;
  - a `runs.csv` header or type error.

  It must collect every error across all files instead of stopping at the first, and return no errors for a valid folder.
- [X] T020 [P] [US2] Create `tests/integration/test_evaluate_search_e2e.py` with the fixtures from research R9:
  - seed pgvector through `PostgresIndexStore` (`store` fixture) with images from `tests.images.photo(seed=…)`;
  - use a `FakeEmbedder(lookup=…)` that maps each normalized JPEG to an identity key, so the same key gives identical embeddings;
  - build evaluation folders in `tmp_path` with real 5 + 5 minimums;
  - call `evaluate_search.run(folder, date, PostgresSearchIndex(db_dsn), embedder, log_to=None)` and assert the exit status and output:
    - clean → 0 and `PASS`;
    - planted miss (1 of 2 pages) → 1, `FAIL`, and the missed URL printed;
    - absent look-alike sharing `person-a`'s key → 1;
    - on-site pair where `person-b` shares `person-a`'s key → 1 with `extra:` lines;
    - labelled URL with no `page` row → 1 with `label error`;
    - photo with 0 faces → 2 with the path printed;
    - undecodable photo → 2 with the path and reason printed.
  - assert that the database row counts are unchanged after each run.
- [ ] T021 [P] [US2] Add `test_committed_eval_set_is_valid` to `tests/unit/test_eval_set.py`, asserting that `eval_set.read_folder(Path("queries/eval"))` returns no errors for the repository's committed folder. This is US2's final gate: it fails until T017 (the committed set) and T027 (attribution rules) are done, because an empty folder is an error. Once T030 adds the stale-results rule, CI's `--check` step also needs T031's logged run.

### Implementation for User Story 2

- [X] T022 [US2] Implement in `src/face_index/eval_set.py`:
  - `_methods_errors`, `_run_log_errors`, and the set-minimum and methods-coverage rules from `data-model.md`, each returning `list[str]`;
  - add them to `read_folder`, so it returns them with the other errors. No `try` is involved in collecting them.

  This makes T019 pass.
- [X] T023 [US2] Wire `--check` in `scripts/evaluate_search.py`: print each error from `eval_set.read_folder` and exit 2, or print `ok <file>` per file and exit 0. This needs no DSN and no model import. Move the `DeepFaceEmbedder` import inside the non-check path so `--check` doesn't load TensorFlow.
- [X] T024 [US2] Make T020 pass. Adjust only the test's seeding helpers, or the evaluator if T020 exposes a real defect, which must then be described in the commit message.
- [X] T025 [US2] Add an `eval-set` job to `.github/workflows/ci.yml`, after `test`:
  - `runs-on: ubuntu-latest`, `timeout-minutes: 5`;
  - the same `postgres` service block and `FACE_INDEX_TEST_DSN` env as `test`;
  - `actions/checkout` at the same pinned SHA with `fetch-depth: 0`, then `astral-sh/setup-uv` at the same SHA with `enable-cache: true`, then `uv sync --locked`.

  Steps:
  1. `uv run --locked python scripts/evaluate_search.py --check`
  2. If `github.event_name == 'pull_request'`: `if git diff --unified=0 "origin/${{ github.base_ref }}...HEAD" -- queries/eval/runs.csv | grep -E '^-[^-]'; then echo "runs.csv is append-only" >&2; exit 1; fi`
  3. `uv run --locked pytest tests/integration/test_evaluate_search_e2e.py --durations=5`

  It inherits the workflow-level `permissions` and `concurrency` and uses no secrets (research R8).

**Checkpoint**: The `eval-set` job is green on a PR. Locally, deliberately breaking a row makes step 1 fail and name the row.

---

## Phase 5: User Story 3 - Commit photos lawfully and without naming people (Priority: P2)

**Goal**: Every committed photo has exactly one attribution row with an allowed licence and a page-ID source link. CI rejects unattributed photos, orphan attributions, unused photos and named source links (FR-004, FR-012 to FR-015).

**Independent Test**: Adding `queries/eval/photos/person-z.jpg` without an attribution row, or an attribution with `https://commons.wikimedia.org/wiki/File:Some_Name.jpg`, makes `--check` exit 2 and name the path or line.

### Tests for User Story 3 (write first, see them fail)

- [X] T026 [P] [US3] Add attribution tests to `tests/unit/test_eval_set.py`. Each must be returned as an error by `read_folder`:
  - a photo file with no attribution row, and an attribution row with no file;
  - a photo used by no set, and a duplicate `path`;
  - the licences `CC-BY-NC-4.0` and `CC-BY-ND-4.0`, and an empty `author`;
  - these source URLs: a Commons `/wiki/File:…` URL, a Flickr `/photos/<user>/<id>` URL, an Unsplash slug URL, and a `flic.kr` short link;
  - a file in `photos/` that isn't `<label>.jpg`.

  Each of the three accepted forms in research R4 must pass.

### Implementation for User Story 3

- [X] T027 [US3] Implement in `src/face_index/eval_set.py`:
  - an `AttributionRow` model with a `Licence` `StrEnum` (research R4) and `source_url` validated by three full-match regexes as module constants;
  - `load_attribution`;
  - the cross-file rules: the attributed paths must equal the `*` files in `photos/` exactly, and every photo must be used by some set.

  Add them to `read_folder`. This makes T026 pass.
- [X] T028 [US3] Update `queries/README.md`: say that `queries/eval/` is committed (labels, openly licensed photos, attribution), that its file names are neutral labels, and that every other file in `queries/` stays ignored. Link to the README evaluation section.

**Checkpoint**: `uv run python scripts/evaluate_search.py --check` passes on the committed set and fails on each case in T026.

---

## Phase 6: User Story 4 - Report the measured accuracy honestly (Priority: P3)

**Goal**: Real-model runs are logged append-only with comparable conditions, and the README reports the latest results, method, bias, crawl date and re-labelling steps (FR-025 to FR-028).

**Independent Test**: `--log` on a real run appends one row per label to `runs.csv` with every field, and the README section shows the same numbers. A mock run (`run(…, log_to=None)`) leaves `runs.csv` unchanged.

### Tests for User Story 4 (write first, see them fail)

- [ ] T029 [P] [US4] Add run-log tests to `tests/unit/test_evaluate_search.py`:
  - `run(…, log_to=tmp_path / "runs.csv")` on a PASS creates the file with the contract header and one row per label, with every field set and `verdict=PASS`;
  - a second run appends without changing the first run's lines (compare the bytes of the prefix);
  - an exit-2 run writes nothing;
  - `labels_sha256` changes when one photo's bytes change, and is the same for identical inputs;
  - stale-results rule (spec FR-001b): `--check` (via `main([folder, "--check"])`) exits 2 naming the latest set when its most recent logged run has a different `labels_sha256` than the current files, exits 0 once a matching run is appended, and ignores older sets; `run()` still evaluates the corrected set;
  - no row contains `http`.

### Implementation for User Story 4

- [ ] T030 [US4] Implement in `src/face_index/eval_set.py`:
  - a `RunLogRow` model (data-model.md) and `labels_sha256(folder, label_set) -> str`, the first 12 hex characters of SHA-256 over the set file bytes followed by each label's photo bytes in label order;
  - `append_run(path, rows)`, which writes the header if the file is missing, then appends with `newline=""` and never rewrites.

  Add `stale_results_errors(folder: EvalFolder, root: Path) -> list[str]` and call it only from the `--check` path in `scripts/evaluate_search.py`, never from evaluation (research R7).

  In `scripts/evaluate_search.py`, have `run()` build the rows (with `run_at` as UTC now at the start of the database step) and call `append_run` when `log_to` is set and the exit is 0 or 1. This makes T029 pass.
- [ ] T031 [US4] [HUMAN] On the labelling-date snapshot, run the quickstart §4 command (`… scripts/evaluate_search.py --set <crawl date> --log`, reader password from the Compose secrets volume) with the real model, and commit the new `queries/eval/runs.csv` rows.
- [ ] T032 [US4] Add a "Search accuracy evaluation" section to `README.md`:
  - the per-label table and verdict copied from T031's output (never hand-filled);
  - the run command, exactly as in quickstart §3 (reader role, password from the Compose secrets volume), the labelling method from `methods.csv`, and the crawl date;
  - the known bias: recall only counts pages the labeller found, rendition checks (FR-007) are done by review, the sample is small and limited to public figures with open-licence photos, and results are tied to one crawl;
  - why a fresh index FAILs with label errors (drift), and the re-labelling steps (quickstart §5);
  - a link to `queries/eval/runs.csv` for history, and how to correct a set or take a photo down (quickstart §5b), including that a takedown also needs a maintainer to remove the photo from git history;
  - whether the set has an on-site look-alike pair;
  - if the evaluation suggests a different threshold, the suggestion only, with a link to `docs/briefs/noisy-index-faces.md`.
- [ ] T033 [P] [US4] Update `specs/002-face-search/quickstart.md` §7: replace the `labels.toml` steps with the label-set folder and command, and link to `specs/003-evaluation-set/quickstart.md`.

**Checkpoint**: The README shows real numbers that match `runs.csv`, and feature 002's quickstart points to the new format.

---

## Phase 7: Polish & cross-cutting

- [ ] T034 Run the full local gate: `uv run pre-commit run --all-files`, `uv run pytest -m "not model"` and `uv run python scripts/evaluate_search.py --check`. Fix only the failures this feature introduced.
- [ ] T035 [P] Check for leftovers: `grep -rn "labels.toml\|tomllib\|parse_labels" scripts src tests docs specs/002-face-search README.md` should return nothing. Also check that no committed file under `queries/eval/` contains a real name, by reviewing `attribution.csv`'s `author` column (photographers only) and the file names.
- [ ] T036 Walk through `specs/003-evaluation-set/quickstart.md` §§1 to 4 on a fresh clone, then tick the spec's success criteria SC-001 to SC-007 in `specs/003-evaluation-set/notes.md` with evidence: command output, the CI run link, the job duration.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: after Setup. Blocks every story.
- **US1 (Phase 3)**: after Foundational. T015 and T016 (human labelling) can start as soon as Setup is done, in parallel with any code task.
- **US2 (Phase 4)**: after Foundational and T012 to T014 (it runs the evaluator). T021 is US2's final gate: it needs T017's committed set and T027's attribution rules to pass.
- **US3 (Phase 5)**: after Foundational. Its rules plug into `read_folder` from T010, so it doesn't wait for US2.
- **US4 (Phase 6)**: after US1 (T013). T031 and T032 need T017 (the committed set).
- **Polish (Phase 7)**: after all stories.

### Within each story

Tests (failing) → models and loaders → evaluator wiring → committed data → documentation.

### Parallel opportunities

- T002 and T003 run alongside T001.
- T004, T005 and T009 run in parallel (different files). T006 → T007 → T008 run in sequence.
- Human tasks T015 and T016 run alongside all of Phase 2 and T011 to T014.
- T019, T020, T021 (US2 tests) and T026 (US3 tests) can be written in parallel.
- T029 runs alongside US2 and US3 once T013 exists. T033 runs any time after T014.

## Parallel Example: User Story 2

```text
Task: "T019 set-level validation tests in tests/unit/test_eval_set.py"
Task: "T020 mock end-to-end tests in tests/integration/test_evaluate_search_e2e.py"
# then, sequentially:
Task: "T022 set-minimum, methods and run-log rules in read_folder (src/face_index/eval_set.py)"
Task: "T023 --check in scripts/evaluate_search.py"
Task: "T025 eval-set job in .github/workflows/ci.yml"
```

## Implementation Strategy

### MVP first (US1)

1. Phases 1 and 2. Meanwhile, the labeller starts T015 and T016.
2. Phase 3. Stop and validate: the committed set evaluates with one command (quickstart §3).

### Incremental delivery

1. MVP (US1): the set exists and can be measured.
2. US2 + US3: CI guards labels, evaluator and licensing. Ship together, since T021 needs both to be green.
3. US4: the logged run and README numbers. This is the deliverable reviewers read.

### Notes

- Commit after each task or logical group, with a message that names the task IDs.
- Tasks marked [HUMAN] can't be completed by an agent. `/speckit-implement` should stop at them and hand off with exact instructions.
- Never hand-fill results: README numbers come only from T031's logged run (Project Constraints, "Result integrity").
