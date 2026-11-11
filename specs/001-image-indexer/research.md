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
- The per-site pools keep each site's own steps apart. Pacing across sites is a separate job, done by the shared pacer in R4: if CNN and People ever share a host (a common CDN), their requests to it draw from one budget.
- **Re-running a `download` task by hand** must use Airflow's default *clear with downstream*, so `plan_batches` and `detect` run again and pick up the newly staged rows. If the operator clears only the `download` task, the rows it stages stay `pending` and are processed by the next run's `plan_batches`. Nothing is lost, and this is documented in the README.

**Rationale**: This is the clarification Q4 answer, using only features Airflow already has. Sites run in parallel because they share no hosts. Face work runs in parallel because batches hold disjoint, row-locked records. The two steps of the same site never overlap, because of the pool.

**Alternatives**:
- A mapped `crawl.expand(site=…)`: a pool can't vary per mapped instance, so it would need a shared rate limiter.
- One task per site doing download *and* detection: simpler, but CPU work would run one image at a time per site, which breaks SC-011 (R8).
- Streaming download → detect overlap through a queue: extra moving parts for about a 3-minute gain.

## R4. Rate limiting, robots.txt and identity (FR-003, FR-004)

**Decision**:
- A **shared pacer** keyed by `(host, kind)`, where `kind ∈ {page, image}`, enforces a minimum interval of `1/rate` between request starts **across all tasks**. It is backed by a small `host_pace(host, kind, next_slot)` table in the `faces` database. Before each request, one atomic statement reserves the next slot:

  ```sql
  INSERT INTO host_pace (host, kind, next_slot) VALUES ($1, $2, clock_timestamp() + $3)
  ON CONFLICT (host, kind) DO UPDATE
    SET next_slot = greatest(host_pace.next_slot, clock_timestamp()) + $3
  RETURNING next_slot - $3 AS my_slot;
  ```

  The caller then sleeps until `my_slot`. Row locking serializes concurrent reservations, so two tasks contacting the same host can never exceed its rate together. At most about 12 requests per second in total, the extra database load is negligible.
- The pacer sits behind a `Pacer` protocol. Unit tests use an in-memory implementation with an injected clock, and an integration test runs the SQL version against loopback Postgres with two concurrent callers.
- **People serves images from `people.com` itself.** That host therefore has two independent budgets, page (1/s) and image (5/s). The rule "a page rate for page hosts and an image rate for image hosts" applies per request kind.
- **robots.txt**: fetched once per host per task through the same client, and parsed with `urllib.robotparser`, following RFC 9309:
  - 2xx: obey the rules.
  - 4xx: allow all.
  - 5xx or a network error: disallow the whole host for this run, logged (the spec edge case "unreachable → conservative").
  - Checked for both pages and images, including images on CDN hosts.
- **User-Agent**: `photo-crawler/<version> (+<repo URL>)`, whose robots token is `photo-crawler`. Timeouts: 10 s to connect, 20 s to read.
- **Redirects are followed by hand** (`follow_redirects=False`, at most 5 hops). Before *each* hop is requested, the fetcher re-runs every check: page scope (for page requests), robots.txt for the target host, address safety, and a pacer slot for the target `(host, kind)`.
- **Address safety (no SSRF)**: before connecting, the host is resolved, and the request is refused if *any* resolved address is not globally routable (`ipaddress.ip_address(a).is_global` is false: loopback, private, link-local including `169.254.169.254`, CGNAT, multicast, reserved). Only `http`/`https` on ports 80/443 are allowed. The connection is made to the vetted IP, with the original `Host` header and TLS server name, so DNS can't change between the check and the connect (DNS rebinding). Refusals are logged as item failures.

**Rationale**: FR-003, FR-004 and FR-007a, and the constitution's crawling etiquette. Pacing by request start is simple to test and never exceeds N requests in any 1-second window. A database-backed pacer reuses the Postgres instance already running, so a host shared between sites stays within its rate without adding a service.

