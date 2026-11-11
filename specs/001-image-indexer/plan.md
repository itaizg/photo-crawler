# Implementation Plan: Image Indexer Pipeline

**Branch**: `feat/photo-indexer` | **Date**: 2026-11-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-image-indexer/spec.md`

## Summary

This plan builds an Airflow pipeline, run entirely under Docker Compose. It crawls edition.cnn.com and people.com, respecting robots.txt, a per-run depth, a per-site page cap, and separate per-host page and image rate limits. For each image found, it downloads it, converts it to a JPEG of at most 1024 px, detects every face with DeepFace (RetinaFace detector, Facenet512 embeddings), and stores everything in one Postgres + pgvector database: image metadata, the JPEG bytes, page links (with a lead-image mark), face boxes and 512-d embeddings. Re-runs skip known URLs before downloading and reuse content already indexed under another URL, matched by its content fingerprint.

The DAG has three stages:

1. **Crawl**: one task per site, collecting pages and image references.
2. **Download**: one task per site, which de-duplicates, downloads, converts, and stages each image as `pending`.
3. **Detect**: parallel batches of pending images, which detect faces, embed them, and flip each image to `indexed`.

Airflow pools prevent collisions: one HTTP slot per site and N slots for CPU-heavy face work. Business logic lives in a plain `face_index` package that does not import Airflow, so the future search script can reuse its schema and model profile.

## Technical Context

**Language/Version**: Python 3.13 (constitution). Airflow image `apache/airflow:<3.x>-python3.13`, with the exact patch pinned at implementation ([research R1](research.md#r1-airflow-version-and-python-313)).

**Primary Dependencies**: Apache Airflow 3.x (LocalExecutor, Task SDK `airflow.sdk`), `deepface` + `tf-keras` + `tensorflow` (CPU), `httpx`, `beautifulsoup4` + `lxml`, `pillow`, `psycopg[binary]` 3, `pgvector` (Python adapter), `numpy`. Robots rules use the standard-library `urllib.robotparser`.

**Storage**: One Postgres 17 instance with the pgvector extension (`pgvector/pgvector:pg17` image). It holds two databases: `airflow` (orchestrator metadata) and `faces` (the index). Embeddings are stored as `vector(512)` with an HNSW cosine index, and JPEG bytes as `bytea`.

**Testing**: pytest, offline ([constitution IV](../../.specify/memory/constitution.md)):
- HTTP is faked with `httpx.MockTransport` and HTML fixtures captured from the live sites.
- The face model is replaced by a deterministic `FakeEmbedder` behind the `FaceEmbedder` protocol.
- Storage tests run against a loopback Postgres + pgvector, the Compose service locally and a service container in CI.
- DAG tests are import and task-level only, with no scheduler.

**Target Platform**: Linux containers via Docker Compose. It is developed on macOS (Apple Silicon, arm64) and also runs on x86-64.

**Project Type**: Data pipeline (Airflow DAG plus a core Python library), with one small operator CLI for image export.

**Performance Goals**: A default run (depth 1, cap 50, 1 req/s per page host, 5 req/s per image host) on both sites from an empty index completes in ≤ 30 min on a typical laptop (SC-011). [research R8](research.md#r8-run-time-budget-sc-011) has the budget.

**Constraints**:
- Polite crawling: robots.txt, per-host pacing, an identifying User-Agent.
- One full run at a time.
- Atomic writes: an image is stored together with its faces, or not at all.
- Model weights are baked into the image, so runs never download models.
- Each face worker uses about 1.5 GB of RAM, so the default face concurrency is 2.

**Scale/Scope**:
- Default run: about 100 pages and about 1–2k unique images.
- Full crawl (cap None): thousands of pages, roughly 10^4–10^5 faces.
- Database growth is about 100 KB per stored image.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (see the end of this file).*

| Principle / gate | How this design complies | Status |
|---|---|---|
| **I. Assignment fidelity**: pipeline steps 1–4 | (1) `crawl` + `download` scrape images from the given sites; (2) `normalize` → JPEG ≤ 1024 px; (3) `detect` → DeepFace pre-trained RetinaFace + Facenet512, every face; (4) `store` → image metadata, boxes, embeddings in Postgres. Traced in the [spec's FRs](spec.md#functional-requirements). | ✅ |
| I. Mandated tech | Python 3.13, Apache Airflow DAG, Docker Compose for Airflow + Postgres. No substitutions. | ✅ |
| I. Deliverables | This feature covers the pipeline source, `docker-compose.yml`, and the README sections for running the pipeline, libraries/models, and architecture. The search script and its README sections are feature 002; the shared schema and model profile are built here for it. | ✅ |
| I. No scope creep | Each extra item has a written need: the lead-image mark (clarification Q2, better search precision), the image export CLI (FR-009a, verifiability), and pools (FR-022a). No UI, API, naming, attributes or training. | ✅ |
| I. Recorded decisions | Every open choice is recorded in [research.md](research.md) and cites the spec/assignment line it serves. | ✅ |
| **II. Typed, ty** | All modules annotated. `Protocol` seams: `Fetcher`, `ImageNormalizer`, `FaceEmbedder`, `IndexStore`. `Any` only at HTML, HTTP, DeepFace and DB-row boundaries, narrowed to dataclasses. | ✅ (enforced by gates) |
| **III. ruff** | `[tool.ruff]` in `pyproject.toml`; ruff is the only linter and formatter. | ✅ |
| **IV. Offline pytest** | MockTransport, HTML fixtures, FakeEmbedder, loopback DB. Each assignment requirement has an outcome test (see [quickstart §Tests](quickstart.md#automated-tests)). | ✅ |
| **V. KISS/YAGNI/DRY** | LocalExecutor (no Celery/Redis), one Postgres for both databases, plain SQL schema with idempotent DDL (no migration framework). One `model_profile.py` and one `config.py` hold every shared fact. | ✅ |
| **VI. Structure** | `face_index` has no Airflow import; DAG tasks are thin wrappers. Components depend on protocols, and the search script will import only `face_index.model_profile` and `face_index.store`. | ✅ |
| Quality gates | `pyproject.toml`, `.pre-commit-config.yaml` (ruff, ty, pinned), `.github/workflows/ci.yml` (pre-commit, pytest on 3.13 with a pgvector service, `docker compose config`). These are part of this feature's tasks, since the repo has none yet. | ✅ (planned) |
| Project constraints | Shared model contract (`model_profile` table plus module), failure isolation for each item, secrets only in `.env`, Compose as the single entry point, README outputs taken from a real run. | ✅ |

No violations, so Complexity Tracking is empty.

## Project Structure

### Documentation (this feature)

```text
specs/001-image-indexer/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: decisions R1–R12
├── data-model.md        # Phase 1: tables, states, constraints
├── quickstart.md        # Phase 1: run and validation guide
├── contracts/
│   ├── dag-params.md        # DAG trigger parameters
│   ├── index-schema.sql     # database schema (consumed by the search script)
│   ├── index-read.md        # read contract for the search script
│   └── export-image-cli.md  # operator export command
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
pyproject.toml                 # uv project, ruff + ty + pytest config
uv.lock
docker-compose.yml             # postgres(pgvector), airflow-init, api-server, scheduler, dag-processor
.env.example                   # documented variables; real .env is git-ignored
docker/
├── airflow.Dockerfile         # apache/airflow:<3.x>-python3.13 + project deps + baked model weights
├── postgres-init.sql          # creates `airflow` and `faces` databases, enables `vector` in `faces`
└── warm_models.py             # build-time: loads RetinaFace + Facenet512 once to cache weights

