# Research & Decisions: Image Indexer Pipeline

Phase 0 output for [plan.md](plan.md). Each entry gives a **Decision**, its **Rationale** (citing the spec, assignment or constitution), and the **Alternatives** considered. Facts marked *verify at implementation* depend on package releases that move. They are pinned when `uv.lock` and the Dockerfile are written, and any change is recorded here.

---

## R1. Airflow version and Python 3.13

**Decision**: Use the latest Apache Airflow **3.x** patch whose official image ships a `-python3.13` tag, pinned exactly in `docker/airflow.Dockerfile` and installed with Airflow's constraints file. DAGs use the Airflow 3 Task SDK (`from airflow.sdk import dag, task, Param`). *Verify at implementation*: that the tag exists and that `tensorflow` + `deepface` install under the constraints on Python 3.13, on both arm64 and amd64.

**Rationale**: The constitution fixes Python 3.13. Airflow 3.1+ supports 3.13, and TensorFlow 2.20+ ships 3.13 wheels for linux arm64 and amd64. Airflow 3 is the maintained line, and its Task SDK keeps DAG files light.

**Alternatives**:
- Airflow 2.10: end of life soon and has no 3.13 support.
- Python 3.12 image: would break constitution "Project Constraints". It is used only if the 3.13 check above fails, and then it is recorded as a justified deviation.

## R2. Airflow deployment shape

**Decision**: Use the **LocalExecutor**. Compose services:
- `postgres`
- `airflow-init`: runs `airflow db migrate`, then creates the pools (R3), then exits.
- `airflow-apiserver`: UI and API on `:8080`.
- `airflow-scheduler`
- `airflow-dag-processor`

There is no triggerer (no deferrable operators), no Redis or Celery, and no worker service. Auth uses the default simple auth manager with `SIMPLE_AUTH_MANAGER_ALL_ADMINS=True` for this local demo, recorded in the README.

**Rationale**: KISS/YAGNI (constitution V). One machine with tens of tasks per run needs no distributed executor. LocalExecutor runs each task in its own process, which suits per-process TensorFlow models.

**Alternatives**: CeleryExecutor would add Redis and a worker service for no benefit at this scale. Running the standalone `airflow standalone` command in one container is not the documented production shape and hides how the services are split.

## R3. DAG shape, concurrency and collisions (FR-022, FR-022a, FR-022b)

**Decision**: There is one DAG, `image_indexer`, with `schedule=None`, `max_active_runs=1` and `catchup=False`. A new trigger waits as `queued` while a run is active.

```text
init_schema ─┬─ crawl[cnn]  ── download[cnn]  ─┐
             └─ crawl[people] ── download[people] ┴─ plan_batches ── detect.expand(batch) ── summarize
```

- `crawl[site]` and `download[site]` are defined per site in a loop over `config.SITES` (not mapped), so each gets the static pool `http_<site>` with 1 slot. Only one HTTP-making task per site can run at once, even when an operator clears a single task.
- `download[site]` writes images in the `pending` state, together with their JPEG bytes and page links (R11). That decouples the network from the CPU.
- `plan_batches` lists the IDs of all `pending` images, including ones left by earlier failed runs, and splits them into batches of `config.DETECT_BATCH_SIZE` (default 50).
- `detect` is a mapped task in pool `face_cpu` (default 2 slots, settable by the `FACE_WORKERS` environment variable in `airflow-init`). Each batch claims its rows with `SELECT … FOR UPDATE SKIP LOCKED`, so two executions never process the same image.
- Pools are created by `airflow-init` (`airflow pools set http_cnn 1 …`), so a fresh stack is collision-safe without manual steps (SC-001).

**Rationale**: This is the clarification Q4 answer, using only features Airflow already has. Sites run in parallel because they share no hosts. Face work runs in parallel because batches hold disjoint, row-locked records. The two steps of the same site never overlap, because of the pool.

**Alternatives**:
- A mapped `crawl.expand(site=…)`: a pool can't vary per mapped instance, so it would need a shared rate limiter.
- One task per site doing download *and* detection: simpler, but CPU work would run one image at a time per site, which breaks SC-011 (R8).
- Streaming download → detect overlap through a queue: extra moving parts for about a 3-minute gain.

