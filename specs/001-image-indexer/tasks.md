---

description: "Task list for the Image Indexer Pipeline"
---

# Tasks: Image Indexer Pipeline

**Input**: Design documents from `specs/001-image-indexer/`: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Required. Constitution IV says every behavior ships with a test that fails without it, and every assignment requirement has an outcome test. The user also asked for **smoke tests** that run the pipeline quickly on very small data (research R13). Test tasks come first in each phase and must fail before their implementation.

**Organization**: Tasks are grouped by user story, so each story can be implemented and tested on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: The user story the task belongs to (US1–US4)
- Every task names its exact file path(s)

## Conventions for every task

- Python 3.13, everything type-annotated (constitution II). `Any` only at trust boundaries, narrowed to frozen Pydantic models at once.
- `ruff check`, `ruff format` and `ty check` pass after each task.
- `src/face_index/` never imports Airflow. `dags/` only wires tasks to `face_index.pipeline` functions.
- **Ports and adapters** (constitution II, VI): `pipeline.py` and `crawl.py` depend only on the protocols `Fetcher`, `Resolver`, `Pacer`, `ImageNormalizer`, `FaceEmbedder` and `IndexStore`, plus plain dataclasses. They never import `psycopg`, `httpx`, `PIL` or `deepface`. Only the adapters (`PostgresIndexStore`, `HttpFetcher`, `PillowNormalizer`, `DeepFaceEmbedder`, `PostgresPacer`) import drivers. Production adapters are built in one place, `pipeline.build_default_deps(dsn)`.
- Every default and constant lives in `src/face_index/config.py` or `src/face_index/model_profile.py` (constitution V, DRY).
- Unit tests: no network, no model weights. HTTP goes through `httpx.MockTransport`, DNS through `FakeResolver`, faces through `FakeEmbedder`. Integration and smoke tests may use loopback Postgres at `FACE_INDEX_TEST_DSN` (default `postgresql://faces@127.0.0.1:5432/faces_test`, with no password: see T025).
- Research decisions are cited as **R1–R13**. Implement exactly what the cited decision says.

---

## Phase 1: Setup (shared infrastructure)

**Purpose**: Repository skeleton, tooling and quality gates, so every later task lands in the correct structure and is checked automatically.

- [X] T001 Create the project layout from plan.md "Source Code": empty packages and folders `src/face_index/__init__.py`, `dags/`, `docker/`, `scripts/`, `tests/{unit,integration,dag,smoke}/__init__.py`, `tests/fixtures/{html,images,minisite}/`, each fixture folder with a short `README.md` describing what belongs there
- [X] T002 Create `pyproject.toml`:
  - `[project]` with `requires-python = ">=3.13,<3.14"` and runtime dependencies `httpx`, `beautifulsoup4`, `lxml`, `pillow`, `numpy`, `psycopg[binary]>=3.2`, `pgvector`, `deepface`, `tf-keras`, `tensorflow` (CPU)
  - dependency groups: `dev` (`pytest`, `pytest-timeout`, `ruff`, `ty`, `pre-commit`) and `dag` (`apache-airflow`, pinned per R1)
  - `src` layout via the hatchling build backend
  - `[tool.ruff]` selecting `E,F,I,UP,B,SIM,RUF,ANN,PT`, line length 100, target `py313`
  - `[tool.ty]` covering `src`, `dags`, `tests`
  - `[tool.pytest.ini_options]` with `testpaths = ["tests"]`, `addopts = "--strict-markers -ra"`, markers `smoke: full pipeline on tiny offline data` and `model: needs real DeepFace weights`, `timeout = 120`
- [X] T003 Generate `uv.lock` with `uv lock` and check that `uv sync --locked --group dev --group dag` succeeds. If the Python 3.13 + TensorFlow + Airflow combination fails (R1), record the evidence and the 3.12 fallback in `specs/001-image-indexer/research.md` R1 before continuing
- [X] T004 [P] Create `.pre-commit-config.yaml` with pinned revisions:
  - `astral-sh/ruff-pre-commit` hooks `ruff-check --fix` and `ruff-format`
  - a local hook `ty` running `uv run ty check` over the whole project (`pass_filenames: false`)
  - `pre-commit-hooks` `check-yaml`, `end-of-file-fixer`, `trailing-whitespace`, `check-added-large-files --maxkb=500`
- [X] T005 [P] Create `.github/workflows/ci.yml` per R13:
  - triggers: `push` to `main`, `pull_request`, `workflow_dispatch`; `permissions: contents: read`; every action pinned to a commit SHA
  - job `lint`: `astral-sh/setup-uv`, Python 3.13, `uv run pre-commit run --all-files`
  - job `test`: service `pgvector/pgvector:pg17` on 5432 with a health check, service env `POSTGRES_USER=faces`, `POSTGRES_DB=faces_test`, `POSTGRES_HOST_AUTH_METHOD=trust` (no password, T025), job env `FACE_INDEX_TEST_DSN`, `uv sync --locked --group dev --group dag`, `uv run pytest -m "not model"`
  - job `compose-config`: `docker compose config -q`
  - job `model-smoke`: only `push` to `main` or `workflow_dispatch`; cache `~/.deepface` keyed on the locked `deepface` version; `uv run pytest -m model`
  - job `stack-smoke`: only `workflow_dispatch`; `docker compose up -d --build --wait`, `scripts/smoke.sh`, `docker compose down -v` in an `always()` step
- [X] T006 [P] Extend `.gitignore` with `.env`, `.venv/`, `.ruff_cache/`, `.deepface/`, `*.jpg` under `out/`, and `smoke-output/`. Create `.env.example` listing every variable Compose reads, each with its default and a one-line comment (`POSTGRES_USER=faces`, `FACE_WORKERS=2`, `AIRFLOW_UID=50000`). It contains **no password variable and no secret value**: the local database uses no password (T025)

