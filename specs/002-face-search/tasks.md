---

description: "Task list for the Face Search Script"
---

# Tasks: Face Search Script

**Input**: Design documents from `specs/002-face-search/`: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required. Constitution IV says every behavior ships with a test that fails without it, and the assignment requirement "search returns only pages above the confidence threshold" needs an outcome test. Test tasks come first in each phase and must fail before their implementation.

**Organization**: Tasks are grouped by user story, so each story can be implemented and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: The user story the task belongs to (US1–US3). FR-018 is a pipeline change with no story, so it sits in Polish.
- Every task names its exact file path(s)

## Conventions for every task

- Python 3.13, everything type-annotated (constitution II). `Any` only for DB rows and DeepFace output, narrowed to frozen Pydantic models at once.
- `ruff check`, `ruff format --check` and `ty check` pass after each task.
- **No Airflow**: nothing under `src/face_index/` imports `airflow`.
- **Ports and adapters**: `search.py` depends only on the protocols `SearchIndex`, `FaceEmbedder` and `ImageNormalizer`, plus frozen Pydantic models from `models.py`. It never imports `psycopg`, `PIL` or `deepface`. Only `search_store.py` touches SQL, and `face_matcher.py` does argument parsing, file I/O, printing and exit statuses.
- **One place for constants** (DRY): the threshold is `model_profile.MATCH_THRESHOLD`, and limits come from `config.py`. Reuse `PillowNormalizer`, `DeepFaceEmbedder` and `db.connect` unchanged.
- **Test types**:
  - Unit tests: no database, no model weights. Faces come from `FakeEmbedder`, the index from `FakeSearchIndex`.
  - Integration tests: loopback Postgres at `FACE_INDEX_TEST_DSN`, via the existing `store`, `sql` and `db_dsn` fixtures in `tests/conftest.py`.
  - Model tests: marker `model`.
- **References**: research decisions are cited as **R1–R9**, and the exact output formats and exit statuses are in [contracts/face-matcher-cli.md](contracts/face-matcher-cli.md). Implement exactly what they say.
- **Branch**: work on `feat/face-search`, created from `feat/indexer-4-spec-status`.

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: The Compose entry point, the shared query folder, and the threshold constant.

- [X] T001 [P] Create `queries/README.md`. It explains that query photos go here, that this folder is mounted into the `face-matcher` container at `/work/queries`, and that annotated copies `<name>.faces.jpg` are written next to the photo. Add `queries/*` and `!queries/README.md` to `.gitignore`.
- [X] T002 [P] Add the `face-matcher` service to `docker-compose.yml` per R1:
  - It reuses the `x-airflow-common` `build` and `image` (`photo-crawler-airflow:latest`) and has `profiles: [tools]`.
  - `entrypoint: ["uv", "run", "--no-project", "python", "-m", "face_index.face_matcher"]`, `working_dir: /work`, volume `./queries:/work/queries`, `user: "${AIRFLOW_UID:-50000}:0"`.
  - Environment: only `FACE_INDEX_DSN`, `DEEPFACE_HOME` and `TF_CPP_MIN_LOG_LEVEL`.
  - `depends_on: postgres: condition: service_healthy`.
  - Add a header comment with the one-line usage `docker compose run --rm face-matcher queries/<photo>`.
  - Check: `docker compose config` passes, and `docker compose up -d` does not start the service.
- [X] T003 [P] Add `MATCH_THRESHOLD = 0.30` to `src/face_index/model_profile.py`, with a comment citing DeepFace's `find_threshold("Facenet512", "cosine")` and R2.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: The data types, the protocol, the read-only adapter with its SQL, and the shared drawing helper. Every story needs them.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Tests for Foundational (write first, see them fail)

