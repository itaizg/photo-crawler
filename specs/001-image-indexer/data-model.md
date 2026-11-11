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
| `key` | text, unique | Stable identity, e.g. `retinaface+Facenet512+cosine+align+base`. Built by `model_profile.py`. |
| `detector` | text | `retinaface` |
| `model` | text | `Facenet512` |
| `dimensions` | smallint | `512`; must equal the `face.embedding` dimension |
| `distance` | text | `cosine` |
| `align` | boolean | `true` |
| `normalization` | text | `base` |
| `min_face_confidence` | real | `0.9` |
| `deepface_version` | text | Recorded for traceability; not part of `key`, since a patch upgrade doesn't change embeddings |
| `created_at` | timestamptz | |

It is upserted by `init_schema`. The search script reads the row for its own `key` and refuses to run if it's missing, which proves both sides use the same model.

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
| `skip_reason` | `skip_reason` enum, null | `unsupported_format` \| `undecodable` \| `too_small`; non-null **iff** `status = skipped` |
| `original_format` | text, null | Pillow format name or content type (`JPEG`, `PNG`, `WEBP`, `GIF`, `image/svg+xml`, …) |
| `original_width`, `original_height` | integer, null | Null when the image couldn't be decoded |
| `width`, `height` | integer, null | Normalized JPEG dimensions; `max(width, height) ≤ 1024`; null unless status ∈ {pending, indexed} |
| `jpeg` | bytea, null | Normalized JPEG bytes; non-null **iff** status ∈ {pending, indexed} (clarification Q3) |
| `created_at` | timestamptz | |
| `indexed_at` | timestamptz, null | Set when the status becomes `indexed` or `no_faces` |

Check constraints enforce the "iff" rules and the 1024 bound. That makes SC-003 a database invariant, not just a test.

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
- When a page's declared lead changes between runs, the same transaction clears the old lead mark before setting the new one.
- Links are never deleted (append-only; the spec's outstanding "re-crawl freshness" item). `last_seen_at` records when a link was last seen.

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

## Image status lifecycle

```text
                (download[site], one transaction with image_url + page_image)
  new content ─┬─> pending ──(detect: ≥1 face, one transaction)──> indexed
               │       └────(detect: 0 faces; jpeg set to NULL)───> no_faces
               └─> skipped(reason)            [terminal, no bytes]
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
| FR-014 no faces → no face rows | status `no_faces`; the confidence filter drops DeepFace's whole-image fallback |
| FR-015 metadata | `image` fields + `image_url` + `page_image` |
| FR-017 no-face images remembered | `no_faces` / `skipped` rows keep `sha256` and their URLs |
| FR-019/020 skip by URL or fingerprint | `image_url.url` PK, `image.sha256` unique |
| FR-021 atomic | transaction boundaries in [research R11](research.md#r11-incremental-logic-idempotency-and-atomicity-fr-019--fr-022b) |
| FR-022b no duplicates | natural-key uniques + `ON CONFLICT DO NOTHING`; `FOR UPDATE SKIP LOCKED` in detect |