## R4. Rate limiting, robots.txt and identity (FR-003, FR-004)

**Decision**:
- An in-process **pacer** keyed by `(host, kind)`, where `kind ∈ {page, image}`, enforces a minimum interval of `1/rate` between request starts. The clock is injected so tests can control it. The pool from R3 guarantees a single process per site, so in-process pacing is enough.
- **People serves images from `people.com` itself.** That host therefore has two independent budgets, page (1/s) and image (5/s). The rule "a page rate for page hosts and an image rate for image hosts" applies per request kind.
- **robots.txt**: fetched once per host per task through the same client, and parsed with `urllib.robotparser`, following RFC 9309:
  - 2xx: obey the rules.
  - 4xx: allow all.
  - 5xx or a network error: disallow the whole host for this run, logged (the spec edge case "unreachable → conservative").
  - Checked for both pages and images, including images on CDN hosts.
- **User-Agent**: `photo-crawler/<version> (+<repo URL>)`, whose robots token is `photo-crawler`. Timeouts: 10 s to connect, 20 s to read. Up to 5 redirects; the scope check applies to the final URL.

**Rationale**: FR-003 and FR-004, and the constitution's crawling etiquette. Pacing by request start is simple to test and never exceeds N requests in any 1-second window.

**Alternatives**:
- A token bucket with bursts would allow brief over-rate bursts, which conflicts with SC-010.
- A shared rate limiter in Redis or Postgres is only needed if the same host were shared across tasks, which R3 rules out.
- One combined budget per host would halve People's image throughput for no politeness gain.

## R5. Crawl scope and URL normalization (FR-001, FR-002, FR-005a, FR-007)

**Decision**:
- Sites in `config.SITES`: `cnn` with seed `https://edition.cnn.com/` and page hosts `{edition.cnn.com}`; `people` with seed `https://people.com/` and page hosts `{people.com, www.people.com}`.
- The crawl is breadth-first from the seed. The seed is depth 0, and links found at depth d are depth d+1, crawled only if d+1 ≤ `depth`. The crawl stops adding pages once `page_cap` pages have been fetched successfully (`None` means no cap). It follows only `text/html` responses.
- **Page URL normalization**: lowercase the scheme and host, map `http` to `https`, drop the fragment and the **entire query string**, collapse duplicate slashes, and strip a trailing `/` except at the root. Article URLs on both sites carry no meaningful query, and dropping it removes tracking parameters in one rule.
- **Image URL normalization**: lowercase the host, drop the fragment, **keep the query** (CDN resize and crop parameters identify the rendition).

**Rationale**: FR-007's "cosmetic variants" are mostly tracking parameters and fragments. Keeping query strings on images respects the clarified rule that different renditions are separate images.

**Alternatives**: An allow-list of query parameters per site would mean upkeep for each site for no current need (YAGNI). Allowing any `*.cnn.com` page host would pull in `www.cnn.com` (US edition), `money.cnn.com` and others, which goes beyond "edition.cnn.com".

## R6. Image reference extraction and lead image (FR-006, FR-006a)

**Decision**: For each page, collect from:
- `<img>` attributes `src`, `srcset`, `data-src`, `data-srcset`, `data-original`, `data-lazy-src`
- `<picture><source srcset>`

Then:
- Resolve relative URLs against the page and drop `data:` URIs.
- When a `srcset` exists, choose the candidate with the largest `w` descriptor, otherwise the largest `x`, otherwise `src`.
- The **lead image** is `<meta property="og:image">`, falling back to `<meta name="twitter:image">`. It is always collected and its link is marked `is_lead = true`. At most one lead per page.
- Downloads send `Accept: image/jpeg,image/png,image/webp,image/gif;q=0.9,*/*;q=0.5`, so that CDNs that negotiate formats prefer formats Pillow decodes natively over AVIF.
- Selectors are validated against HTML fixtures captured from both live sites at implementation and kept under `tests/fixtures/` (*verify at implementation*).

**Rationale**: The spec's edge cases on responsive and lazy-loaded images; clarification Q2 (option D: page metadata, no layout rules per site).