- [X] T004 [P] Write `tests/integration/test_search_store.py` against loopback Postgres. Seed data with the existing `PostgresIndexStore` (`init_schema`, `upsert_page`, `stage_image`, `link`, `process_pending`) and raw SQL for edge rows, as `tests/integration/test_index_read.py` does. Assert:
  - (a) `search_query.sql` equals the SQL block in `specs/002-face-search/contracts/search-query.md`, skipped when the specs folder is absent;
  - (b) a face at distance 0.299 matches and one at 0.301 does not, and a face at exactly the threshold does not (strict `<`);
  - (c) a face under another `model_profile` id is ignored;
  - (d) a face on a `pending` image is ignored;
  - (e) an image linked to 2 pages returns both pages;
  - (f) a page with 2 matching images is returned once, with `image_urls` of length 2, closest first;
  - (g) an outdated link (`UPDATE page_image SET last_seen_at = page.last_crawled_at - interval '1 hour'`) is ignored, while a page whose `last_crawled_at` was not bumped keeps its links;
  - (h) ordering is by `best_distance` then `url`;
  - (i) `lead` is true only when a matching image has `is_lead`;
  - (j) the image URL chosen is the earliest `first_seen_at`;
  - (k) `profile_id(KEY)` returns the id and `profile_id("missing")` returns None;
  - (l) `indexed_face_count` counts only `indexed` images under that profile;
  - (m) on an empty database with no schema, `profile_id` raises `IndexNotReady`;
  - (n) a write on the adapter's connection raises `psycopg.errors.ReadOnlySqlTransaction`;
  - (o) an unreachable DSN raises `InfrastructureError` whose message does not contain the DSN.
- [X] T005 [P] Add an outdated-link case to `tests/integration/test_index_read.py`: a third page whose only link has `last_seen_at < last_crawled_at` must not be returned by the reference query.
- [X] T006 [P] Write `tests/unit/test_drawing.py`:
  - `draw_boxes(jpeg, [(label, Box)])` returns a decodable JPEG with the input's size, and pixels on a box outline differ from the input.
  - Labels are drawn as given (strings), with an empty list giving a re-encoded copy of the same size.
  - `export_image --boxes` output still labels faces by `face_index`. Cover this with a unit test of `export_image._with_boxes` on an `ExportedImage`.
- [X] T007 [P] Add `test_match_threshold_matches_deepface` (marker `model`) to `tests/unit/test_model_profile.py`. It asserts `MATCH_THRESHOLD == deepface.modules.verification.find_threshold(MODEL, DISTANCE)`.

### Implementation for Foundational

- [X] T008 [P] Add frozen, slotted dataclasses `QueryFace(number: int, box: Box, embedding: tuple[float, ...])`, `PageMatch(url: str, best_distance: float, lead: bool, image_urls: tuple[str, ...])` and `FaceResult(face: QueryFace, pages: tuple[PageMatch, ...])` to `src/face_index/models.py`, per data-model.md.
- [X] T009 [P] Add `class IndexNotReady(Exception)` to `src/face_index/errors.py`, documented as "the schema, the current model profile row, or indexed faces are missing" (R5).
- [X] T010 Add the `SearchIndex` protocol to `src/face_index/ports.py`, with `profile_id(key) -> int | None`, `indexed_face_count(profile_id) -> int` and `matching_pages(embedding: Sequence[float], max_distance: float, profile_id: int) -> list[PageMatch]`. Its docstring says implementations raise only `InfrastructureError` or `IndexNotReady`. Depends on T008 and T009.
- [X] T011 [P] Create `src/face_index/search_query.sql`, the exact SQL block from `contracts/search-query.md`, comments included.
- [X] T012 Implement `PostgresSearchIndex` in `src/face_index/search_store.py`. Depends on T010 and T011.
  - Connect with `db.connect(dsn)`, then `register_vector`, then `SET default_transaction_read_only = on`.
  - Load the SQL with `importlib.resources` (as `store.schema_sql` does) and run it with `%(q)s` as a float32 numpy array.
  - Narrow rows to `PageMatch`.
  - Map `psycopg.errors.UndefinedTable` to `IndexNotReady`, and `OperationalError`/`InterfaceError` to `InfrastructureError("database unavailable: <Type>")`.
  - Provide `close()`. Never call `init_schema` and never write.
  - Make T004 pass.