**Checkpoint**: `uv run pre-commit run --all-files` passes on the empty skeleton, and CI is green except for jobs with nothing to test yet.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Shared configuration, schema, store connection, the safe fetcher, pacing, test fakes and the Compose stack. Every user story needs these.

**⚠️ CRITICAL**: No user story work starts until this phase is complete.

### Tests for Foundational (write first, see them fail)

- [X] T007 [P] Unit tests for URL handling in `tests/unit/test_urls.py`:
  - page normalization per R5: lowercase host, `http`→`https`, fragment dropped, tracking parameters (`utm_*`, `fbclid`, `gclid`, …) dropped, remaining parameters sorted and kept, trailing slash stripped except at the root, duplicate slashes collapsed
  - image normalization keeps the query
  - `in_scope(url, site)` accepts only the site's page hosts (`edition.cnn.com`; `people.com`, `www.people.com`)
- [X] T008 [P] Unit tests for pacing in `tests/unit/test_pacing.py`: with a fake clock, `InMemoryPacer` never allows more than N request starts in any 1-second window per `(host, kind)`; page and image budgets on the same host are independent; different hosts don't block each other (R4)
- [X] T009 [P] Unit tests for the fetcher in `tests/unit/test_http.py`, using `httpx.MockTransport` + `FakeResolver`:
  - redirects are followed by hand (≤ 5 hops), and each hop re-checks scope (pages), robots, address safety and the pacer before it is requested
  - **IP pinning**: the request the transport receives has the vetted IP as its URL host, the original name in the `Host` header, and `request.extensions["sni_hostname"]` equal to the original name (for https)
  - a hop to a non-public address (127.0.0.1, 10.0.0.0/8, 169.254.169.254, ::1, 100.64.0.0/10) is refused **before** any request is sent to it
  - only http/https on ports 80/443 are allowed
  - a body over `MAX_DOWNLOAD_BYTES` is aborted, by `Content-Length` and by streaming
  - the User-Agent and `Accept` header are set as R4/R6 specify
  - robots.txt: 2xx obeyed, 4xx allow-all, 5xx or a network error disallows the host for the run; one fetch per host per fetcher
- [X] T010 [P] Integration tests for the schema in `tests/integration/test_schema.py`, on loopback pgvector:
  - `init_schema` is idempotent (runs twice)
  - check constraints reject: half-set dimensions, width or height above 1024, `jpeg` on a `no_faces` row, `skip_reason` on a non-skipped row, `indexed_at` missing on an `indexed` row, `detect_attempts` of 4, `page.depth` of 11
  - the one-lead-per-page partial unique index rejects a second lead
  - `model_profile` upsert returns the same id for the same `KEY`
- [X] T011 [P] Integration test for the Postgres pacer in `tests/integration/test_pg_pacer.py`. It calls `PostgresPacer.reserve()` only and **never sleeps** (constitution IV: no wall-clock dependence): two threads reserving 10 slots each for the same `(host, image)` at rate 5 receive 20 distinct slots, and after sorting, consecutive slots differ by exactly 0.2 s. The test asserts on the returned timestamps, which the SQL derives arithmetically, not on elapsed time. A second case: `wait()` with an injected recording `sleep` passes the computed delay to `sleep` and doesn't call `time.sleep`

### Implementation for Foundational

- [X] T012 [P] Implement `src/face_index/config.py`, holding every default as a typed constant:
  - `SITES` (frozen dataclass `Site(name, seed_url, page_hosts)` for `cnn` and `people`, R5)
  - `DEFAULT_DEPTH=1`, `MAX_DEPTH=10`, `DEFAULT_PAGE_CAP=50`, `DEFAULT_PAGE_RATE=1.0`, `DEFAULT_IMAGE_RATE=5.0`
  - `MAX_SIDE=1024`, `MIN_SIDE=64`, `JPEG_QUALITY=90`, `MAX_DOWNLOAD_BYTES=20*1024*1024`, `MAX_PIXELS=50_000_000`
  - `DETECT_BATCH_SIZE=50`, `MAX_ATTEMPTS=3`, `MAX_REDIRECTS=5`, `CONNECT_TIMEOUT=10`, `READ_TIMEOUT=20`, `MAX_XCOM_BYTES=10*1024*1024`
  - `TRACKING_PARAMS`, `USER_AGENT`, `ROBOTS_TOKEN="photo-crawler"`
  - `database_dsn()`, which reads `FACE_INDEX_DSN` from the environment and never logs it
- [X] T013 [P] Implement `src/face_index/model_profile.py` per R9:
  - constants `DETECTOR="retinaface"`, `MODEL="Facenet512"`, `DIMENSIONS=512`, `DISTANCE="cosine"`, `ALIGN=True`, `NORMALIZATION="base"`, `MIN_FACE_CONFIDENCE=0.9`
  - `installed_versions()`, which reads the `deepface` and `tensorflow` versions with `importlib.metadata`
  - `profile_key()`, building `retinaface+Facenet512+cosine+align+base@deepface<maj.min>/tf<maj.min>`. For DeepFace's 0.0.x versions, use the full version
