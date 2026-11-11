# Quickstart & Validation: Image Indexer Pipeline

This is a run guide and the end-to-end validation for [spec.md](spec.md). Schema and parameter details are in [data-model.md](data-model.md) and [contracts/](contracts/). Commands assume the repository root.

## Prerequisites

- Docker with Compose v2, at least **8 GB of RAM** allocated to Docker (2 face workers × ~1.5 GB, plus Airflow and Postgres), and about 6 GB of disk for images.
- `uv` (for local tests only).
- Outbound HTTPS to edition.cnn.com, people.com and their image hosts.

## 1. Start the stack (SC-001)

```bash
cp .env.example .env        # set POSTGRES_PASSWORD; optionally FACE_WORKERS
```

```bash
docker compose up -d --build
```

**Expected**:
- `docker compose ps` shows `postgres`, `airflow-apiserver`, `airflow-scheduler` and `airflow-dag-processor` as healthy. `airflow-init` exited with code 0.
- The UI at http://localhost:8080 lists `image_indexer` with no import errors.
- `airflow pools list` shows `http_cnn` (1), `http_people` (1) and `face_cpu` (2).

## 2. Run with defaults (US1, SC-002, SC-011)

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer
```

**Expected**:
- The run reaches `success` within 30 minutes with no manual action.
- The `summarize` task log shows a counts table (FR-025) with non-zero `faces stored` for both sites.
- Item failures, if any, appear in that table and in the `download`/`detect` logs with a URL and reason. They never show up as a failed task.

## 3. Check the stored data (SC-003, SC-005, SC-006)

```bash
docker compose exec postgres psql -U "$POSTGRES_USER" -d faces
```

| Check | SQL | Expected |
|---|---|---|
| Single format and size | `SELECT count(*) FROM image WHERE status='indexed' AND (greatest(width,height) > 1024 OR substring(jpeg from 1 for 3) <> '\xffd8ff'::bytea);` | `0` |
| Every face complete and linked | `SELECT count(*) FROM face f LEFT JOIN page_image pi USING (image_id) WHERE pi.page_id IS NULL;` | `0` |
| No faces left on non-indexed images | `SELECT count(*) FROM face f JOIN image i ON i.id=f.image_id WHERE i.status<>'indexed';` | `0` |
| Images shared across pages | `SELECT image_id, count(*) FROM page_image GROUP BY 1 HAVING count(*) > 1 LIMIT 5;` | At least one row (site logos and recirculation thumbnails) |
| Lead marks | `SELECT count(*) FROM page_image WHERE is_lead;` | Close to the number of pages with an `og:image` |
| Same model | `SELECT key, dimensions FROM model_profile;` | One row: `retinaface+Facenet512+cosine+align+base`, `512` |

## 4. Spot-check faces (SC-004)

```bash
docker compose exec airflow-scheduler python -m face_index.export_image --id <id> --boxes -o /tmp/check.jpg
```

Then copy the file out with `docker compose cp`. Repeat for 50 random `indexed` IDs (`ORDER BY random() LIMIT 50`). **Expected**: every drawn box surrounds a face. Without `--boxes`, the file's SHA-256 matches `encode(sha256(jpeg),'hex')` from the database (FR-009a).

## 5. Re-run (US2, SC-007)

Trigger again with defaults.

**Expected**:
- The summary shows `downloaded = 0` for URLs indexed in run 1.
- `faces stored` grows only by faces of genuinely new images.
- `page links added` is non-zero if the home pages changed.
- `SELECT count(*) FROM face` before and after differ only by those new images.

## 6. Parameters and politeness (US3, SC-010)

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer --conf '{"depth": 0}'
```

**Expected**: `pages crawled` is 1 per site.

- Trigger twice in a row: the second run waits as `queued` until the first finishes (FR-022).
- Pacing and robots behavior are proven by automated tests (below), not by watching live traffic.

## 7. Failure isolation (US4, SC-008)

This is covered by automated tests that inject a 500 response, a timeout, a corrupt image and a robots.txt network error into a fake HTTP transport. Each test asserts that the run summary counts the failure, the log names the URL, and every other item is indexed.

## Automated tests

```bash
uv sync --locked
```

```bash
docker compose up -d postgres
```

```bash
uv run pytest
```

Each assignment requirement has at least one outcome test (constitution IV):

| Assignment requirement | Test asserts |
|---|---|
| 1. Scrape images from the site | Crawling fixture HTML yields the expected image refs (src, srcset largest, lazy attributes, `og:image` lead) and respects depth, cap, scope and robots |
| 2. Single format, fixed max size | Every fixture image (PNG RGBA, GIF, WebP, large JPEG, EXIF-rotated) becomes RGB JPEG ≤ 1024 px with no upscaling; SVG and tiny images are skipped with a reason |
| 3. Detect and embed all faces | `FakeEmbedder` returning 3 faces → 3 face rows; DeepFace's whole-image fallback (confidence 0) → 0 faces; 511-d vectors are rejected |
| 4. Store metadata, boxes, embeddings | Loopback pgvector: atomic commit (a failure inside the transaction leaves no faces), URL and fingerprint reuse, no duplicates after two runs, lead mark moved, HNSW cosine query returns the inserted face |
| Rate limits | The pacer, with a fake clock, never allows more than N starts in any 1-second window per `(host, kind)` |
| DAG | Imports cleanly; param schema rejects `depth=-1`; tasks use the expected pools; `max_active_runs == 1` |