**Alternatives**:
- A token bucket with bursts would allow brief over-rate bursts, which conflicts with SC-010.
- An in-process pacer is correct only if no host is ever shared between tasks. CNN and People don't share hosts today, but a common CDN would break that silently.
- A Redis rate limiter would add a service for something Postgres does in one statement.
- One `http` pool for both sites would be correct, but it would give up running the sites in parallel, which clarification Q4 asked for.
- One combined budget per host would halve People's image throughput for no politeness gain.
- httpx's automatic redirects would send the request to the redirect target before any check runs.

## R5. Crawl scope and URL normalization (FR-001, FR-002, FR-005a, FR-007)

**Decision**:
- Sites in `config.SITES`: `cnn` with seed `https://edition.cnn.com/` and page hosts `{edition.cnn.com}`; `people` with seed `https://people.com/` and page hosts `{people.com, www.people.com}`.
- The crawl is breadth-first from the seed. The seed is depth 0, and links found at depth d are depth d+1, crawled only if d+1 ≤ `depth`. The crawl stops adding pages once `page_cap` pages have been fetched successfully (`None` means no cap). It follows only `text/html` responses.
- **Page URL normalization**: lowercase the scheme and host, map `http` to `https`, drop the fragment, collapse duplicate slashes, and strip a trailing `/` except at the root. In the query, **drop only known tracking parameters** (`utm_*`, `fbclid`, `gclid`, `dclid`, `msclkid`, `mc_cid`, `mc_eid`, `cid`, `ref`, `ref_src`, `taid`, `iid`), sort the rest by name, and drop the `?` if nothing remains. The tracking list lives in `config.py`. Parameters that can select content (pagination, gallery slide, search) are kept, so distinct pages don't collapse into one.
- **Image URL normalization**: lowercase the host, drop the fragment, **keep the query** (CDN resize and crop parameters identify the rendition).

**Rationale**: FR-007's "cosmetic variants" are mostly tracking parameters and fragments. A deny-list removes those without risking two different pages being merged. Keeping query strings on images respects the clarified rule that different renditions are separate images.

**Alternatives**: Dropping the whole query string is simpler, but it merges pages that differ only by a content-selecting parameter, losing their image links. An allow-list of query parameters per site would mean upkeep for each site for no current need (YAGNI). Allowing any `*.cnn.com` page host would pull in `www.cnn.com` (US edition), `money.cnn.com` and others, which goes beyond "edition.cnn.com".

## R6. Image reference extraction and lead image (FR-006, FR-006a)

**Decision**: For each page, collect from:
- `<img>` attributes `src`, `srcset`, `data-src`, `data-srcset`, `data-original`, `data-lazy-src`
- `<picture><source srcset>`
- inline CSS: every `url(…)` inside a `background-image` or `background` declaration in a `style` attribute (a regex over the attribute value; `image-set()` takes its last candidate)
- `<noscript>` blocks, parsed as HTML, since lazy-loading setups often put the real `<img>` there

Then:
- Resolve relative URLs against the page and drop `data:` URIs.
- When a `srcset` exists, choose the candidate with the largest `w` descriptor, otherwise the largest `x`, otherwise `src`.
- The **lead image** is `<meta property="og:image">`, falling back to `<meta name="twitter:image">`. It is always collected and its link is marked `is_lead = true`. At most one lead per page.
- Downloads send `Accept: image/jpeg,image/png,image/webp,image/gif;q=0.9,*/*;q=0.5`, so that CDNs that negotiate formats prefer formats Pillow decodes natively over AVIF.
- Selectors are validated against HTML fixtures captured from both live sites at implementation and kept under `tests/fixtures/` (*verify at implementation*).

**Rationale**: The spec's edge cases on responsive and lazy-loaded images; clarification Q2 (option D: page metadata, no layout rules per site).

**Alternatives**:
- A headless browser to render JavaScript is heavy and not needed, because both sites render their HTML on the server.
- Fetching and parsing external stylesheets for `background-image` rules: a CSS parser, extra requests, and selector matching back to the page, for photos that editorial pages almost never put there. Inline `style` attributes, which hero and card components do use, are covered by a regex.