**Alternatives**: A headless browser to render JavaScript is heavy and not needed, because both sites render their HTML on the server. CSS `background-image` scraping is left out: those are rare for editorial photos and not worth the parser complexity.

## R7. Normalization rules (FR-008, FR-009, FR-010)

**Decision**, in `normalize.py` with Pillow:

1. Reject SVG (by content type or `.svg`) → `unsupported_format`.
2. `Image.open` from the bytes; a decode error → `undecodable`.
3. Shorter side < `MIN_SIDE` (64) of the original → `too_small`.
4. Take the first frame (`seek(0)`) for animated images.
5. `ImageOps.exif_transpose`.
6. Composite RGBA/LA/P-with-transparency onto white, then convert to RGB.
7. `thumbnail((1024, 1024), LANCZOS)`, which only ever shrinks.
8. Save as JPEG with quality 90, `optimize=True`, without EXIF.

- **Content fingerprint**: SHA-256 of the **original downloaded bytes**, used for de-duplication (FR-020).
- The normalized dimensions recorded are those of the saved JPEG, and face boxes refer to it.

**Rationale**:
- Fingerprinting the original bytes makes "identical content" exact and independent of encoder version.
- Quality 90 is visually lossless for detection and gives about 100 KB per image (clarification Q3, size note).
- Stripping EXIF avoids carrying location or device metadata from third-party photos into our store.

**Alternatives**:
- Hashing the normalized bytes depends on the Pillow/libjpeg version, so the hash could change after an upgrade.
- A perceptual hash would merge different renditions, which contradicts the Q2 edge case ("not merged").
- PNG output would be lossless but 5–10× larger.

## R8. Run-time budget (SC-011)

**Estimate for a default run, from an empty index**:

| Stage | CNN | People | Runs |
|---|---|---|---|
| Crawl, about 50 pages at 1 req/s plus robots | ~1 min | ~1 min | sites in parallel |
| Download, about 50 × 20 refs, about 600 unique after URL de-duplication, at 5 req/s | ~2 min | ~2 min | sites in parallel |
| Detect, about 1,200 images × ~1.2 s (RetinaFace + Facenet512 at ≤1024 px, CPU) ÷ 2 workers, plus about 20 s model load per batch | ~14 min in total | | 2 parallel batches |

**Total ≈ 17–20 min**, leaving headroom under 30 min. If a laptop measures slower, the levers in order are: raise `FACE_WORKERS` (needs RAM), lower the page cap, or set `MIN_SIDE` higher to skip thumbnails. The measured figure goes in the README.

## R9. DeepFace usage (FR-011, FR-012, FR-013, FR-014)

**Decision**: `DeepFaceEmbedder.embed(jpeg: NDArray) -> list[DetectedFace]` calls:

```text
DeepFace.represent(img_path=<BGR ndarray>, model_name="Facenet512",
                   detector_backend="retinaface", enforce_detection=False,
                   align=True, normalization="base")
```

