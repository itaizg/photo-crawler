# Implementation Plan: Face Search Script

**Branch**: `feat/face-search` | **Date**: 2026-11-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-face-search/spec.md`

## Summary

The search is one command-line module, `face_index/face_matcher.py`, run as `uv run --no-project python -m face_index.face_matcher` in a Compose service that reuses the pipeline's image (same DeepFace, TensorFlow and cached weights). It:

1. Reads a query photo from the shared `queries/` folder.
2. Normalizes it with the pipeline's `PillowNormalizer` (JPEG, ≤ 1024 px).
3. Detects and embeds faces with the pipeline's `DeepFaceEmbedder`. Faces come back sorted left to right, top to bottom, so they are numbered 1, 2, … in that order.
4. For each face, runs one read-only SQL query against the `faces` database. The query keeps only faces under the current model profile, on `indexed` images, linked through page links the page's latest crawl refreshed, with cosine distance strictly below the threshold (default 0.30). Results are grouped by page.
5. Prints page addresses grouped per face, ordered by best distance. With `--details` it also prints distance, a lead mark and the matching image addresses.

`--list-faces` skips the search and writes `<name>.faces.jpg` next to the query, with numbered boxes. The drawing code is shared with the existing image export CLI.

One small pipeline change rides along (FR-018, clarification Q2): after each page, the download step counts that page's links not seen in this crawl, logs the count, and adds it to the run summary. It never deletes them.

Everything the search knows about the model (profile key, threshold) comes from `model_profile.py`, and every limit comes from `config.py`. The search never imports Airflow and opens its database session read-only.

## Technical Context

**Language/Version**: Python 3.13 (constitution), same `face_index` package and `uv.lock` as the pipeline.

**Primary Dependencies**: No new ones. It reuses `deepface` + `tensorflow` (embedding), `pillow` (normalization and drawing), `psycopg` 3 + `pgvector` (query) and `numpy`. Arguments are parsed with the standard-library `argparse`, matching `export_image`.

**Storage**: Reads the existing `faces` Postgres 17 + pgvector database ([index-schema.sql](../001-image-indexer/contracts/index-schema.sql)). No schema change. The session runs with `default_transaction_read_only = on`.

**Testing**: pytest, offline ([constitution IV](../../.specify/memory/constitution.md)):
- Unit tests for the search core and the CLI use `FakeEmbedder` and an in-memory `FakeSearchIndex` behind a new `SearchIndex` protocol.
- Integration tests run the real SQL against loopback Postgres. The SQL ships in the package as `search_query.sql`, since the image excludes `specs/`. A test asserts that it equals the SQL block in [contracts/search-query.md](contracts/search-query.md), as `test_schema.py` does for `schema.sql`.
- A `model`-marked smoke test runs the real model on the optional local fixture photo.
- Accuracy (SC-001 to SC-003) is measured by a separate evaluation script against the live index ([research R8](research.md#r8-measuring-sc-001-to-sc-003)).

**Target Platform**: Linux container via Docker Compose (developed on macOS arm64). It also runs from the host with `uv run`, pointing at the Compose database on 127.0.0.1.

**Project Type**: CLI tool on top of the existing core library.

**Performance Goals**: One search within 30 s of invocation, excluding the first model download, which never happens in the image (SC-006). It is measured on a default depth-1 index of every reachable site, which today is CNN only. Expected cost ([research R7](research.md#r7-performance-budget-sc-006)):
- Container start plus TensorFlow and model load: about 8–12 s.
- Detection on one ≤ 1024 px image: about 1–2 s.
- Exact scan of about 10⁴ faces: under 0.1 s per query face.

**Constraints**:
- Read-only toward the database (FR-013), enforced by the session setting as well as by code.
- Same model profile as the pipeline (FR-003). The script refuses to run if the database has no row for the current profile key.
- Every match must be returned (FR-006), so the query uses an exact scan, not the approximate HNSW index.
- Output files are written only next to the query photo (FR-012).

**Scale/Scope**: The current index has about 9.5k faces on about 4.7k images and 50 pages. A full crawl is designed for up to about 10⁵ faces, where an exact scan stays well under 1 s.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (see the end of this file).*

| Principle / gate | How this design complies | Status |
|---|---|---|
| **I. Assignment fidelity**: search steps | Takes a new image (`image` argument), extracts its face embeddings (`DeepFaceEmbedder`), searches by vector similarity (pgvector cosine `<=>`), and returns the pages containing the same character with high confidence (strict threshold 0.30, grouped by page). Traced to FR-002 through FR-009. | ✅ |
| I. Mandated tech | Python, run by Docker Compose (`face-matcher` service). Airflow is not used by the search, as the assignment's "separate script" requires. | ✅ |
| I. Deliverables | The face matching script is `src/face_index/face_matcher.py`. This feature adds the README sections "How to use the face matching script" and the search part of "Example usage and expected outputs", taken from a real run (FR-017). | ✅ |
| I. No scope creep | The extras each have a recorded need: `--list-faces` and `--face` (brief decision, multi-face queries), `--details` with the lead mark (clarification Q3), outdated-link filtering and its pipeline log line (clarification Q2, FR-018), and the evaluation script (SC-001 to SC-003 need a measurement). No UI, API, naming or attributes. | ✅ |
| I. Recorded decisions | Every open choice is in [research.md](research.md), citing the spec or assignment line it serves. | ✅ |
| **II. Typed, ty** | All new code annotated. A new `SearchIndex` protocol goes in `ports.py`. DB rows are narrowed to the frozen `PageMatch` model inside the adapter. | ✅ |
| **III. ruff** | No config change. | ✅ |
| **IV. Offline pytest** | Fakes for the embedder and index, loopback DB for SQL, no network. The assignment outcome "search returns only pages above the confidence threshold" gets its own integration test, alongside dedupe, profile, visibility and outdated-link tests. | ✅ |
| **V. KISS/YAGNI/DRY** | One module for the CLI and one small module for the search core. It reuses the normalizer, embedder, `db.connect` and `model_profile`. Box drawing moves to one shared helper instead of a second copy. No new image, dependency or config file. The threshold is defined once, in `model_profile.py`. | ✅ |
| **VI. Structure** | The CLI does I/O and exit codes. `search.py` holds the pure logic over protocols. `PostgresSearchIndex` is the only code that touches SQL. No Airflow import (a test asserts it). | ✅ |
| Quality gates | Existing pre-commit and CI cover the new code. The CI `docker compose config` step validates the new service. The constitution's final check (run the script on the real index, with README outputs from that run) is an explicit task. | ✅ |
| Project constraints | Shared model contract (`model_profile.KEY`, profile row check), Compose as the entry point, no secrets (DSN never printed), README results only from real runs. | ✅ |

No violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/002-face-search/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions R1–R9
├── data-model.md        # Phase 1: entities the search reads and produces
├── quickstart.md        # Phase 1: how to validate the feature end to end
├── contracts/
│   ├── face-matcher-cli.md   # command line, output format, exit statuses
│   └── search-query.md       # the SQL the search runs (mirrors src/face_index/search_query.sql)
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks), not created here
```

