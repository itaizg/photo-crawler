# Quickstart & Validation: Image Indexer Pipeline

This is a run guide and the end-to-end validation for [spec.md](spec.md). Schema and parameter details are in [data-model.md](data-model.md) and [contracts/](contracts/). Commands assume the repository root.

## Prerequisites

- Docker with Compose v2, at least **8 GB of RAM** allocated to Docker (2 face workers × ~1.5 GB, plus Airflow and Postgres), and about 6 GB of disk for images.
- `uv` (for local tests only).
- Outbound HTTPS to edition.cnn.com, people.com and their image hosts.

## 1. Start the stack (SC-001)

```bash
docker compose up -d --build
```

No `.env` file is needed. `docker-compose.yml` gives every variable a non-secret default (`${POSTGRES_USER:-faces}`, `${FACE_WORKERS:-2}`, …). The local database uses **no password** (`POSTGRES_HOST_AUTH_METHOD=trust`) and is published only on `127.0.0.1`, so no credential is committed (constitution, Project Constraints). To override a variable, or to set a password before exposing the database beyond your machine, `cp .env.example .env` and edit it. The real `.env` is git-ignored.

**Expected**:
- `docker compose ps` shows `postgres`, `airflow-apiserver`, `airflow-scheduler` and `airflow-dag-processor` as healthy. `airflow-init` exited with code 0.
- The UI at http://localhost:8080 lists `image_indexer` with no import errors.
- `airflow pools list` shows `http_cnn` (1), `http_people` (1) and `face_cpu` (2).

## 2. Run with defaults (US1, SC-002, SC-011)

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer
```

**Expected**:
- The run reaches `success` with no manual action. The target is 30 minutes (SC-011), which is not yet met: the measured default run took 72 minutes for CNN alone (README, research R8). Use a smaller `page_cap` for a quicker check.
- The `summarize` task log shows a counts table (FR-025) with non-zero `faces stored`. Faces come only from sites that accept the crawler: people.com currently answers HTTP 402, which shows up as a crawl failure in the table. Crawling it needs the site's permission.
- Item failures, if any, appear in that table and in the `download`/`detect` logs with a URL and reason. They never show up as a failed task.

## 3. Check the stored data (SC-003, SC-005, SC-006)

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces'
```

The single quotes matter: `$POSTGRES_USER` is expanded *inside* the container, where Compose has set it, not in your shell, where it is usually unset.

| Check | SQL | Expected |
|---|---|---|
| Single format and size | `SELECT count(*) FROM image WHERE status='indexed' AND (greatest(width,height) > 1024 OR substring(jpeg from 1 for 3) <> '\xffd8ff'::bytea);` | `0` |
| Every face complete and linked | `SELECT count(*) FROM face f LEFT JOIN page_image pi USING (image_id) WHERE pi.page_id IS NULL;` | `0` |
| No faces left on non-indexed images | `SELECT count(*) FROM face f JOIN image i ON i.id=f.image_id WHERE i.status<>'indexed';` | `0` |
| Images shared across pages | `SELECT image_id, count(*) FROM page_image GROUP BY 1 HAVING count(*) > 1 LIMIT 5;` | At least one row (site logos and recirculation thumbnails) |
| Lead marks | `SELECT count(*) FROM page_image WHERE is_lead;` | Close to the number of pages with an `og:image` |
| Same model | `SELECT key, dimensions FROM model_profile;` | One row: `retinaface+Facenet512+cosine+align+base@deepface<maj.min>/tf<maj.min>`, `512` |
| Dimensions kept for analyzed images | `SELECT count(*) FROM image WHERE status IN ('indexed','no_faces') AND (width IS NULL OR indexed_at IS NULL);` | `0` |

## 4. Spot-check faces (SC-004)

