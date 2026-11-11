# Data Model: Image Indexer Pipeline

Phase 1 output for [plan.md](plan.md). It lives in the `faces` database (Postgres 17 + pgvector). The DDL is in [contracts/index-schema.sql](contracts/index-schema.sql), which mirrors `src/face_index/schema.sql`, the single source (constitution V).

```text
model_profile 1 ──< face >── 1 image 1 ──< image_url
                                  │
                                  └──< page_image >── 1 page
```

## Entities

### `model_profile`: the fixed face pipeline (FR-013)

| Field | Type | Rules |
|---|---|---|
| `id` | smallserial PK | |
| `key` | text, unique | Stable identity, including the deepface and tensorflow major.minor versions, e.g. `retinaface+Facenet512+cosine+align+base@deepface0.0.95/tf2.20`. Built by `model_profile.py` ([research R9](research.md#r9-deepface-usage-fr-011-fr-012-fr-013-fr-014)). |
| `detector` | text | `retinaface` |
| `model` | text | `Facenet512` |
| `dimensions` | smallint | `512`; must equal the `face.embedding` dimension |
| `distance` | text | `cosine` |
| `align` | boolean | `true` |
| `normalization` | text | `base` |
| `min_face_confidence` | real | `0.9` |
| `deepface_version`, `tensorflow_version` | text | Full installed versions, for traceability. Their major.minor parts are also in `key` |
| `created_at` | timestamptz | |

It is upserted by `init_schema`. The search script reads the row for its own `key` and refuses to run if it's missing. It then searches only faces with that `model_profile_id`, so embeddings from a different runtime are never compared.

### `page`: a crawled web page (spec entity "Page")

| Field | Type | Rules |
|---|---|---|
| `id` | bigserial PK | |
| `url` | text, unique | Normalized ([research R5](research.md#r5-crawl-scope-and-url-normalization-fr-001-fr-002-fr-005a-fr-007)) |
| `site` | text | `cnn` \| `people` (check constraint) |
| `depth` | smallint | Smallest depth this page was reached at in any run, ≥ 0 |
| `first_crawled_at`, `last_crawled_at` | timestamptz | |

### `image`: one unique picture, keyed by content (spec entity "Image")

| Field | Type | Rules |
|---|---|---|
| `id` | bigserial PK | |
| `sha256` | bytea, unique | 32 bytes, from the **original** downloaded bytes (R7) |
| `status` | `image_status` enum | `pending` \| `indexed` \| `no_faces` \| `skipped` |
| `skip_reason` | `skip_reason` enum, null | `unsupported_format` \| `too_small` \| `too_large`; non-null **iff** `status = skipped`. Decode errors are not skips: they are item failures, retried next run |
| `original_format` | text, null | Pillow format name or content type (`JPEG`, `PNG`, `WEBP`, `GIF`, `image/svg+xml`, …) |
| `original_width`, `original_height` | integer, null | Both set or both null; null only for `unsupported_format` |
| `width`, `height` | integer, null | Normalized JPEG dimensions, each in 1–1024. Both set **iff** status ∈ {pending, indexed, no_faces}, otherwise both null. `no_faces` keeps them as metadata of what was analyzed |
| `jpeg` | bytea, null | Normalized JPEG bytes; non-null **iff** status ∈ {pending, indexed} (clarification Q3) |
| `created_at` | timestamptz | |
| `indexed_at` | timestamptz, null | Set in the same transaction that makes the status `indexed` or `no_faces`; non-null **iff** status ∈ {indexed, no_faces} |

Check constraints enforce the "iff" rules, the both-or-neither rule for each dimension pair, and the 1–1024 bound on each side. That makes SC-003 a database invariant, not just a test.

### `image_url`: a source address of an image (spec entity "Image source URL")

| Field | Type | Rules |
|---|---|---|
| `url` | text PK | Normalized image URL (query kept) |
| `image_id` | bigint FK → `image.id` | Many URLs → one image |
| `first_seen_at` | timestamptz | |

This is the lookup used to skip a known URL before downloading (FR-019).

### `page_image`: an image appears on a page (spec entity "Page–Image link")

| Field | Type | Rules |
|---|---|---|
| `page_id` | bigint FK → `page.id` | |
| `image_id` | bigint FK → `image.id` | |
| `is_lead` | boolean, default false | The page's declared `og:image` / `twitter:image` (FR-006a) |
| `first_seen_at`, `last_seen_at` | timestamptz | |

- The primary key is `(page_id, image_id)`.
- A partial unique index on `(page_id) WHERE is_lead` enforces at most one lead per page.
- Each time a page is crawled, its existing lead mark is cleared first, and the current declared lead is marked once its image resolves ([research R11](research.md#r11-incremental-logic-idempotency-and-atomicity-fr-019--fr-022b)). A lead that changed or disappeared doesn't linger.
- Links are never deleted (append-only; the spec's outstanding "re-crawl freshness" item). Every re-sighting refreshes `last_seen_at`.

### `face`: one detected face (spec entity "Face")

| Field | Type | Rules |
|---|---|---|
| `id` | bigserial PK | |
| `image_id` | bigint FK → `image.id`, `ON DELETE CASCADE` | The image must be `indexed` |
| `face_index` | smallint | 0-based, left to right then top to bottom; unique with `image_id` |
| `x`, `y`, `w`, `h` | integer | Box in normalized-JPEG pixels; `x,y ≥ 0`, `w,h > 0`, `x+w ≤ image.width`, `y+h ≤ image.height` (the last two are checked in code, since a check constraint can't read another table) |
| `confidence` | real | `≥ model_profile.min_face_confidence` |
| `embedding` | `vector(512)` | Not null |
| `model_profile_id` | smallint FK → `model_profile.id` | |

The index on `embedding` is HNSW with `vector_cosine_ops`. It is the search script's main access path ([index-read.md](contracts/index-read.md)).

### `host_pace`: shared request pacing (FR-003, [research R4](research.md#r4-rate-limiting-robotstxt-and-identity-fr-003-fr-004))

| Field | Type | Rules |
|---|---|---|
| `host` | text | Lowercase host name |
| `kind` | text | `page` \| `image` (check constraint) |
| `next_slot` | timestamptz | The earliest start time for the next request to this `(host, kind)` |

The primary key is `(host, kind)`. This is operational state, not index data: it is safe to truncate, and the search script ignores it.

## Image status lifecycle

```text
                (download[site], one transaction with image_url + page_image)
  new content ─┬─> pending ──(detect: ≥1 face, one transaction)──> indexed
               │       └────(detect: 0 faces; jpeg set to NULL)───> no_faces   [dims kept]
               └─> skipped(reason)            [terminal, no bytes, no normalized dims]

  decode error / oversized download / network error → nothing stored, URL retried next run
```

- `pending` is the only state that is not terminal. A failed `detect` leaves the row `pending`, and the next run's `plan_batches` picks it up again (FR-023).
- Terminal states are never re-processed. The URL and fingerprint lookups find them, and only page links are added (FR-019, FR-020, FR-017).
- Search reads only `indexed` images (and through them, faces). So a `pending` image is never seen with a partial set of faces (FR-021).

## Validation rules mapped to requirements

| Requirement | Enforced by |
|---|---|
| FR-008 JPEG ≤ 1024 | `normalize.py`, plus a check constraint on `width`/`height` |
| FR-009 bytes kept, boxes in copy coordinates | `jpeg` iff-rule; box bounds checked in `store.commit_faces` |
| FR-012/016 one row per face | `face (image_id, face_index)` unique; every `DetectedFace` above confidence is inserted |
| FR-010/010a skips and limits | `skip_reason` enum; byte and pixel limits in `http.py` and `normalize.py`; decode errors are not persisted |
| FR-014 no faces → no face rows | status `no_faces`; the confidence filter drops DeepFace's whole-image fallback |
| FR-015 metadata | `image` fields + `image_url` + `page_image` |
| FR-017 no-face images remembered | `no_faces` / `skipped` rows keep `sha256` and their URLs |
| FR-019/020 skip by URL or fingerprint | `image_url.url` PK, `image.sha256` unique |
| FR-021 atomic | transaction boundaries in [research R11](research.md#r11-incremental-logic-idempotency-and-atomicity-fr-019--fr-022b) |
| FR-022b no duplicates | natural-key uniques; `ON CONFLICT` plus a follow-up `SELECT` for the winning ID; `FOR UPDATE SKIP LOCKED` in detect |
| FR-003 shared per-host rate | `host_pace` row per `(host, kind)`, with one atomic reservation per request |