- [X] T013 [P] Create `src/face_index/drawing.py` with `draw_boxes(jpeg: bytes, boxes: Sequence[tuple[str, Box]]) -> bytes`, moved from `export_image._with_boxes`: a red 2-px outline, the label at the box's top-left, and JPEG at `config.JPEG_QUALITY`. Refactor `src/face_index/export_image.py` to call it with `str(face_index)` labels. Make T006 pass and keep `tests/integration/test_export_image.py` green.
- [X] T014 Add `FakeSearchIndex` to `tests/fakes.py`, after T008 (it builds `PageMatch` values):
  - Constructor `(profiles: Mapping[str, int], face_count: int, pages_by_face: Callable[[Sequence[float], float], list[PageMatch]] | None, raise_on_connect: Exception | None)`.
  - It records every `matching_pages` call as `(embedding, max_distance, profile_id)` in `calls`.
  - Its default `pages_by_face` returns the canned pages whose `best_distance < max_distance`, so threshold behavior is testable without a database.
- [X] T015 Update `specs/001-image-indexer/contracts/index-read.md`: add guarantee 6 (link freshness) with the wording in `contracts/search-query.md` §"Change to the pipeline's read contract", and add `AND pi.last_seen_at >= p.last_crawled_at` to the reference query's `page` join. Make T005 pass.

**Checkpoint**: The adapter returns correct, read-only, de-duplicated page matches for a given embedding.

---

## Phase 3: User Story 1 - Find every page that shows a person (Priority: P1) 🎯 MVP

**Goal**: `docker compose run --rm face-matcher queries/<photo>` with a one-face photo prints every matching page once, closest first. Every failure ends with one clear message and its exit status.

**Independent Test**: The unit tests below, plus quickstart §2 on the live stack.

### Tests for User Story 1 (write first, see them fail)

- [X] T016 [P] [US1] Write `tests/unit/test_search.py` for `search.prepare_query` and `search.search_faces`:
  - `prepare_query` passes the photo through the normalizer, and the embedder receives the normalized JPEG, not the original bytes (FR-003).
  - It returns `QueryFace` numbers 1..n in the embedder's order.
  - A `Skipped` result raises `QueryImageError` with a message per reason: SVG, too small with its W×H and the 64 px minimum, too large. An undecodable file raises `QueryImageError("not a decodable image")`.
  - No faces raises `NoFaceError`.
  - `search_faces` calls `index.matching_pages` once per face with the given threshold and profile id, and returns `FaceResult`s in face order. An empty match list gives `pages == ()`.
- [X] T017 [P] [US1] Write `tests/unit/test_face_matcher_cli.py`, calling `face_matcher.main(argv, index_factory=…, embedder=…, normalizer=…)` with `FakeSearchIndex`, `FakeEmbedder` and photos from `tests/images.py` in `tmp_path`. Capture output with `capsys`. Assert:
  - The default output equals the contract's format exactly: header `# face 1 x=… y=… w=… h=…: N pages`, URLs, and `no matching pages`. Exit 0.
  - Exit 2 for each query-file problem in FR-014 and the contract:
    - a missing file, with the `queries/` hint;
    - an unreadable file (`chmod 000`, skipped when running as root);
    - a non-image file;
    - an SVG;
    - an image under 64 px on its shortest side, with its W×H in the message.
  - Exit 3 for a photo with no face.
  - Exit 1 when `index_factory` raises `InfrastructureError`, and the DSN text is not printed.
  - Exit 4 when the profile is missing or the face count is 0, with the message naming the profile key and "run the image_indexer pipeline first".
  - The index is contacted before the embedder is called, so a down database fails fast.
  - An unexpected exception is not caught (constitution VIII): Python prints its traceback and exits 1, after the index connection is closed.
  - `"airflow" not in sys.modules` after importing `face_index.face_matcher` in a subprocess.