### Source Code (repository root)

```text
src/face_index/
├── face_matcher.py      # NEW: CLI. Argument parsing, file I/O, printing, exit statuses
├── search.py            # NEW: core. Threshold check, face selection, per-face search, grouping
├── search_store.py      # NEW: PostgresSearchIndex adapter (read-only session)
├── search_query.sql     # NEW: the search SQL; must equal contracts/search-query.md (tested)
├── drawing.py           # NEW: draw numbered boxes on a JPEG (shared with export_image)
├── export_image.py      # CHANGED: uses drawing.py
├── model_profile.py     # CHANGED: adds MATCH_THRESHOLD = 0.30
├── models.py            # CHANGED: adds QueryFace, PageMatch, FaceResult
├── pipeline.py          # CHANGED: download logs and counts outdated links per page; new summary row (FR-018)
├── store.py             # CHANGED: PostgresIndexStore.outdated_links(page_id), read-only count (FR-018)
├── ports.py             # CHANGED: adds the SearchIndex protocol; IndexStore gains outdated_links
└── (normalize.py, faces.py, db.py, config.py reused unchanged)

scripts/
└── evaluate_search.py   # NEW: SC-001 to SC-003 report from a labels file and the live index

queries/                 # NEW: host folder shared with the container. Photos are git-ignored
└── README.md

docker-compose.yml       # CHANGED: `face-matcher` service (profile `tools`)
.gitignore               # CHANGED: queries/* except README.md
README.md                # CHANGED: the face matching script sections

specs/001-image-indexer/contracts/index-read.md   # CHANGED: guarantee 6, link freshness

tests/
├── fakes.py                              # CHANGED: FakeSearchIndex
├── unit/test_search.py                   # NEW
├── unit/test_face_matcher_cli.py         # NEW
├── unit/test_drawing.py                  # NEW
├── unit/test_model_profile.py            # CHANGED: threshold equals DeepFace's (model marker)
├── unit/test_pipeline_failures.py        # CHANGED: outdated-link log line and summary row (FR-018)
├── integration/test_store_index.py       # CHANGED: outdated_links counts one page's outdated links, deletes nothing
├── integration/test_search_store.py      # NEW
├── integration/test_index_read.py        # CHANGED: outdated link case
└── smoke/test_face_matcher_smoke.py      # NEW, marker `model`
```

**Structure Decision**: Single project, extending the existing `face_index` package. The script lives inside the package, so the Compose service runs it from the already-built image with `uv run --no-project python -m face_index.face_matcher`. The assignment's "`face_matcher.py` or similar" is met by the module's file name. A root-level wrapper was rejected as a second entry point with no use ([research R1](research.md#r1-where-the-script-lives-and-how-it-runs)).

## Post-Design Constitution Re-check

Re-checked after writing [data-model.md](data-model.md), [contracts/](contracts/) and [quickstart.md](quickstart.md):

- **I**: Each FR maps to a contract section or test in [quickstart.md](quickstart.md#requirement-coverage). FR-016 (one command) is met by `docker compose run --rm face-matcher queries/<photo>`. ✅
- **II/VI**: The protocol surface is three methods (`profile_id`, `indexed_face_count`, `matching_pages`). Only `search_store.py` imports psycopg. ✅
- **IV**: The packaged SQL and the contract's SQL block are checked equal by a test. Every exit status in the CLI contract has a unit test. ✅
- **V**: No new dependency, image or config file. The drawing helper removes a would-be duplicate. ✅
- Project constraint "run the script against the resulting database" is a task with a documented command, not a claim. ✅

No new violations.

## Complexity Tracking

No constitution violations to justify.