dags/
└── image_indexer.py           # DAG definition only: params, pools, task wiring

src/face_index/                # core library, no Airflow import
├── config.py                  # defaults: sites, depth, cap, rates, MAX_SIDE, MIN_SIDE, JPEG quality, batch size
├── model_profile.py           # detector, model, dims, metric, alignment, normalization, min confidence
├── urls.py                    # page/image URL normalization, site scope
├── http.py                    # Fetcher protocol + httpx implementation, per-(host, kind) pacer, robots cache
├── extract.py                 # HTML → links, image refs (src/srcset/lazy attrs/picture), lead image (og:image)
├── crawl.py                   # breadth-first crawl with depth, page cap, scope, robots
├── normalize.py               # bytes → NormalizedImage (JPEG ≤1024) or SkipReason
├── faces.py                   # FaceEmbedder protocol, DeepFaceEmbedder, result narrowing/filtering
├── store.py                   # IndexStore: schema init, URL lookup, upserts, pending batches, atomic face commit
├── schema.sql                 # single source of DDL (contracts/index-schema.sql mirrors it)
├── pipeline.py                # stage functions called by DAG tasks; return typed summaries
└── export_image.py            # CLI: export stored JPEG by image id or fingerprint

tests/
├── fixtures/                  # captured HTML (cnn, people), robots.txt, sample images (faces, no face, PNG/RGBA, GIF, SVG, tiny)
├── unit/                      # urls, extract, crawl, normalize, faces (narrowing), pacer, robots, pipeline with fakes
├── integration/               # store against loopback pgvector: idempotency, dedup, atomicity, HNSW query
└── dag/                       # DAG import, params schema, pools, task wiring

.pre-commit-config.yaml
.github/workflows/ci.yml
README.md                      # run pipeline, libraries/models, approach and architecture
```

**Structure Decision**: This is a single project with an `src/` layout. `face_index` is the importable core, shared later by `face_matcher.py` (feature 002). `dags/` is mounted into Airflow, and `docker/` holds the build and initialization assets. There is no separate service for the index, since it lives in the Compose Postgres instance.

## Complexity Tracking

No constitution violations to justify.

## Constitution Check (post-design)

Re-evaluated after Phase 1 artifacts:

- **I**: The data model stores every item that assignment step 4 lists (image metadata, face boxes, embeddings), plus the page links that search needs. [index-read.md](contracts/index-read.md) proves the search script can query by vector similarity without further processing (SC-009). ✅
- **II/VI**: The contracts introduce no Airflow dependency in `face_index`. The four protocols are named in the data model and research. ✅
- **IV**: [quickstart.md](quickstart.md) maps each assignment requirement to an automated test, and the end-to-end check runs on Docker Compose as the Quality Gates require. ✅
- **V**: No new services beyond Postgres and Airflow, no migration tool, no message queue. Pools and `max_active_runs` are built into Airflow. ✅

Gate passes. Ready for `/speckit-tasks`.
