# Research: Face Search Script

Every open choice for feature 002, with the spec line it serves. Facts about the pipeline were checked against the code on branch `feat/indexer-4-spec-status` (2026-11-13).

## R1. Where the script lives and how it runs

**Decision**:
- **Location**: the CLI is `src/face_index/face_matcher.py`, run as `uv run python -m face_index.face_matcher` (on the host) or `uv run --no-project python -m face_index.face_matcher` (in the image, where it runs in Airflow's interpreter).
- **Service**: Compose gets a `face-matcher` service with `profiles: [tools]`, so `docker compose up` does not start it. It uses the pipeline's image (`photo-crawler-airflow:latest`, same build anchor) with the entrypoint replaced by `uv run --no-project python -m face_index.face_matcher`.
  - Environment: only `FACE_INDEX_DSN`, `DEEPFACE_HOME` and `TF_CPP_MIN_LOG_LEVEL`.
  - It runs as `${AIRFLOW_UID:-50000}`, has `working_dir: /work`, mounts `./queries:/work/queries`, and depends on a healthy `postgres`.
- **One command** (FR-016): `docker compose run --rm face-matcher queries/alice.jpg`.
- **Paths**: because the container's working directory mirrors the project root, a path under `queries/` is the same string on the host and in the container. The printed path of an annotated copy (`queries/alice.faces.jpg`) opens from the host as is, with no path translation (FR-012).

**Rationale**:
- The pipeline image already has DeepFace, TensorFlow and the baked weights, so the query is embedded by exactly the runtime that produced the index. That is the same `model_profile.KEY`, which a separate image could silently break.
- `docker compose run` returns the script's exit status, so FR-015 holds through Compose.
- No Airflow code runs: the entrypoint is replaced, and the module imports nothing from Airflow.

**Alternatives considered**:
- **A slim search-only image**: smaller, but a second dependency set to keep in step with the pipeline's for the profile key to match. Rejected (DRY, constitution "shared model contract").
- **`docker compose exec airflow-scheduler uv run --no-project python -m …`** (as the export CLI does): needs the Airflow stack running and has no `queries/` mount. Rejected for the main path, though it still works for operators.
- **A root-level `face_matcher.py` wrapper**: a second entry point not in the image. Rejected (YAGNI). The module name already satisfies "`face_matcher.py` or similar".

## R2. Threshold: value, comparison and valid range

**Decision**: `model_profile.MATCH_THRESHOLD = 0.30`. A face matches when `cosine_distance < threshold` (strict). `--threshold` accepts a float in (0, 2]. Anything else exits with usage status 64 before the model loads (FR-005).

**Rationale**:
- `deepface.modules.verification.find_threshold("Facenet512", "cosine")` returns 0.3 in the pinned DeepFace (checked 2026-11-13). DeepFace's own `verify` uses `distance <= threshold`, but the read contract's reference query and spec FR-005 use "strictly below". The difference affects only exact ties at 0.30, so the contract wins.
- pgvector's `<=>` is cosine distance, `1 − cos`, in [0, 2]. A threshold of 0 or less can never match, and above 2 everything matches.
- The constant is hard-coded rather than read from DeepFace at run time, so that `search.py` stays importable without TensorFlow. A `model`-marked test asserts it equals `find_threshold(...)`, so a DeepFace upgrade that changes the recommendation fails CI's model job instead of drifting.

**Alternatives considered**: calling `find_threshold` at startup (adds a TensorFlow import to argument validation); allowing any positive float (accepts meaningless values silently).

## R3. Exact scan instead of the HNSW index

**Decision**: the per-face query filters with `WHERE f.embedding <=> q < :max_dist` and has no `ORDER BY … LIMIT` on `face`, so Postgres does an exact sequential scan.

**Rationale**:
- FR-006 requires every matching page, with no cap. HNSW is approximate and needs a `LIMIT`. Iterative scans could miss matches or need a guessed `k`.
- At the current scale (about 9.5k faces) and the designed scale (≤ about 10⁵), an exact scan of 512-d vectors is well under a second (read contract note).
- The HNSW index stays for future top-k use. Nothing is dropped.
- **Measured** (2026-11-13, live index, about 9.5k faces): the [contract query](contracts/search-query.md) for one face took 0.21 s, including the grouping and the image-URL lookup.

**Alternatives considered**: `ORDER BY distance LIMIT k` with `hnsw.iterative_scan = relaxed_order` (the contract's suggestion for larger scale). Deferred until the scale requires it.

## R4. Ignoring outdated page links (FR-006a)

**Decision**: a link counts only if `page_image.last_seen_at >= page.last_crawled_at`.

**Rationale**, checked in `pipeline.download` and `store.py`:
- The download step reads the database clock (`store.clock()`) before it touches a page, then calls `upsert_page`, which leaves an existing page's `last_crawled_at` alone. It links each image reference, and every `link` / `add_url_and_link` / `stage_image` sets `last_seen_at = now()` in a later transaction. Only after the page's last reference does `store.finish_page` set `last_crawled_at` to the start time.
- Both timestamps come from the same database clock, so every link refreshed in the latest finished crawl satisfies `>=`, and every link not seen in it is strictly older.
- A page whose fetch failed never reaches the download step, and a page whose download stopped partway never reaches `finish_page`. Either way its `last_crawled_at` stays at the previous finished crawl and all its links still count. That is the spec's "pages whose latest crawl failed keep their existing links".
- A known image URL is linked without a download, so a temporary image server failure cannot make a link to an already-indexed image look outdated.

**Measured** (2026-11-13, live index after two runs): 118 of the 5,727 page links are outdated by this rule. Those are the cases where clarification Q2 changes results.

**No window mid-crawl** (Sourcery review of #6, 2026-11-14): an earlier version set `last_crawled_at` in `upsert_page`, before the links were refreshed. While a page was being processed, and until a retry if the task died partway, search dropped that page's links not yet refreshed. Committing the crawl time in `finish_page` closes both.

**Guarantee to record**: `index-read.md` gets guarantee 6, "a link refreshed in a page's latest crawl is never older than that crawl", and its reference query adds the filter. `test_index_read.py` gains an outdated-link case.

**Reporting (FR-018)**: after `download` finishes a page's image references, it calls `store.outdated_links(page_id)`. That is a `SELECT count(*)` over the page's links with `last_seen_at < last_crawled_at`.
- When the count is above zero, it logs `outdated links page=<url> count=<n>` at INFO and adds `links.outdated` to the task summary.
- `summarize` prints the total as "outdated page links (ignored by search)".
- Nothing is deleted, so the search-side rule stays the single definition of "outdated".
- A page whose fetch failed is never processed by `download`, so it is never counted. That matches the rule.

**Alternatives considered**:
- **Deleting outdated links in the pipeline**: option 1 of `stale-page-links.md`. A pipeline change, rejected in clarification Q2.
- **Comparing against the run ID**: the tables don't record which run linked what.

## R5. Read-only access and the profile check

**Decision**:
- **Connection**: the adapter connects with `db.connect(dsn)` (same timeouts), then runs `SET default_transaction_read_only = on`. It never calls `init_schema`.
- **Profile check**: at startup it looks up `SELECT id FROM model_profile WHERE key = %s` with `model_profile.KEY`. If there's no row, or the `model_profile` or `face` table doesn't exist (`UndefinedTable`), it exits with status 4 (index not ready) and the profile key in the message.
- **Empty index**: `indexed_face_count(profile_id) == 0` gives the same status with an "index has no faces yet" message.

**Rationale**: FR-013 is enforced by the server, not only by code review. Any accidental write fails with `ReadOnlySqlTransaction`, which a test asserts. This was verified on the live database on 2026-11-13: even a `CREATE TEMP TABLE` was rejected. `init_schema` would create tables and upsert a profile row, so a script that silently "fixed" a profile mismatch would hide it.

**Alternatives considered**: a dedicated read-only Postgres role. The read contract calls this optional (YAGNI), and it needs grants in `postgres-init.sql` plus a second DSN.

## R6. Preparing the query photo

**Decision**:
- The script reads bytes from the path and runs `PillowNormalizer().normalize(data, None)`. It maps the results to exit status 2 (query file problem) with a specific message:
  - `Skipped(unsupported_format)`: "not a supported photo (SVG)".
  - `Skipped(too_small)`: "image is W×H; at least 64 px on the shortest side is needed".
  - `Skipped(too_large)`: "image exceeds 50 megapixels".
  - `ItemError` from decoding: "not a decodable image".
- It passes `NormalizedImage.jpeg` to `DeepFaceEmbedder().embed`. No faces gives exit status 3.
- Face numbers are the 1-based positions in the returned list. `faces.narrow_results` already sorts by `(box.x, box.y)` and drops faces under the 0.9 confidence floor, exactly as for indexed images (FR-003, FR-010).

**Rationale**: the same normalization (EXIF rotation, transparency flattened onto white, first frame, ≤ 1024 px) and the same detector settings make query and index embeddings comparable. Boxes are in normalized-image coordinates, the same space the annotated copy is drawn in.

**Alternatives considered**: embedding the original file directly (a different scale and preprocessing from the index, against FR-003).

## R7. Performance budget (SC-006)

**Decision**: no special optimization. One process loads the model once, then runs one SQL query per searched face.

| Step | Estimate | Basis |
|---|---|---|
| `docker compose run` container start | 1–2 s | existing image, no build |
| Import TensorFlow + DeepFace, build Facenet512 | 6–10 s | warm_models step; weights are in the image |
| Normalize + RetinaFace + embed one ≤ 1024 px photo | 1–2 s | about 1.4 s per image measured in the first pipeline run |
| SQL, exact scan of about 9.5k faces, per face | < 0.1 s | 512-d cosine on about 20 MB of vectors |
| **Total** | **about 10–15 s** | well within 30 s |

**Rationale**: SC-006 is checked in quickstart §4 with `time`. The search connects to the database first, before loading the model, so a down database fails in seconds rather than after the model load.

## R8. Measuring SC-001 to SC-003

**Decision**: `scripts/evaluate_search.py LABELS.toml` runs the same search code (in process, default threshold) for each labelled query and prints a report:
- per person: expected pages, returned pages, recall, extra pages, and every missed page;
- for non-site people: whether any page was returned.

It exits non-zero if any person's recall is below 0.80 (SC-001), any extra page is returned (SC-002), or any non-site person gets a page (SC-003).

The labels file, kept in `queries/`, so git-ignored with the photos:

```toml
[[person]]
name = "person-a"                      # a neutral label, never a real name in committed files
photo = "person-a.jpg"                 # relative to this file; not taken from the indexed site
pages = ["https://edition.cnn.com/…", "…"]

[[absent]]
photo = "absent-1.jpg"
```

How labels are built (documented in quickstart §5):
1. Pick at least 5 people who appear on several indexed CNN pages, and at least 5 who don't.
2. For each person on the site, find candidate pages with a loose search (`--threshold 0.45 --details`).
3. Confirm each candidate by eye with `export_image --boxes` and the page itself.
4. Add any page found by browsing.

**Rationale**:
- The spec's criteria are about the live index and real faces, which can't run offline and can't be committed (licensing, constitution IV). So they need a repeatable script, not a unit test.
- Listing missed pages is the clarification Q1 requirement.
- **Known bias**: candidates come from a looser search, so recall can be overestimated for faces the model misses entirely. Browsing in step 4 reduces this. The README reports it with the numbers.

**Alternatives considered**: a pytest marked `live` (pytest's pass/fail output can't print the missed pages on a passing run without extra plumbing); manual checking only (not repeatable).

## R9. Output format and exit statuses

**Decision**: the full format is in [contracts/face-matcher-cli.md](contracts/face-matcher-cli.md). In short:
- **stdout** carries results only. Each face group starts with a header line beginning `# ` (`# face 1 x=… y=… w=… h=…: N pages`), followed by one page URL per line, with a blank line between groups. `grep -v '^#'` gives a bare URL list.
- **`--details`** prefixes each page line with its distance and an optional `lead` mark, and follows it with indented `image:` lines.
- **stderr** carries progress, warnings and errors. The DSN is never printed.
- **Image addresses**: an image can have several stored URLs, so the details line shows the URL first seen for that image (`min(first_seen_at)`, ties by URL), which is deterministic.
- **Exit statuses**:

| Status | Meaning |
|---|---|
| 0 | Success (including "no matching pages") |
| 1 | Database or face model unavailable |
| 2 | Query file problem |
| 3 | No face in the query |
| 4 | Index not ready |
| 5 | Annotated copy could not be written |
| 64 | Usage error, including a face number the photo doesn't have |

**Rationale**:
- It follows `export_image`'s pattern (1 for infrastructure, 64 for usage), so the two CLIs read the same.
- Distinct statuses let SC-005 be tested per edge case.
- Keeping headers on stdout keeps each group's URLs next to its label, and `# ` makes them trivial to filter.

**Alternatives considered**:
- **Headers on stderr**: the groups lose their labels when stdout is piped.
- **JSON output**: not asked for; the spec's assumptions keep plain text.