- [X] T014 [P] Implement `src/face_index/errors.py`: `InfrastructureError(Exception)` (database or model unavailable; must fail the task, R12), `ItemError(Exception)` with `url`, `stage` and `reason` fields, and `FetchRefused(ItemError)` (scope, robots, non-public address, too large)
- [X] T015 [P] Implement `src/face_index/urls.py` (`normalize_page_url`, `normalize_image_url`, `in_scope`) so that T007 passes
- [X] T016 Write `src/face_index/schema.sql` with exactly the DDL in `specs/001-image-indexer/contracts/index-schema.sql`: enums, `model_profile` (with `tensorflow_version`), `page`, `image` (with `detect_attempts`), `image_url`, `page_image`, `face` with the HNSW cosine index, `fetch_attempt`, `host_pace`. Add a test in `tests/integration/test_schema.py` asserting the two files are identical, so they can't drift
- [X] T017 Define the store port and its Postgres adapter, and implement the connection layer:
  - `src/face_index/ports.py`: the typed `IndexStore` **Protocol** (constitution II), with the methods `init_schema() -> int` (returns the model profile id), `upsert_page`, `reset_lead`, `find_image_by_url`, `find_image_by_sha256`, `stage_image`, `add_url_and_link`, `claim_pending`, `commit_faces`, `pending_batches`, `is_abandoned`, `record_failure`, `record_success`, `record_detect_failure` and `abandoned_pending`. Each takes and returns only plain values and dataclasses from `src/face_index/models.py` (`PageRef`, `StagedImage`, `ImageRecord`, `DetectedFace`, …), never a connection or a row. Define the other ports there too: `ImageNormalizer`, plus re-exports of `Fetcher`, `Resolver`, `Pacer` and `FaceEmbedder` from their modules, so the pipeline imports every port from one place
  - `src/face_index/store.py`: `PostgresIndexStore(dsn)`, which owns its `psycopg` connection with pgvector registered. Its `init_schema()` executes `schema.sql`, then upserts the `model_profile` row. Every public method runs inside a private `_infra()` context manager that turns `psycopg.OperationalError`/`InterfaceError` into `InfrastructureError`, so no driver exception leaves the adapter

  In this task, implement only `init_schema` and the constructor; T040, T050 and T051 fill in the other methods. Depends on T012–T014 and T016; makes the `init_schema` parts of T010 pass, and adds a T010 case: calling a method after the server connection is closed raises `InfrastructureError`.
- [X] T018 Implement `src/face_index/pacing.py` per R4:
  - `Pacer` protocol `wait(host: str, kind: Literal["page","image"]) -> None`
  - `InMemoryPacer(rates, clock, sleep)`
  - `PostgresPacer(conn_factory, rates, sleep=time.sleep, now=…)`, with `reserve(host, kind) -> datetime` (the `host_pace` upsert statement from R4, returning the slot) and `wait()` = `reserve()` followed by `sleep(max(0, slot - now()))`, with `sleep` and `now` injected so tests never sleep

  Makes T008 and T011 pass.
- [X] T019 Implement `src/face_index/http.py` per R4/R6/R7 step 0:
  - `Resolver` protocol (default `socket.getaddrinfo`)
  - an address check using `ipaddress.ip_address(a).is_global` on **every** resolved address
  - `Fetcher` protocol with `get_page(url) -> PageResponse` and `get_image(url) -> ImageResponse`
  - `HttpFetcher`, built on an injectable `httpx.Client` transport: manual redirects, robots cache via `urllib.robotparser`, pacer call before each hop, streamed body with a size cap, and the connection pinned to the vetted IP. **How to pin**: resolve once through `Resolver`, check every address, then build the request with the URL host replaced by the first vetted IP (IPv6 in brackets), `headers={"Host": original_host}` and `extensions={"sni_hostname": original_host}` for https. httpx then verifies the certificate against the original name while connecting to the IP, so DNS can't change between check and connect. Robots and pacer keys always use the original host name
  - raises `FetchRefused`/`ItemError` for item problems and `InfrastructureError` never

  Makes T009 pass.
- [X] T020 [P] Create the test fakes in `tests/fakes.py`:
  - `FakeResolver` (maps hostnames to public test IPs, or to private ones for SSRF tests)
  - `FakeEmbedder` (deterministic faces per image fingerprint, configurable to raise)
  - `MockSite`, which builds an `httpx.MockTransport` from a dict `{url: (status, headers, body)}`, including redirects and robots.txt
  - `FakeClock`