- **Gotcha**: with `enforce_detection=False` and no face found, DeepFace returns **one entry covering the whole image with `face_confidence == 0`**. Results are kept only when `face_confidence ≥ MIN_FACE_CONFIDENCE` (0.9, RetinaFace's own default threshold). That makes FR-014 hold, and it is the spec's "standard confidence".
- Results are narrowed from `dict[str, Any]` into `DetectedFace(box: Box, confidence: float, embedding: tuple[float, ...])`, validating 512 dimensions (constitution II, trust boundary).
- Face order is left to right, then top to bottom, by box, matching the search brief's face numbering. The face index is stored.
- One `DeepFaceEmbedder` is built per `detect` task process, and models load once per batch.
- **Weights**: `docker/warm_models.py` runs at image build time to cache the RetinaFace and Facenet512 weights in `/opt/deepface` (`DEEPFACE_HOME`). Runs never download them.
- `model_profile.py` holds `DETECTOR`, `MODEL`, `DIMENSIONS=512`, `DISTANCE="cosine"`, `ALIGN=True`, `NORMALIZATION="base"`, `MIN_FACE_CONFIDENCE=0.9` and `deepface_version`. Its identity key is stored in the `model_profile` table, and each face references it (FR-013).

**Rationale**: These are the brief's fixed model choices. Representing directly avoids running detection twice (`extract_faces` + `represent`).

**Alternatives**:
- `enforce_detection=True`: raises an error when there are no faces, which would mean using exceptions for control flow.
- A minimum face-size filter: the spec says "no extra quality filtering". This risk is noted in `TODO.md` for the search side.

## R10. Database design choices (FR-015 … FR-018)

**Decision**:
- **psycopg 3** + the **pgvector** adapter, with plain SQL.
- **Schema**: in `src/face_index/schema.sql`, written as idempotent DDL (`CREATE … IF NOT EXISTS`), and applied by the `init_schema` task on every run and by test fixtures. There is no migration framework.
- **Embeddings**: `vector(512)` with an **HNSW** index, `vector_cosine_ops`, default parameters (`m=16, ef_construction=64`).
- **JPEG bytes**: in `image.jpeg bytea`. Postgres TOAST stores them outside the row and tries compression, which leaves JPEG data almost unchanged (clarification Q3).
- **Image status**: an `image_status` enum: `pending`, `indexed`, `no_faces`, `skipped`.
- Full details are in [data-model.md](data-model.md).

**Rationale**:
- It follows the brief's storage decision (one database).
- HNSW works on an empty table and gives good recall without a training step, unlike IVFFlat.
- Idempotent DDL is the simplest correct choice for a single-version schema (constitution V).

**Alternatives**:
- SQLAlchemy plus Alembic: too heavy for 6 tables.
- An IVFFlat index: needs data before building lists.
- A separate vector store: rejected in the brief.

## R11. Incremental logic, idempotency and atomicity (FR-019 … FR-022b)

**Decision**: For each image reference found by `download[site]`:

1. Look up the normalized URL in `image_url`. **Known** → upsert the `page_image` link (and `is_lead` if set); no download (FR-019).
2. Unknown → robots check, paced download, then fingerprint.
3. Fingerprint already in `image` → insert `image_url` and `page_image` and reuse the image, with no detection (FR-020).
4. New → normalize. In **one transaction**, insert the `image` (`pending` with JPEG bytes, or `skipped` with a reason and no bytes), its `image_url` and its `page_image`.

`detect` then, in **one transaction per image**:
- inserts every `face` row and sets `status = indexed`, or
- sets `status = no_faces` and sets `jpeg = NULL`, since the clarification says images without faces keep no bytes.

All inserts use `ON CONFLICT DO NOTHING` on natural keys: `page.url`, `image.sha256`, `image_url.url`, `(page_id, image_id)` and `(image_id, face_index)`. For `is_lead`, a conflict on `page_image` updates `is_lead = page_image.is_lead OR EXCLUDED.is_lead`.

Download failures are not persisted, so the URL stays unknown and is retried next run (FR-023). Detection failures leave the image `pending`, so it is retried by the next `plan_batches`.

**Rationale**: FR-021 reads "image and its faces together or not at all". Seen from search, an image is invisible until it is `indexed`, and its faces appear in the same commit as the status flip. A `pending` image has bytes but no faces by definition and is never read by search ([index-read.md](contracts/index-read.md)).

**Alternatives**:
- Download and detect in one transaction: holds a transaction open during network I/O and forces download and detection to run one after the other.
- Persisting failed downloads with a retry counter: no current need (YAGNI). The run logs and summary cover diagnosis.

## R12. Failure isolation, logging and run summary (FR-023 … FR-025)

**Decision**:
- Every page and image is processed inside its own `try/except Exception`, logged with `logger.warning("image failed url=%s reason=%s", …)`, and counted. A per-item failure never raises out of the task.
- Tasks raise only on infrastructure failures (database unreachable, model load failure), and Airflow then retries the task once (`retries=1`, `retry_delay=1 min`).
- Each stage function returns a typed summary dataclass, passed by XCom: small dicts of counts, never image data. `summarize` merges them and logs one table: pages crawled per site, images found, skipped by reason, newly indexed, reused by URL, reused by fingerprint, faces stored, and failures by stage.

**Rationale**: The constitution's failure isolation and FR-025. XCom carries only small counters, as Airflow recommends.

**Alternatives**: Recording failures in a table would add persistent state with no reader, since the assignment's evaluator reads the Airflow logs.
