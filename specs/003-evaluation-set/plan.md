# Implementation Plan: Search Evaluation Set

**Branch**: `claude/vibrant-gates-qklwhp` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-evaluation-set/spec.md`

## Summary

This feature turns `scripts/evaluate_search.py` from a tool with nothing to measure into a reproducible accuracy check of the search.

- **The evaluation set** is committed under `queries/eval/`:
  - neutral-named, openly licensed photos, one per label, with `attribution.csv`;
  - one CSV label set per labelling crawl (`sets/<crawl date>.csv`);
  - `methods.csv`, recording how each set was labelled;
  - `runs.csv`, an append-only run log.
- **Loading and validation** move into a new module, `src/face_index/eval_set.py`. It reads every file with pandas as text and validates each row once into frozen Pydantic models, with `file:line` errors. A label's photo path and role are derived from the label, so they can't be inconsistent.
- **The evaluator**:
  - keeps calling `search_faces` at the default threshold;
  - gains `--set`, `--log` and `--check`;
  - reports label errors (labelled pages missing from the index) separately from misses;
  - lists every photo with no detected face;
  - distinguishes PASS (0), FAIL (1) and "cannot evaluate" (2).
- **Two read-only methods on `SearchIndex`**: `known_pages` and `index_size`.
- **A new `eval-set` CI job** in `ci.yml` validates the files, enforces the append-only run log, and runs mock end-to-end tests on seeded pgvector with `FakeEmbedder`. Those tests must show PASS on a clean fixture and FAIL on a planted miss, a planted look-alike (absent and on-site) and a label error.
- **Documentation**: the README gets the real-model results, the labelling method, the bias, the crawl date and the re-labelling steps. Feature 002's quickstart §7 points to the new format.

## Technical Context

**Language/Version**: Python 3.13, the same `face_index` package and `uv.lock`.

**Primary Dependencies**:
- `pandas` 3.1.0 becomes a direct dependency. It is already locked through deepface and pinned by the Airflow constraint, so the lock gains no package (research R2).
- Otherwise the existing stack: `pydantic` v2, `psycopg` 3 + `pgvector`, `deepface` for the real run only, and the standard-library `argparse` and `hashlib`.

**Storage**:
- Committed files under `queries/eval/` ([contracts/eval-set-files.md](contracts/eval-set-files.md)).
- Reads the existing `faces` database through the read-only role. No schema change.

**Testing**:
- pytest, following constitution IV.
- Unit tests for loading and validation, using `tmp_path` CSVs.
- Integration tests on loopback pgvector with `FakeEmbedder` (research R9).
- A unit test that `--check` passes on the committed `queries/eval/`.

**Target Platform**:
- A contributor's host via `uv run`, against the Compose database.
- GitHub Actions `ubuntu-latest` for the mock.

**Project Type**: Data files, a library module and a CLI dev tool inside the existing single project.

**Performance Goals**:
- CI job under 5 minutes (SC-003). Expected under 1 minute after `uv sync`.
- A real run: about 10 photos × one embedding each, plus about 10 queries, which takes seconds after the model loads.

**Constraints**:
- No secrets in CI.
- No real model in CI.
- No names in committed files.
- The database is read only.
- Every Python command goes through `uv run`.

**Scale/Scope**:
- Per set: ≥ 10 labels, tens of labelled pages.
- Sets grow by one per re-labelling.
- The run log grows by about 10 rows per logged run.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Assignment requirement served (I)**: "Returns the pages containing images of the same character with high confidence." Feature 002 operationalized this as SC-001 to SC-003; this feature provides the labelled set and the evidence. The scope extension (label sets accumulate, run log) was approved by the user on 2026-11-16 and is recorded in the spec and brief. It adds no service, UI, threshold or model change.

| Principle | Status | How the design complies |
|---|---|---|
| I Assignment fidelity | Pass | Traces to the search requirement. Extensions are user-approved and recorded. Mandated technology is unchanged. |
| II Typed, ty | Pass | All new code is annotated. The new `SearchIndex` methods are on the `Protocol`. pandas values are narrowed to models at load. |
| III ruff | Pass | No rule changes. |
| IV Tests | Pass | Mock e2e tests must FAIL on planted defects (research R9). Validation tests assert each rejection with its `file:line`. Everything is deterministic: generated images and `FakeEmbedder`. |
| V Simplicity | Pass | Photo path and role are derived, not stored. There is one loader. No threshold option. The run log is a CSV. The ladder was applied: pandas only because the brief mandates it; everything else uses existing code. |
| VI Structure | Pass | File contract in `eval_set.py`, orchestration in the script, data access behind `SearchIndex`. The script never imports Airflow. |
| VII Pythonic | Pass | `pathlib`, comprehensions, `argparse`, `zip(strict=True)`. |
| VIII Exceptions at boundaries | Pass | One `EvalSetError` raised at load. Pydantic validation, not `try` around rows. `NoFaceError` is caught per photo at the model boundary and listed (exit 2). |
| IX Frozen Pydantic | Pass | Every row and report is a `Record`. |
| X Human oversight | Pass | The PR is reviewed by a human. The README numbers come from the labeller's run. |
| XI Delegation | Pass | The threshold and verdict rules are code constants. No LLM involved. |
| XII Observability | Pass | Every label error, no-face photo, miss and extra page is listed. Nothing is skipped silently. |
| XIII Security | Pass | All files are validated at load. SQL is parameterized (`= ANY(%s)`). The DSN is never printed. |
| XIV Zero trust | Pass | The evaluator uses the read-only `faces_reader` role. |
| XV Stateless | Pass | No state besides the committed files. The append-only log write is the one intended output. |
| XVI Documentation | Pass | The data contract is in `contracts/` and the module docstring. The README and quickstart are updated. |
| XVII to XIX | Pass | Spec clarified. Assumptions recorded in research. Success criteria are measurable. |
| XVIII Surgical | Pass, with a note | The evaluator's TOML reader is removed (the brief replaces the format). Its existing functions (`evaluate_person`, `passes`) are extended, not rewritten. The no-profile exit changes from 1 to 2 (research R6), a deliberate change with a test. |
| XX Memory | Pass | Lessons from labelling go into `methods.csv` and the README bias section. |
| Quality gates | Pass | The new job uses pinned SHAs, `contents: read`, `uv sync --locked` and a timeout. It runs on every PR, so it is never a skipped required check (research R8). |
| Project constraints: result integrity | Pass | The README results are copied from a logged real-model run, never hand-filled. |

No violations, so Complexity Tracking is empty.

**Post-design re-check (after Phase 1)**: still passes.
- Deriving the photo path and role from the label removed two validation rules instead of adding them (V).
- The `SearchIndex` additions are reads only (XIV, SC-007 of feature 002).
- One process note: during the specify and clarify steps, spec files were edited with `python3` directly, which breaks the "always `uv run`" project constraint. That only affected document edits, not project code, and later steps use `uv run`.

## Project Structure

### Documentation (this feature)

```text
specs/003-evaluation-set/
├── plan.md              # This file
├── research.md          # Phase 0: R1–R10
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── evaluate-search-cli.md
│   └── eval-set-files.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
queries/eval/                         # NEW, committed (gitignore exception)
├── photos/<label>.jpg
├── attribution.csv
├── methods.csv
├── runs.csv
└── sets/<YYYY-MM-DD>.csv