```bash
docker compose exec airflow-scheduler uv run --no-project python -m face_index.export_image --id <id> --boxes -o /tmp/check.jpg
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

This is covered by automated tests that inject a 500 response, a timeout, a corrupt image, an oversized download, a redirect to a private address, and a robots.txt network error into a fake HTTP transport. Each test asserts that the run summary counts the failure, the log names the URL, and every other item is indexed. A separate test makes the store raise a connection error and asserts that the task *fails* (so Airflow retries it) instead of counting item failures.

## Smoke runs on very small data (research R13)

| Smoke | Command | Takes | Proves |
|---|---|---|---|
| Offline pipeline | `uv run pytest -m smoke` | < 15 s | The whole pipeline, run twice, on a fixture mini-site: storage, dedup, links, skips, summary, re-run with zero downloads, attempt cap |
| Real model | `uv run pytest -m model` | ~1 min (first run downloads weights) | DeepFace finds 1, 2 and 0 faces on the fixture images; confidence filter; 512-d vectors |
| Full stack | `scripts/smoke.sh` (stack must be up) | ~2–4 min | The real DAG in Compose at depth 0 and page cap 1 on the live home pages, followed by SQL checks |

`scripts/smoke.sh` runs `airflow dags test image_indexer --conf '{"depth": 0, "page_cap": 1}'` inside the scheduler container. Its checks look only at pages crawled by this run and the image links this run saw on them, so rows from earlier runs cannot make them pass. The one exception is the orphan-face check, which covers the whole `face` table: links are never deleted, so an orphan face anywhere is a bug, and one left by an earlier run fails the smoke too. It exits non-zero unless all of these hold:
- the run succeeded;
- at least one site has a page crawled by this run (a site that refuses the crawler is reported as a warning);
- at least 1 face is on this run's pages;
- every stored image on this run's pages is JPEG ≤ 1024 px (skipped and `no_faces` images keep no bytes, so there is nothing to check);
- every face on this run's pages has a 512-d embedding;
- every face in the database is linked to a page.

A site with no page this run is reported as `WARN`, with the failing links, but does not fail the check: a site that refuses the crawler (people.com answers HTTP 402) is not a pipeline fault.

## 8. Attempt cap (SC-012)

This is covered by the offline smoke and integration tests: a link that returns 500 shows `fetch_attempt.attempts` of 1, 2 and 3 over three runs, and the fourth run doesn't request it and counts it under `abandoned`. To see it on the live stack:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "SELECT kind, attempts, count(*) FROM fetch_attempt GROUP BY 1,2;"'
```

## Automated tests

```bash
uv sync --locked
```

```bash
docker compose up -d postgres
```

```bash
uv run pytest -m "not model"     # unit, integration, DAG and offline smoke, as CI runs them
```

Each assignment requirement has at least one outcome test (constitution IV):

| Assignment requirement | Test asserts |
|---|---|
| 1. Scrape images from the site | Crawling fixture HTML yields the expected image refs (src, largest srcset, lazy attributes, inline `background-image`, `<noscript>`, `og:image` lead) and respects depth, cap, scope, robots, redirect checks and the non-public-address block. Tracking parameters are dropped, while content parameters are kept |
| 2. Single format, fixed max size | Every fixture image (PNG RGBA, GIF, WebP, large JPEG, EXIF-rotated) becomes RGB JPEG ≤ 1024 px with no upscaling. SVG, tiny and decompression-bomb images are skipped with a logged reason; corrupt bytes are a retryable failure |
| 3. Detect and embed all faces | `FakeEmbedder` returning 3 faces → 3 face rows; DeepFace's whole-image fallback (confidence 0) → 0 faces; 511-d vectors are rejected |
| 4. Store metadata, boxes, embeddings | Loopback pgvector: atomic commit (a failure inside the transaction leaves no faces and the image `pending`); the `no_faces` transition succeeds and keeps the dimensions; URL and fingerprint reuse; a lost insert race still links to the winner's ID; no duplicates after two runs; the lead mark is moved or cleared; `last_seen_at`, `depth` and `indexed_at` are refreshed; schema checks reject half-set dimensions; the HNSW cosine query returns the inserted face |
| Rate limits | The in-memory pacer, with a fake clock, never allows more than N starts in any 1-second window per `(host, kind)`. The Postgres pacer, with two concurrent callers on one host, reserves non-overlapping slots |
| DAG | Imports cleanly; param schema rejects `depth=-1` and `depth=11`; tasks use the expected pools; `max_active_runs == 1` |