- [X] T018 [P] [US1] Write `tests/smoke/test_face_matcher_smoke.py` (marker `model`, skipped without `tests/fixtures/images/face_one.jpg`):
  - Index `face_one.jpg` into the test database through `PostgresIndexStore` and `DeepFaceEmbedder`, linked to one page.
  - Run `main(["<path to a re-encoded, resized copy of face_one.jpg>"])` with the real normalizer and embedder and `PostgresSearchIndex` on `FACE_INDEX_TEST_DSN`.
  - Assert the page URL is printed and the exit status is 0.

### Implementation for User Story 1

- [X] T019 [US1] Implement `src/face_index/search.py`. Depends on T008 to T010. Make T016 pass.
  - Exceptions `QueryImageError(reason)` and `NoFaceError`.
  - `prepare_query(data: bytes, normalizer: ImageNormalizer, embedder: FaceEmbedder) -> tuple[NormalizedImage, list[QueryFace]]` per R6.
  - `search_faces(faces: Sequence[QueryFace], index: SearchIndex, profile_id: int, threshold: float) -> list[FaceResult]`.
  - No I/O and no driver imports.
- [X] T020 [US1] Implement `src/face_index/face_matcher.py`. Depends on T012 and T019. Make T017 pass.
  - Module docstring with the usage line. An argparse parser with positional `image`, and `_Parser.error` exiting 64 like `export_image`.
  - Exit status constants per the contract.
  - `main(argv=None, *, index_factory=None, embedder=None, normalizer=None) -> int`, which defaults to `PostgresSearchIndex(config.database_dsn())`, `DeepFaceEmbedder()` and `PillowNormalizer()`.
  - Order of steps:
    1. Validate the arguments.
    2. Open the index and check the profile (`profile_id(model_profile.KEY)`, then `indexed_face_count`).
    3. Read the file.
    4. Run `prepare_query`, printing `loading face model…` to stderr.
    5. Run `search_faces`.
    6. Print the groups per the contract.
  - Map every expected error to its status with one `error:` line on stderr, in one place (`_status`).
  - Always close the index.
  - End the module with `if __name__ == "__main__": raise SystemExit(main())`.
- [ ] T021 [US1] Do the constitution's final check for US1. Rebuild and start the full stack (`docker compose up -d --build`), trigger `image_indexer`, and wait for the DAG run to succeed. Then run quickstart §2 on the index that run produced: `docker compose run --rm face-matcher queries/one.jpg`. Fix anything that differs from the contract. Record the real output for the README (used in T038).

**Checkpoint**: User Story 1 works end to end. This is the MVP that meets the assignment's search requirement.

---

## Phase 4: User Story 2 - Trust the results: high confidence only (Priority: P2)

**Goal**: An adjustable, validated threshold, an explicit "no matching pages" for absent people, and `--details` with distance, lead mark and image URLs.

**Independent Test**: The unit tests below, plus quickstart §3 (an absent person gives exit 0 with no pages, `--threshold 0.2` returns a subset, `--threshold 3` gives exit 64).

### Tests for User Story 2 (write first, see them fail)

- [X] T022 [P] [US2] Add threshold tests to `tests/unit/test_search.py`. With `FakeSearchIndex` canned pages at distances 0.10, 0.25 and 0.29:
  - The default threshold returns 3 pages.
  - `threshold=0.2` returns only the 0.10 page, a subset of the default result (acceptance scenario 2.3).
  - `threshold=0.10` returns none (strict).
- [X] T023 [P] [US2] Add CLI tests to `tests/unit/test_face_matcher_cli.py`:
  - `--threshold 0`, `-1`, `2.5` and `abc` exit 64 with a message naming the range (0, 2], before `index_factory` or the embedder is called. `--threshold 2` is accepted.
  - The threshold reaches `FakeSearchIndex.calls`. The default is `MATCH_THRESHOLD`.
  - `--details` output equals the contract exactly: 3-decimal distance, `lead` or 4 spaces, the URL, then indented `    image: <url>` lines.
  - Without `--details`, no distance or image URL appears.
  - An all-empty result prints the `no matching pages` header and exits 0 (FR-009).

### Implementation for User Story 2