src/face_index/
├── eval_set.py                       # NEW: models, pandas loading, validation, run-log append
├── models.py                         # + IndexSize record
├── ports.py                          # SearchIndex + known_pages(), index_size()
├── search_store.py                   # PostgresSearchIndex: the two read-only queries
└── urls.py                           # unchanged; normalize_page_url is the canonical-URL check

scripts/evaluate_search.py            # argparse CLI: --set, --log, --check; label errors; exit 0/1/2/64

tests/
├── unit/test_eval_set.py             # NEW: each rejection with file:line; committed set passes --check
├── unit/test_evaluate_search.py      # updated: TOML → label sets, verdict with label errors
├── integration/test_search_store.py  # + known_pages, index_size
└── integration/test_evaluate_search_e2e.py  # NEW: clean / miss / look-alikes / label error / no face

.github/workflows/ci.yml              # + eval-set job
.gitignore                            # + !queries/eval/
pyproject.toml, uv.lock               # + pandas direct dependency
README.md                             # results, method, bias, crawl date, re-labelling
queries/README.md                     # mentions the committed eval folder
specs/002-face-search/quickstart.md   # §7 points to the new format
```

**Structure decision**: the existing single-project layout. Loading and validation go in the package (`eval_set.py`) so the script, the `--check` mode and the tests share one implementation. The script stays the thin entry point that the tests already import.

## Risks and follow-ups

- **Finding licensed photos of people who are on CNN that day** is the slowest step, and it's manual. Public figures on Wikimedia Commons are the likeliest source. If fewer than 5 people qualify, the set can't meet SC-001; the labeller widens the crawl date choice rather than lowering the minimum.
- **An on-site look-alike pair may not exist** in a given crawl. That is allowed (`onsite_pair=false`) and recorded.
- **Drift** makes contributor runs FAIL with label errors after the labelling date. This is by design (clarification 1), and it's documented in the README and quickstart §3.

## Complexity Tracking

No constitution violations to justify.