- [X] T021 Create `tests/conftest.py` with fixtures:
  - `pg_extension` (session scope): runs `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public` once per test session
  - `db`: connects to `FACE_INDEX_TEST_DSN`, creates a fresh schema per test and sets `search_path = <test_schema>, public` (`public` must stay on the path, because pgvector's `vector` type and operators live there), runs `init_schema`, and drops the schema after the test. Skips with a clear message if Postgres is unreachable, except in CI, where `CI=true` turns the skip into a failure
  - `fake_clock`, `fake_embedder`, `fake_resolver`, `mock_site`
- [X] T022 [P] Create `docker/postgres-init.sql`, which creates the databases `airflow`, `faces` and `faces_test`, and runs `CREATE EXTENSION vector` in `faces` and `faces_test`
- [X] T023 [P] Create `docker/warm_models.py`, which builds `DeepFace.build_model("Facenet512")` and runs one `DeepFace.represent` on a generated blank image with `detector_backend="retinaface"` and `enforce_detection=False`, so both weight files are cached under `DEEPFACE_HOME=/opt/deepface`
- [X] T024 Create `docker/airflow.Dockerfile`:
  - `FROM apache/airflow:<pinned 3.x>-python3.13` (R1)
  - install the locked runtime dependencies with Airflow's constraints file, then the project (`pip install --no-deps .`)
  - set `ENV DEEPFACE_HOME=/opt/deepface` and run `python docker/warm_models.py` at build time
  - copy `dags/` to `/opt/airflow/dags`
- [X] T025 Create `docker-compose.yml` per R2:
  - service `postgres`: `pgvector/pgvector:pg17`, published **only** on `127.0.0.1:5432`, `POSTGRES_USER=${POSTGRES_USER:-faces}`, `POSTGRES_HOST_AUTH_METHOD=trust` and **no `POSTGRES_PASSWORD`**, so no secret value is committed (constitution, Project Constraints). Add a comment in the file explaining why: local-only, reachable from the Compose network and loopback only, and an operator who exposes it must set a password in `.env`. Also a health check, the init SQL mounted, a named volume
  - a shared Airflow environment block (YAML anchor) with `AIRFLOW__CORE__EXECUTOR=LocalExecutor`, the `airflow` database DSN, `AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS=True`, `AIRFLOW__CORE__LOAD_EXAMPLES=False`, `FACE_INDEX_DSN` for the `faces` database, and `DEEPFACE_HOME`
  - services `airflow-init` (`airflow db migrate`, then `airflow pools set http_cnn 1 …`, `http_people 1`, `face_cpu ${FACE_WORKERS:-2}`), `airflow-apiserver` (8080), `airflow-scheduler` and `airflow-dag-processor`, each depending on a healthy `postgres` and a completed `airflow-init`
  - every variable written as `${VAR:-default}`, so no `.env` is required. None of the defaults is a credential; all DSNs are password-less (`postgresql://faces@postgres:5432/<db>`)
- [X] T026 Verify the stack: `docker compose config -q` passes, `docker compose up -d --build --wait` brings every service up healthy (if the installed Compose version reports the one-shot `airflow-init` exit as a `--wait` failure, switch both this task and the CI `stack-smoke` job to `docker compose up -d --build --wait postgres airflow-apiserver airflow-scheduler airflow-dag-processor`, which still waits for `airflow-init` through `depends_on: service_completed_successfully`, and note the Compose version in research R2), and the pools exist (`docker compose exec airflow-scheduler airflow pools list`). Fix any problems in `docker-compose.yml` or `docker/airflow.Dockerfile`

**Checkpoint**: The tests from T007–T011 pass, and the empty stack starts with one command.

---

## Phase 3: User Story 1 - Build a searchable face index from the target sites (Priority: P1) 🎯 MVP

**Goal**: One trigger crawls both sites and stores converted JPEGs, every face (box, confidence, 512-d embedding), image metadata, and page links with lead marks. The offline smoke test proves the whole pipeline in seconds.

**Independent Test**: `uv run pytest -m smoke` passes (first run). On the stack, `scripts/smoke.sh` passes and quickstart §2–§4 SQL checks return the expected values.

### Tests for User Story 1 (write first, see them fail)

- [X] T027 [P] [US1] Unit tests for extraction in `tests/unit/test_extract.py`, using `tests/fixtures/html/` (captured CNN and People pages, plus synthetic edge cases):
  - `<img src>`; largest `srcset` candidate by `w`, then `x`; `data-src`, `data-srcset`, `data-original`, `data-lazy-src`; `<picture><source srcset>`
  - inline `style` `background-image: url(…)` and the last `image-set()` candidate; `<noscript>` images
  - relative URLs resolved; `data:` URIs dropped
  - lead image from `og:image`, falling back to `twitter:image`; links extracted for the crawl
- [X] T028 [P] [US1] Unit tests for normalization in `tests/unit/test_normalize.py`, on `tests/fixtures/images/`:
  - PNG RGBA → RGB JPEG on white; GIF → first frame; EXIF-rotated → upright; a 3000 px JPEG → longest side 1024; a 500 px image unchanged in size; output starts with `FFD8FF` and has no EXIF
  - SVG → skip `unsupported_format`; 40 px → skip `too_small` (original dimensions recorded); a header claiming 60 MP → skip `too_large` without decoding pixels; truncated bytes → `ItemError` (not a skip)
  - `sha256` taken from the original bytes
- [X] T029 [P] [US1] Unit tests for face narrowing in `tests/unit/test_faces.py`, feeding recorded DeepFace-shaped dicts to the narrowing function (no model):
  - entries with `face_confidence` 0 (whole-image fallback) or below 0.9 are dropped
  - 511-d embeddings raise `ItemError`
  - faces are ordered left to right, then top to bottom, and indexed from 0
  - boxes are clamped to the image bounds
- [X] T030 [P] [US1] Unit tests for the crawl in `tests/unit/test_crawl.py`, with `MockSite` + `FakeResolver` + `InMemoryPacer`: breadth-first order; depth 1 visits seed plus linked in-scope pages only; off-site links are ignored; non-HTML responses are not parsed; robots-disallowed pages are not requested; the result lists `(page_url, depth, image_refs, lead_ref)`
- [X] T031 [P] [US1] Integration tests for the store in `tests/integration/test_store_index.py`:
  - `upsert_page` returns an id
  - `stage_image` inserts a `pending` image with bytes, its `image_url` and its link in one transaction (a forced failure leaves no rows)
  - `commit_faces` with 2 faces makes the image `indexed`, sets `indexed_at` and stores 2 face rows; with 0 faces makes it `no_faces`, sets `jpeg` to NULL, keeps the dimensions and sets `indexed_at`
  - a failure inside `commit_faces` leaves the image `pending` with no faces (FR-021)
  - a box outside the image raises
  - a cosine query `ORDER BY embedding <=> q LIMIT 1` returns the inserted face
  - **read contract (SC-009)**, in a second file `tests/integration/test_index_read.py`: seed 2 pages sharing one `indexed` image with a face, a `pending` image with a face-like vector, and a face under a second `model_profile`. Run the reference query from `contracts/index-read.md` **verbatim** (load it from the contract file, so the doc and the test can't drift). Assert: both pages are returned, once each; `best_distance` is below 0.30 for an embedding equal to the stored one; the `pending` image and the other profile's face are never returned; `is_lead_on_some_match` reflects the lead mark
- [X] T032 [P] [US1] Unit test for the export CLI in `tests/integration/test_export_image.py`: `--id` writes a file whose sha256 equals the stored `jpeg`; `--sha256` works the same; `pending` → exit 3; `no_faces` → exit 3; unknown → exit 2; `--boxes` writes a different, valid JPEG and warns on stderr
- [X] T033 [P] [US1] Build the offline smoke fixture in `tests/fixtures/minisite/`, a `minisite.py` returning a `MockSite` mapping for two sites (`cnn`-like and `people`-like hosts) with 3 pages each, robots.txt, and 8 images:
  - 2 with faces (`FakeEmbedder` returns 1 and 2 faces)
  - 1 photo with no face
  - the same bytes under two URLs on different pages
  - a PNG with alpha, an SVG and a 40 px icon
  - a corrupt JPEG and an image URL returning 500
  - each page declares an `og:image`
- [X] T034 [US1] Offline pipeline smoke test in `tests/smoke/test_pipeline_smoke.py` (marker `smoke`), first run only. It calls `pipeline.init_schema`, `crawl_site` ×2, `download_site` ×2, `plan_batches`, `detect_batch` for each batch and `summarize`, with the minisite, `FakeEmbedder`, `PostgresPacer` with rates of 1000 requests/s for both kinds (so pacing adds no meaningful delay), and the `db` fixture. It asserts:
  - all `indexed` images are JPEG ≤ 1024 px
  - face rows = 3
  - the duplicate image is linked to both pages
  - each page has exactly one lead
  - skip reasons are present for the SVG and the icon
  - the corrupt and 500 URLs appear in the summary failures
  - the summary counts match the table rows
  - (no duration assertion; constitution IV forbids wall-clock dependence. Run it with `pytest --durations=10` to watch speed.)
- [ ] T035 [P] [US1] Real-model smoke test in `tests/smoke/test_deepface_smoke.py` (marker `model`): `DeepFaceEmbedder` on `tests/fixtures/images/face_one.jpg`, `face_two.jpg` and `no_face.jpg` returns 1, 2 and 0 faces, each with a 512-d embedding and confidence ≥ 0.9. Add these three small CC0 fixture images (≤ 100 KB each) with their source and license noted in `tests/fixtures/images/README.md`

### Implementation for User Story 1

- [X] T036 [P] [US1] Implement `src/face_index/extract.py`: `extract_page(html: str, base_url: str) -> PageExtract(links: list[str], image_refs: list[str], lead_ref: str | None)`, per R6, with BeautifulSoup + lxml. Narrow all soup values to `str`. Makes T027 pass
- [X] T037 [P] [US1] Implement `src/face_index/normalize.py`: `PillowNormalizer`, which satisfies the `ImageNormalizer` protocol from `ports.py`, with method `normalize(data: bytes, content_type: str | None) -> NormalizedImage | Skipped`, with dataclasses carrying `sha256`, `jpeg`, `width`, `height`, `original_format`, `original_width` and `original_height`, per R7 steps 1–9 (set `Image.MAX_IMAGE_PIXELS` and turn `DecompressionBombWarning` into an error locally). Raise `ItemError` on decode errors. Makes T028 pass
- [X] T038 [P] [US1] Implement `src/face_index/faces.py`:
  - `Box` and `DetectedFace` dataclasses
  - `FaceEmbedder` protocol `embed(jpeg: bytes) -> list[DetectedFace]`
  - a pure `narrow_results(raw: list[dict[str, Any]], width: int, height: int) -> list[DetectedFace]`
  - `DeepFaceEmbedder`, which decodes the JPEG to a BGR ndarray and calls `DeepFace.represent` with the `model_profile` settings (R9); model-load failures become `InfrastructureError`

  Makes T029 pass.
- [X] T039 [US1] Implement `src/face_index/crawl.py`: `crawl_site(site: Site, fetcher: Fetcher, depth: int, page_cap: int | None) -> CrawlResult`, breadth-first per R5. `CrawlResult` holds the per-page extracts and counters. Makes T030 pass (page_cap is enforced here too, but tested in US3)
- [X] T040 [US1] Implement the indexing methods of `PostgresIndexStore` in `src/face_index/store.py`, matching the `IndexStore` protocol signatures exactly:
  - `upsert_page`
  - `find_image_by_url`, `find_image_by_sha256`
  - `stage_image` (pending or skipped, plus `image_url` and link, one transaction)
  - `add_url_and_link`
  - `claim_pending(batch_ids)` with `FOR UPDATE SKIP LOCKED`
  - `commit_faces(image_id, faces, profile_id)` (indexed or no_faces, with `indexed_at`), in its simplest correct form: plain inserts with the R11 `RETURNING id` + `SELECT` fallback

  Makes T031 pass.
- [X] T041 [US1] Implement `src/face_index/pipeline.py`, with stage functions that each return a frozen summary dataclass of counts:
  - `run_init_schema(deps)`
  - `crawl_site(site_name, params, deps)`, which returns `CrawlResult` serialized to compact JSON for XCom: URLs only, as `[[page_url, depth, lead_ref_or_null, [image_refs…]], …]`, with image refs de-duplicated within a page. Log the payload size. Per research R3 it stays around 1 MB at depth 1 without a cap; above `config.MAX_XCOM_BYTES` (10 MB), fail the task with a message telling the operator to lower the depth or set a page cap
  - `download_site(site_name, crawl_result, params, deps)`, with the per-reference flow of R11 steps 3.1–3.4: the URL-known and fingerprint-known paths add links without downloading or detecting, and this task owns them
  - `plan_batches(deps) -> list[list[int]]`
  - `detect_batch(ids, deps)`
  - `summarize(summaries)`, which logs one table

  `deps` is a frozen `Deps` dataclass of ports (`store: IndexStore`, `fetcher: Fetcher`, `normalizer: ImageNormalizer`, `embedder: FaceEmbedder`, `sites`). `build_default_deps(dsn)` is the only place that constructs the production adapters. Tests pass fakes or a `PostgresIndexStore` on the test database. `pipeline.py` must not import `psycopg`, `httpx`, `PIL` or `deepface`.

  **Basic per-item isolation belongs here** (US1's smoke test needs it): each page and each image reference is processed in its own `try/except ItemError`, logged at WARNING with its URL and counted in `failures[stage]`, and the run continues. Skips are logged at INFO with their URL. US4 (T061) later broadens this to every non-infrastructure exception and fixes the log formats. Makes T034 pass.
- [X] T042 [US1] Implement `dags/image_indexer.py` with the Airflow 3 Task SDK:
  - `@dag(schedule=None, max_active_runs=1, catchup=False, params=…)` using the defaults from `config.py`
  - `init_schema` → per site in a loop over `config.SITES`: `crawl_<site>` and `download_<site>` with `pool=f"http_{site}"`
  - `plan_batches` → `detect_batch.expand(ids=…)` with `pool="face_cpu"` → `summarize`
  - every task a thin wrapper around a `face_index.pipeline` function; `retries=1`, `retry_delay=timedelta(minutes=1)`
- [X] T043 [P] [US1] DAG tests in `tests/dag/test_image_indexer_dag.py`: the DAG imports with no errors through `DagBag(include_examples=False)`; task IDs and dependencies match the R3 diagram; the crawl and download tasks use pools `http_cnn`/`http_people`; detect uses `face_cpu`; `max_active_runs == 1`; `schedule is None`
- [X] T044 [US1] Implement `src/face_index/export_image.py` (`python -m face_index.export_image`) per `contracts/export-image-cli.md`, with argparse, exit codes 0/1/2/3/64, the `--boxes` overlay drawn with `PIL.ImageDraw`, and no DSN in error messages. Makes T032 pass
- [X] T045 [US1] Create `scripts/smoke.sh` (bash, `set -euo pipefail`):
  - runs `docker compose exec -T airflow-scheduler airflow dags test image_indexer --conf '{"depth": 0, "page_cap": 1}'`
  - then runs SQL through `docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -tAc …'`, asserting: ≥ 1 site crawled this run (a site that refuses the crawler, as people.com does with HTTP 402, is a warning, not a failure), ≥ 1 face on this run's pages, 0 stored images on them over 1024 px or not JPEG, 0 faces on them without a 512-d embedding, and 0 faces in the whole table without a page link. "This run's pages" are pages crawled since the run started, with only the links the run saw on them
  - prints a one-line PASS/FAIL per check and exits non-zero on any failure
- [ ] T046 [US1] Run the US1 end-to-end checks:
  - `uv run pytest -m "smoke or not model"` passes
  - `uv run pytest -m model` passes locally
  - with the stack up, `scripts/smoke.sh` passes

  Record the measured smoke durations in `specs/001-image-indexer/research.md` R13.

**Checkpoint**: The MVP works. One trigger builds a valid face index, the offline smoke runs in seconds, and the stack smoke runs in minutes.

---

## Phase 4: User Story 2 - Re-run without redoing work, while still recording new pages (Priority: P2)

**Goal**: Re-runs download nothing already known, reuse content by fingerprint, keep links and metadata fresh, stay correct under races, and try a failing link or detection **at most 3 times**.

**Independent Test**: The offline smoke runs the pipeline 4 times on the minisite. Run 2 downloads 0 known images and adds a new page's links; the failing link's `fetch_attempt.attempts` goes 1→2→3; run 4 doesn't request it and counts it as abandoned (SC-007, SC-012).

### Tests for User Story 2 (write first, see them fail)

- [X] T047 [P] [US2] Integration tests for re-run behavior in `tests/integration/test_store_rerun.py`:
  - a re-sighted page refreshes `last_crawled_at` and keeps `least(depth)`
  - a re-sighted link refreshes `last_seen_at`
  - a lead reset followed by setting a new lead moves the mark; a page whose lead disappeared ends with no lead
  - a second `stage_image` with the same sha256 from another connection (simulated race: two connections, the first holds its transaction open while the second inserts) links to the winner's id and loses no `image_url` or link
- [X] T048 [P] [US2] Unit and integration tests for the attempt cap in `tests/integration/test_attempts.py`:
  - `record_failure(url, kind, error, run_id)` increments once per run id; the same run id twice stays at 1
  - `is_abandoned` is true at 3
  - `record_success` deletes the row
  - `detect` failure increments `image.detect_attempts`; `plan_batches` excludes rows at 3 and reports them as abandoned
  - an `InfrastructureError` does not increment anything
- [X] T049 [US2] Extend `tests/smoke/test_pipeline_smoke.py` with `test_reruns_and_attempt_cap`:
  - runs the pipeline 4 times with distinct run IDs; before run 2, adds a new page to the minisite that shows an already-indexed image
  - run 2: summary `downloaded == 0` for known URLs, unchanged face count, the new page linked to the existing image
  - the 500 URL's `attempts` is 1, 2, 3 after runs 1–3
  - run 4: the `MockSite` request log shows no request to the 500 URL, and the summary has `abandoned >= 1`
  - a `FakeEmbedder` configured to fail on one image makes it abandoned after 3 runs, still `pending`, with no faces

### Implementation for User Story 2

- [X] T050 [US2] Implement the attempt-cap SQL per R11a in `src/face_index/attempts.py`, as private helpers taking a `psycopg` cursor (used only by the adapter): `is_abandoned`, `record_failure` (the upsert from R11a), `record_success`, `record_detect_failure` and `abandoned_pending`. Expose them as the matching `IndexStore` methods on `PostgresIndexStore` in `src/face_index/store.py`. The pipeline calls them only through the protocol. Makes T048 pass
- [X] T051 [US2] Update `src/face_index/store.py` for re-runs per R11: `upsert_page` with `ON CONFLICT DO UPDATE SET last_crawled_at=now(), depth=least(...)`; `reset_lead(page_id)`; links use `ON CONFLICT DO UPDATE SET last_seen_at=now(), is_lead=EXCLUDED.is_lead`; verify that the race-safe `RETURNING id` + `SELECT` fallback already written in T040 holds under the two-connection race of T047. Makes T047 pass
- [X] T052 [US2] Wire the cap and re-run flow into `src/face_index/pipeline.py`:
  - `crawl_site` and `download_site` check `is_abandoned` before each request (INFO log `link abandoned …`, counter `abandoned`), call `record_failure` on `ItemError` and `record_success` on success
  - `download_site` calls `reset_lead` before processing each page's references
  - `plan_batches` selects `pending AND detect_attempts < 3`
  - `detect_batch` calls `record_detect_failure` after the per-image transaction rolls back
  - the Airflow `run_id` reaches these functions through a `run_id: str` parameter

  Makes T049 pass.
- [X] T053 [US2] Pass `run_id` from Airflow in `dags/image_indexer.py` (`get_current_context()["run_id"]` inside each task wrapper) to the pipeline functions, and extend `tests/dag/test_image_indexer_dag.py` to assert the wrappers forward it

**Checkpoint**: Re-runs are cheap, links stay complete, and dead links stop being retried after 3 runs.

---

## Phase 5: User Story 3 - Tune and control a run (Priority: P3)

**Goal**: Depth, page cap, page rate and image rate can be set per run and are validated. Pacing is shared across tasks; robots.txt and scope always hold.

**Independent Test**: The parameter schema rejects bad values; a minisite run with `depth=0` crawls 1 page per site; `page_cap=2` stops at 2 pages; the pacer request log shows no window over the configured rates (SC-010).

### Tests for User Story 3 (write first, see them fail)

- [X] T054 [P] [US3] DAG parameter tests in `tests/dag/test_dag_params.py`: the defaults equal the `config.py` constants; `depth=-1`, `depth=11`, `page_cap=0`, `page_rate=0` and `image_rate=25` are rejected by the `Param` schema; `page_cap=None` is accepted (`contracts/dag-params.md`)
- [X] T055 [P] [US3] Unit tests in `tests/unit/test_crawl_limits.py`: `depth=0` → only seeds; `page_cap=2` → exactly 2 successfully fetched pages per site, with failed pages not counted toward the cap; `page_cap=None` → bounded only by depth; a page and an image request to the same host use separate budgets; with a `FakeClock`, the recorded request times never exceed `page_rate` or `image_rate` in any 1-second window

### Implementation for User Story 3

- [X] T056 [US3] Add the parameters to `dags/image_indexer.py`: `Param` definitions with the JSON schemas from `contracts/dag-params.md`, defaults from `config.py`, read in each task wrapper and passed as a typed `RunParams` dataclass (defined in `src/face_index/pipeline.py`) to the pipeline functions. Makes T054 pass
- [X] T057 [US3] Enforce `page_cap` (successful fetches only) and use both rates in `src/face_index/crawl.py` and `src/face_index/pipeline.py`. The production pacer is `PostgresPacer` with `{page: params.page_rate, image: params.image_rate}`. Makes T055 pass
- [X] T058 [US3] Add a minisite smoke case to `tests/smoke/test_pipeline_smoke.py`, `test_params_bound_the_run`: `depth=0` gives 1 crawled page per site, and `page_cap=1` with `depth=1` gives 1 page per site

**Checkpoint**: The operator controls the run's size and pace, and every limit is enforced and tested.

---

## Phase 6: User Story 4 - Diagnose failures without losing the run (Priority: P3)

**Goal**: Item failures never fail the run and are logged with URL and reason; skips are logged too; infrastructure failures fail the task so Airflow retries it; the summary counts everything.

**Independent Test**: In the minisite smoke, the 500 page, corrupt image, oversized download and private-address redirect each appear in the logs with their URL and reason, and the run completes. A store raising a connection error fails the task.

### Tests for User Story 4 (write first, see them fail)

- [X] T059 [P] [US4] Unit tests in `tests/unit/test_pipeline_failures.py`, using `caplog`:
  - a page timeout, an image 500, a corrupt image, a download over 20 MB and a redirect to `10.0.0.5` each produce one WARNING `… failed url=<url> stage=<stage> reason=<reason>` and increment `failures[stage]`, while every other item is processed
  - an SVG and a tiny image produce one INFO `image skipped url=<url> reason=<reason>`
  - a fake `IndexStore` raising `InfrastructureError` mid-download makes `download_site` re-raise it, with no failure counted and no `record_failure` call. The driver-to-`InfrastructureError` translation itself is tested against the real adapter in T010
  - a `DeepFaceEmbedder` model-load failure raises `InfrastructureError`
- [X] T060 [P] [US4] Unit test in `tests/unit/test_summary.py`: `summarize` merges per-site and per-batch summaries and logs one table containing pages crawled per site, images found, skipped by reason, newly indexed, reused by URL, reused by fingerprint, faces stored, failures by stage and abandoned (FR-025). Counts add up across inputs

### Implementation for User Story 4

- [X] T061 [US4] Complete failure isolation in `src/face_index/pipeline.py` per R12, building on the basic `ItemError` handling from T041: broaden each per-item handler to `except InfrastructureError: raise / except Exception`, so unexpected item errors are counted rather than crashing the task, and add one logging helper with the fixed message formats from T059. Store calls need no wrapping here, since `PostgresIndexStore` already raises only `InfrastructureError` (T017). Makes T059 pass
- [X] T062 [US4] Complete `summarize` in `src/face_index/pipeline.py` with the full counter set and a fixed-width table in the log. Makes T060 pass
- [X] T063 [US4] (Done in `tests/unit/test_pipeline_failures.py`, which injects all three cases into the download stage directly, rather than in the minisite.) Extend the minisite in `tests/fixtures/minisite/minisite.py` with a page that times out, an image larger than `MAX_DOWNLOAD_BYTES` (declared by `Content-Length`) and an image URL redirecting to `http://10.0.0.5/x.jpg`. Extend `tests/smoke/test_pipeline_smoke.py` to assert that each appears in the summary failures and the log, and that the run still indexes every healthy image

**Checkpoint**: All four user stories work. Failures are visible and isolated, and infrastructure problems are retried.

---

## Phase 7: Polish & cross-cutting concerns

**Purpose**: Documentation, the full-stack run the constitution requires, and final gates.

- [X] T064 [P] Write `README.md` sections (the assignment deliverables):
  - **How to run the pipeline**: one command, trigger with and without `--conf`, where to read the summary, resetting abandoned links with the SQL from R11a, re-running a task "with downstream"
  - **Libraries/models used**: Airflow 3, DeepFace RetinaFace + Facenet512, cosine distance, pgvector HNSW, Pillow, httpx, BeautifulSoup
  - **Approach and architecture decisions**: a short summary of R2–R13 with links to `specs/001-image-indexer/research.md`, including the storage decision and why there is no Redis
  - **Tests and smoke runs**: the three tiers from quickstart.md
- [ ] T065 (Partly done 2026-11-11: both runs, the machine and the quickstart §3 results are in the README and research R8. Open: the first run took 72 min, and the R8 lever, a smaller default `page_cap` or a changed SC-011 target, is waiting for the user's decision. The run was CNN-only and not on the reference machine.) Run a full default run on the stack (`airflow dags trigger image_indexer`, then wait for success), then a second default run. Record the machine (CPU cores, RAM, Docker memory), wall-clock times, the summary tables and the quickstart §3 SQL results in `README.md` "Example usage and expected outputs" (real outputs only; constitution result integrity). If the first run exceeds 30 min (SC-011), apply the R8 levers and record that
- [X] T066 [P] (Done 2026-11-11: 50/50 boxes on faces, some of them depictions or tiny background faces; findings in TODO.md.) Spot-check 50 random `indexed` images with `python -m face_index.export_image --boxes` (quickstart §4, SC-004). Record the count of correct boxes in `README.md`, and open a TODO in `TODO.md` for any systematic false detections
- [X] T067 [P] Remove any duplicated constants found by `rg -n "1024|Facenet512|retinaface|0\.9\b|cosine" src dags` outside `config.py`/`model_profile.py`, and confirm `rg -n "^from airflow|^import airflow" src` returns nothing, and `rg -n "psycopg|httpx|from PIL|import PIL|deepface" src/face_index/pipeline.py src/face_index/crawl.py` returns nothing (constitution V, VI)
- [ ] T068 Run every gate: `uv run pre-commit run --all-files`, `uv run pytest -m "not model"`, `uv run pytest -m model`, `docker compose config -q`, `scripts/smoke.sh`. Push and confirm all CI jobs pass, including a manual `workflow_dispatch` of `model-smoke` and `stack-smoke`. The constitution's last done-gate (run the face matching script against the resulting database) can't run until feature 002 exists. Record it in `README.md` and `TODO.md` as pending feature 002, so this feature is "done" except for that gate
- [ ] T069 Walk through `specs/001-image-indexer/quickstart.md` §1–§8 on a fresh clone (`git clone` into a temp directory, then `docker compose up -d --build`), and fix any step that doesn't match reality in the quickstart or README

---

## Dependencies & execution order

### Phase dependencies

- **Setup (Phase 1)**: none.
- **Foundational (Phase 2)**: depends on Setup and blocks all stories. T016 must come before T017; T017 before T018 (the Postgres pacer); T012–T015 before T019; T022–T024 before T025, and T025 before T026.
- **US1 (Phase 3)**: depends on Foundational. It is the MVP.
- **US2 (Phase 4)**: depends on US1's store and pipeline (T040, T041). Its re-run logic extends those modules.
- **US3 (Phase 5)**: depends on US1's DAG and crawl (T039, T042). It is independent of US2, so it can run in parallel with it if T052 and T057 are merged carefully (both touch `pipeline.py`).
- **US4 (Phase 6)**: depends on US1's pipeline (T041). It is independent of US2 and US3 apart from shared edits to `pipeline.py`.
- **Polish (Phase 7)**: depends on all stories.

### Within each story

Tests first and failing → pure modules (`extract`, `normalize`, `faces`) → store → pipeline → DAG → smoke → end-to-end check.

### Files touched by several stories (do these in sequence)

`src/face_index/pipeline.py` (T041, T052, T057, T061, T062), `src/face_index/store.py` (T017, T040, T051), `dags/image_indexer.py` (T042, T053, T056), `tests/smoke/test_pipeline_smoke.py` (T034, T049, T058, T063).

---

## Parallel examples

```text
# Phase 2: the four test files, then the four independent modules
T007 tests/unit/test_urls.py   | T008 tests/unit/test_pacing.py | T009 tests/unit/test_http.py | T010 tests/integration/test_schema.py
T012 config.py | T013 model_profile.py | T014 errors.py | T015 urls.py | T020 tests/fakes.py | T022 postgres-init.sql | T023 warm_models.py

# US1: tests in parallel, then the three pure modules in parallel
T027 test_extract | T028 test_normalize | T029 test_faces | T030 test_crawl | T031 test_store_index | T032 test_export_image | T033 minisite | T035 deepface smoke
T036 extract.py | T037 normalize.py | T038 faces.py

# After US1: US3 and US4 tests in parallel
T054 test_dag_params | T055 test_crawl_limits | T059 test_pipeline_failures | T060 test_summary
```

---

## Implementation strategy

### MVP first (User Story 1)

1. Phase 1 → Phase 2. Stop when the T007–T011 tests pass and the empty stack starts.
2. Phase 3 (US1). Stop and validate: the offline smoke passes, the model smoke passes, and `scripts/smoke.sh` passes on the stack.
3. This already meets every assignment pipeline requirement (scrape, normalize, detect and embed, store).

### Incremental delivery

1. + US2 → re-runs are cheap and link failures are capped at 3 (smoke run 4 times).
2. + US3 → per-run controls are validated and enforced.
3. + US4 → failure diagnostics are complete.
4. Polish → README with real outputs, a full default run, every gate green.

### Smoke tests as the fast loop

While implementing, run `uv run pytest -m smoke` (< 15 s) after every pipeline change. Run `scripts/smoke.sh` (minutes) before each push that touches `dags/`, `docker/` or `docker-compose.yml`.

---

## Notes

- [P] tasks touch different files and depend on no unfinished task.
- Commit after each task or small group, with Conventional Commit messages.
- Never weaken a safety check (SSRF, robots, size limits) to make a test pass. Inject a fake `Resolver` or transport instead.
- If a decision in research.md turns out wrong during implementation, update research.md in the same commit as the code change.