## R7. Normalization rules (FR-008, FR-009, FR-010)

**Decision**, in `normalize.py` with Pillow:

0. **Download limit**: the body is streamed and aborted once it exceeds `MAX_DOWNLOAD_BYTES` (20 MB), or immediately if `Content-Length` says so. That is an item failure (no fingerprint exists yet, so nothing is stored), logged, and retried next run.
1. Reject SVG (by content type or `.svg`) → skip `unsupported_format`.
2. Set `Image.MAX_IMAGE_PIXELS = MAX_PIXELS` (50 megapixels) and treat Pillow's `DecompressionBombWarning` as an error. `Image.open` reads only the header, so the size is checked before any pixels are decoded. Above the limit → skip `too_large`.
3. A decode error (truncated or corrupt data) → **item failure**, not a skip. Nothing is stored, so the URL stays unknown and is downloaded again next run, in case the source was only temporarily broken.
4. Shorter side < `MIN_SIDE` (64) of the original → skip `too_small`.
5. Take the first frame (`seek(0)`) for animated images.
6. `ImageOps.exif_transpose`.
7. Composite RGBA/LA/P-with-transparency onto white, then convert to RGB.
8. `thumbnail((1024, 1024), LANCZOS)`, which only ever shrinks.
9. Save as JPEG with quality 90, `optimize=True`, without EXIF.

Skips are permanent because they depend only on the bytes, which the fingerprint pins. Decode errors and oversized downloads are retried because they may be temporary.

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
- `model_profile.py` holds `DETECTOR`, `MODEL`, `DIMENSIONS=512`, `DISTANCE="cosine"`, `ALIGN=True`, `NORMALIZATION="base"`, `MIN_FACE_CONFIDENCE=0.9`, plus the installed `deepface` and `tensorflow` versions. Its identity key is stored in the `model_profile` table, and each face references it (FR-013).
- The **key includes the major.minor versions** of `deepface` and `tensorflow`, for example `retinaface+Facenet512+cosine+align+base@deepface0.0.95/tf2.20`. DeepFace uses 0.0.x numbering, so for it the full version counts as "major.minor". Patch versions of TensorFlow are recorded but left out of the key. A dependency upgrade that could change embeddings therefore creates a new profile row. Search uses only faces whose `model_profile_id` matches its own key, so vectors from different runtimes are never compared.

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
- **Image status**: an `image_status` enum: `pending`, `indexed`, `no_faces`, `skipped`. Skip reasons: `unsupported_format`, `too_small`, `too_large`.
- **Pacer state**: the `host_pace` table (R4) lives in the same database.
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

**Decision**: `download[site]` handles the pages in crawl order. For each page:

1. **Upsert the page** and get its ID. A conflict refreshes the crawl metadata and keeps the smallest depth:
   `INSERT … ON CONFLICT (url) DO UPDATE SET last_crawled_at = now(), depth = least(page.depth, EXCLUDED.depth) RETURNING id`.
2. **Reset the lead mark**: `UPDATE page_image SET is_lead = false WHERE page_id = $p AND is_lead`. The page's *current* lead is set again in step 3 once its image resolves. A lead that changed or disappeared on the site is therefore cleared, and the one-lead-per-page index can never conflict.
3. For each image reference on the page:
   1. Look up the normalized URL in `image_url`. **Known** → link it (below), with no download (FR-019).
   2. Unknown → robots and address checks, paced download, then fingerprint.
   3. Fingerprint already in `image` → insert `image_url` and link it. The image is reused, with no detection (FR-020).
   4. New → normalize, then in **one transaction** insert the `image` (`pending` with JPEG bytes and normalized dimensions, or `skipped` with a reason), its `image_url`, and its link.

**Getting IDs when two inserts race** (`image` by `sha256`, `page` by `url`): use `INSERT … ON CONFLICT DO NOTHING RETURNING id`. If no row comes back, the insert lost the race, so `SELECT id … WHERE <natural key>`. Under READ COMMITTED, the losing insert waits for the winner to commit before it reports the conflict, so the follow-up `SELECT` always finds the winner's row. URL and page links are then written against that ID, so no association is lost.