- [X] T024 [US2] Add `--threshold` to `src/face_index/face_matcher.py`: a float parsed by a custom type that enforces `0 < T <= 2`. Use argparse `default=None`, so T030 can tell an explicit `--threshold` apart, and resolve `None` to `model_profile.MATCH_THRESHOLD` after parsing. The help text names the default (`default: 0.30`). Make the T023 threshold cases pass.
- [X] T025 [US2] Add `--details` and its formatter to `src/face_index/face_matcher.py`, per contracts/face-matcher-cli.md §"Search, `--details`". Make the remaining T023 cases pass.
- [ ] T026 [US2] Run quickstart §3 on the live stack with `queries/absent.jpg` and the two threshold commands. Record the real absent-person output for the README (used in T038).

**Checkpoint**: User Stories 1 and 2 both work. The confidence controls are verified.

---

## Phase 5: User Story 3 - Search a photo with several people (Priority: P3)

**Goal**: Every face searched and grouped, `--face N` to pick one face, and `--list-faces` to print the boxes and save `<name>.faces.jpg` next to the photo, persistent on the host.

**Independent Test**: The unit tests below, plus quickstart §4 (two groups, `--face 2`, `--list-faces`, and the file present on the host after the container exits).

### Tests for User Story 3 (write first, see them fail)

- [X] T027 [P] [US3] Add tests to `tests/unit/test_search.py`, with `FakeEmbedder` returning 3 faces at x = 4, 24 and 44:
  - The numbers are 1, 2, 3 left to right, and two faces with equal `x` are ordered by `y`.
  - `select_faces(faces, None)` returns all of them, and `select_faces(faces, 2)` returns only face 2.
  - `select_faces(faces, 4)` raises `FaceNumberError` carrying the count 3.
- [X] T028 [P] [US3] Add CLI tests to `tests/unit/test_face_matcher_cli.py`:
  - A 2-face photo prints two groups `# face 1` and `# face 2`, separated by a blank line.
  - `--face 2` prints only face 2. `--face 9` exits 64 with `--face 9, but only 2 faces were found`.
  - `--list-faces` prints the contract's list format, writes `<stem>.faces.jpg` in the photo's folder (`tmp_path`), prints its path as given relative to the working directory (use `monkeypatch.chdir(tmp_path)` and a relative argument), and never calls `matching_pages`.
  - The saved file decodes, and its size equals the normalized query's.
  - A second `--list-faces` overwrites the file (one file, changed contents allowed).
  - `--list-faces` combined with `--face`, `--details` or `--threshold` exits 64.
  - A read-only folder (`chmod 0o555`, skipped when running as root) prints the face list, then exits 5 with `cannot write`.

### Implementation for User Story 3

- [X] T029 [US3] Add `FaceNumberError` and `select_faces(faces: Sequence[QueryFace], number: int | None) -> list[QueryFace]` to `src/face_index/search.py`. Make T027 pass.
- [X] T030 [US3] Add `--face N` (int ≥ 1) and `--list-faces` (mutually exclusive with `--face`, `--details` and an explicit `--threshold`) to `src/face_index/face_matcher.py`. Make T028 pass.
  - Multi-face grouping goes through `select_faces`.
  - `--list-faces` writes `drawing.draw_boxes(normalized.jpeg, [(str(f.number), f.box) …])` to `image_path.with_name(f"{image_path.stem}.faces.jpg")` and prints `# saved <path>`.
  - An `OSError` while writing maps to exit 5, after the list is printed.
- [ ] T031 [US3] Run quickstart §4 on the live stack with `queries/group.jpg`, and time the default search (SC-006, under 30 s). Record the index size (faces, indexed images, pages) with the time, using the query in quickstart §4. Confirm `queries/group.faces.jpg` exists on the host after the container exits and is overwritten on a re-run. Record the real `--list-faces` output for the README (used in T038).

**Checkpoint**: All three user stories work independently.

---

## Phase 6: Polish & cross-cutting concerns

**Purpose**: The accuracy measurement, the pipeline's outdated-link report (FR-018), documentation from real runs, and the final gates.