**Linking**: `INSERT INTO page_image (page_id, image_id, is_lead) … ON CONFLICT (page_id, image_id) DO UPDATE SET last_seen_at = now(), is_lead = EXCLUDED.is_lead`. `is_lead` is true only for the page's current declared lead. Step 2 has already cleared every other lead mark, so assigning (not OR-ing) is correct.

**`detect`**, in **one transaction per image**, on a row locked with `FOR UPDATE SKIP LOCKED` and still `pending`:
- **≥ 1 face**: insert every `face` row, then `UPDATE image SET status = 'indexed', indexed_at = now()`.
- **0 faces**: `UPDATE image SET status = 'no_faces', jpeg = NULL, indexed_at = now()`. The normalized `width`/`height` are **kept**, as metadata of what was analyzed, and the schema allows dimensions without bytes for `no_faces` ([data-model.md](data-model.md)).

`image_url` and `face` inserts use `ON CONFLICT DO NOTHING` on `url` and `(image_id, face_index)`. A retried batch is a no-op for rows that are already `indexed`, because they no longer match `status = 'pending'`.

Download failures, decode errors included, are not persisted, so the URL stays unknown and is retried next run (FR-023). Detection failures leave the image `pending`, so the next `plan_batches` retries it.

**Rationale**: FR-021 as clarified: an image is *staged* (`pending`) with its bytes and links, and becomes visible to readers only when it turns `indexed` in the same commit as its faces. The search reads only `indexed` images ([index-read.md](contracts/index-read.md)), so it never sees a partial set of faces. The upserts keep `last_crawled_at`, `last_seen_at`, `depth` and `indexed_at` accurate across re-runs.

**Alternatives**:
- Download and detect in one transaction: holds a transaction open during network I/O and makes the two stages run one after the other (see R3 and R8).
- `ON CONFLICT DO UPDATE … RETURNING` with a dummy update to always get the ID: it works, but it writes a new row version on every hit. The follow-up `SELECT` is cheaper and clearer.
- Persisting failed downloads with a retry counter: no current need (YAGNI). The run logs and summary cover diagnosis.

## R12. Failure isolation, logging and run summary (FR-023 … FR-025)

**Decision**:
- **Two kinds of exception.** `face_index` defines `InfrastructureError`. The store wraps `psycopg.OperationalError` and `psycopg.InterfaceError` (connection lost, server down) in it, and the embedder wraps model-load failures in it. The per-item handler is:

  ```text
  try: process(item)
  except InfrastructureError: raise           # Airflow fails and retries the task
  except Exception as e: log + count failure  # item failure, run continues
  ```

  So a database outage fails the task (Airflow retries it once, `retries=1`, `retry_delay=1 min`) and is never miscounted as an item failure. Each item's work is committed in its own transaction before the next item starts, so a retried task redoes only the work that wasn't committed, and that is safe to repeat (R11).
- **Logging every outcome with its URL** (FR-024):
  - Skips are returned values, not exceptions, and are logged at INFO: `image skipped url=… reason=too_small`.
  - Item failures are logged at WARNING: `image failed url=… stage=download reason=…`.
  - Page failures and robots refusals are logged the same way.
- Each stage function returns a typed summary dataclass, passed by XCom: small dicts of counts, never image data. `summarize` merges them and logs one table: pages crawled per site, images found, skipped by reason, newly indexed, reused by URL, reused by fingerprint, faces stored, and failures by stage.

**Rationale**: The constitution's failure isolation, plus FR-023's clarified split between item and infrastructure failures, plus FR-025. XCom carries only small counters, as Airflow recommends.

**Alternatives**:
- Catching everything per item: hides outages as thousands of "item failures" and lets a run end as `success` with work missing.
- Recording failures in a table: adds persistent state with no reader, since the evaluator reads the Airflow logs.