- [X] T032 [P] Write `tests/unit/test_evaluate_search.py` for `scripts/evaluate_search.py`'s pure functions, using `FakeSearchIndex` and `FakeEmbedder`:
  - It parses a labels TOML with `[[person]]` and `[[absent]]` entries.
  - Per-person recall is computed, with missed pages listed even when recall ≥ 0.80.
  - An extra page fails SC-002, and any page for an absent person fails SC-003.
  - The exit status is 1 when any criterion fails, else 0.
- [X] T033 Implement `scripts/evaluate_search.py` per R8, reusing `search.prepare_query`, `search.search_faces` and `PostgresSearchIndex` with the default threshold. It prints a per-person table (expected, returned, recall, extra, missed URLs) and a summary line `PASS`/`FAIL`. Make T032 pass. Depends on T019 and T012.
- [X] T034 [P] Add `## How to use the face matching script` to `README.md`, per FR-017. Link to `contracts/face-matcher-cli.md` instead of duplicating it.
  - Cover: the `queries/` folder, the one-line command, options and defaults (threshold 0.30, strict), the output format, how faces are numbered, `--list-faces` and where the copy goes, the exit-status table, and the host-side `uv run` alternative.
  - Remove the "arrives with feature 002" line from Known limitations.
  - Add the R4 window (a download task that dies mid-page hides that page's later links until the Airflow retry) and the note that several renditions of one photo can appear under one page in `--details`.
- [X] T035 [P] Update `TODO.md`'s "Pages keep links to images they no longer show" entry: outdated links are now filtered on the search side (feature 002, FR-006a), and reporting them in the Airflow task logs remains open. Link `docs/briefs/stale-page-links.md`.
- [X] T036 [P] Write the FR-018 tests (they must fail first).
  - In `tests/integration/test_store_index.py`: with two pages, `outdated_links(page_id)` counts only that page's links whose `last_seen_at < page.last_crawled_at`, returns 0 right after a fresh `upsert_page` + `link`, and deletes nothing (row count of `page_image` unchanged).
  - In `tests/unit/test_pipeline_failures.py`: extend `MemoryStore` with `outdated_links`, returning a configurable count per page. Assert that `download` logs `outdated links page=<url> count=2` at INFO and returns `links.outdated == 2` when the store reports 2, logs nothing and adds no key when it reports 0, and that `summarize` prints the row `outdated page links (ignored by search)` with the total.
- [X] T037 Implement FR-018 per research R4 §Reporting. Make T036 pass and keep the existing pipeline tests green.
  - Add `outdated_links(self, page_id: int) -> int` to the `IndexStore` protocol in `src/face_index/ports.py`.
  - Implement it in `src/face_index/store.py` as a read-only `SELECT count(*) FROM page_image pi JOIN page p ON p.id = pi.page_id WHERE pi.page_id = %s AND pi.last_seen_at < p.last_crawled_at`.
  - In `src/face_index/pipeline.py`, call it in `download` after a page's references and lead handling. `InfrastructureError` propagates; any other error is logged as a page warning and doesn't stop the run. Add the `("outdated page links (ignored by search)", "links.outdated")` row to `SUMMARY_ROWS`.
- [ ] T038 Add the real outputs recorded in T021, T026 and T031 to `README.md` under "Example usage and expected outputs": a single-face search, an absent person, and `--list-faces`, each with the exact command, date and index size. Constitution "Result integrity": real runs only.
- [ ] T039 Build `queries/eval/labels.toml` per quickstart §7, with at least 5 people on the site and at least 5 absent people. Run `scripts/evaluate_search.py`, and record the per-person recall, extra pages, absent results and the labelling method with its bias (R8) in `README.md`. If SC-001 to SC-003 fail, record the result as is and open a TODO entry. Don't tune the threshold to pass.
- [ ] T040 Final check (constitution, Quality Gates). If anything under `src/`, `dags/` or `docker/`, or `docker-compose.yml`, changed after T021's pipeline run (T037 does), rebuild with `docker compose up -d --build`, trigger `image_indexer` again and wait for success. Confirm the `summarize` log shows the outdated-links row. Re-run quickstart §2 to §4 and replace any README output from T038 that differs. Then run every gate and fix failures:
  - `uv run ruff check`, `uv run ruff format --check` and `uv run ty check`.
  - `uv run pytest` (unit and integration) and `uv run pytest -m model`.
  - `docker compose config`.
  - Then quickstart §5: every edge case and its exit status, plus the SC-007 before/after counts.

---

## Dependencies & execution order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies. T001 to T003 are independent.
- **Foundational (Phase 2)**: needs T003 (T007 checks it). Blocks every story.
- **US1 (Phase 3)**: needs Phase 2.
- **US2 (Phase 4)** and **US3 (Phase 5)**: need Phase 2. Their code extends `face_matcher.py` from T020, so start them after T020, then in either order.
- **Polish (Phase 6)**:
  - T032, T033, T034 and T035 can start after Phase 3.
  - T036 and T037 (FR-018) can start after Phase 2. They touch only pipeline files plus `ports.py`, which T010 also edits, so do T037 after T010.
  - T038 needs T021, T026 and T031. T039 needs T033.
  - T040 comes last, after T037, so its fresh pipeline run includes FR-018.

### Within each story

Tests first and failing → `search.py` → `face_matcher.py` → live-stack check from the quickstart.

### Files touched by several stories (do these in sequence)

- `src/face_index/face_matcher.py`: T020, T024, T025, T030
- `src/face_index/search.py`: T019, T029
- `tests/unit/test_face_matcher_cli.py`: T017, T023, T028
- `tests/unit/test_search.py`: T016, T022, T027
- `README.md`: T034, T038, T039, T040
- `tests/unit/test_pipeline_failures.py` and `src/face_index/pipeline.py`: T036, T037 only (no overlap with the search files)

---

## Parallel examples

```text
# Phase 1: all three
T001 queries/ + .gitignore | T002 docker-compose.yml | T003 model_profile.py

# Phase 2: the four test files, then the independent modules
T004 test_search_store | T005 test_index_read | T006 test_drawing | T007 test_model_profile
T008 models.py | T009 errors.py | T011 search_query.sql | T013 drawing.py + export_image.py | T014 tests/fakes.py | T015 index-read.md

# US1: the three test files together
T016 test_search | T017 test_face_matcher_cli | T018 face_matcher smoke

# After US1: tests for US2 and US3, plus the polish work that doesn't touch face_matcher.py
T022 + T023 (US2 tests) | T032 test_evaluate_search | T034 README usage | T035 TODO.md | T036 FR-018 tests
```

---

## Implementation strategy

### MVP first (User Story 1)

1. Phase 1 → Phase 2. Stop when T004 to T007 pass.
2. Phase 3 (US1). Stop and validate: the unit tests pass, the model smoke passes with the fixture photo, and quickstart §2 works on the stack.
3. This already meets the assignment's search requirement: it takes an image, embeds its faces, searches by similarity, and returns the pages with high confidence.

### Incremental delivery

1. + US2 → confidence controls and `--details`, with the clarification Q3 lead mark.
2. + US3 → group photos, `--face` and `--list-faces`, with the clarification Q4 file rules.
3. Polish → outdated-link report in the pipeline (FR-018), accuracy evaluation (SC-001 to SC-003), README from real runs, a fresh end-to-end run, every gate green.

### Fast loop

While implementing, run `uv run pytest tests/unit` (seconds) after each change, and `tests/integration/test_search_store.py` after any SQL change. Run the live-stack checks (T021, T026, T031) once per story.

---

## Notes

- `[P]` tasks touch different files and depend on no unfinished task.
- Commit after each task or logical group. Stop at any checkpoint to validate a story on its own.
- Out of scope here, tracked in their briefs:
  - query-face size or confidence filters (`multi-face-queries.md`);
  - index-face size filtering (`noisy-index-faces.md`);
  - re-indexing after a model upgrade (`reindex-on-model-upgrade.md`);
  - deleting outdated links in the pipeline (`stale-page-links.md`).
