# Data Model: Face Search Script

The search adds **no tables and no columns**. It reads the pipeline's schema ([index-schema.sql](../001-image-indexer/contracts/index-schema.sql)) and holds a few in-memory values while it runs. Nothing it computes is stored.

## Stored data it reads (read-only)

| Table | Columns used | Rule applied by the search |
|---|---|---|
| `model_profile` | `id`, `key` | The row whose `key = model_profile.KEY` must exist, or the script exits with status 4 (R5) |
| `face` | `image_id`, `embedding`, `model_profile_id` | Only `model_profile_id` = that row's id (FR-005a) |
| `image` | `id`, `status` | Only `status = 'indexed'` (FR-005a, read contract guarantee 2) |
| `page_image` | `page_id`, `image_id`, `is_lead`, `last_seen_at` | Only links with `last_seen_at >= page.last_crawled_at` (FR-006a, R4) |
| `page` | `id`, `url`, `last_crawled_at` | The returned address is `url` |
| `image_url` | `image_id`, `url`, `first_seen_at` | One URL per image for `--details`: earliest `first_seen_at`, ties broken by `url` (R9) |

## In-memory values

All of these are frozen dataclasses in `face_index.models`, typed for ty (constitution II).

### QueryFace

One face found in the query photo. It is built from the `DetectedFace` values that `DeepFaceEmbedder.embed` returns.

| Field | Type | Rule |
|---|---|---|
| `number` | `int` | 1-based position in the embedder's order: left to right by `box.x`, then top to bottom by `box.y` (FR-010) |
| `box` | `Box` (x, y, w, h) | In normalized-query-image pixels (≤ 1024 px side) |
| `embedding` | `tuple[float, ...]` | Length `model_profile.DIMENSIONS` (512) |

Detection confidence is not carried: faces under the 0.9 floor were already dropped by `narrow_results`.

### PageMatch

One row of the [search query](contracts/search-query.md), narrowed from the database row inside `PostgresSearchIndex`.

| Field | Type | Rule |
|---|---|---|
| `url` | `str` | Unique within one face's result (FR-007) |
| `best_distance` | `float` | `0 ≤ best_distance < threshold`: the smallest cosine distance among the page's matching faces |
| `lead` | `bool` | True if any matching image is the page's lead (`is_lead`) |
| `image_urls` | `tuple[str, ...]` | One URL per matching image on the page, closest first. At least one |

### FaceResult

The result for one searched face.

| Field | Type | Rule |
|---|---|---|
| `face` | `QueryFace` | |
| `pages` | `tuple[PageMatch, ...]` | Ordered by `best_distance`, then `url`. Empty means "no matching pages" (FR-009) |

### SearchRequest

The validated command line, before the model loads.

| Field | Type | Rule |
|---|---|---|
| `image_path` | `Path` | Must exist and be readable, or exit status 2 |
| `threshold` | `float` | Default `model_profile.MATCH_THRESHOLD` (0.30). Must be in (0, 2], or exit status 64 (FR-005, R2) |
| `face_number` | `int \| None` | ≥ 1 when given. Checked against the face count after detection, or exit status 64 (FR-011) |
| `list_faces` | `bool` | Not allowed together with `face_number`, `details` or an explicit `threshold` (usage error) |
| `details` | `bool` | Shows distance, lead mark and image URLs (FR-008) |

## Protocol seam

Added to `ports.py`. `search.py` depends only on this and on `FaceEmbedder` / `ImageNormalizer`, which already exist.

```python
class SearchIndex(Protocol):
    """Read-only access to the face index. Implementations raise only InfrastructureError
    (database unreachable) or IndexNotReady (schema or profile row missing)."""

    def profile_id(self, key: str) -> int | None: ...
    def indexed_face_count(self, profile_id: int) -> int: ...
    def matching_pages(
        self, embedding: Sequence[float], max_distance: float, profile_id: int
    ) -> list[PageMatch]: ...
```

`IndexNotReady` is a new exception in `errors.py`. The CLI maps it to exit status 4.

## Annotated copy (file, not data)

`--list-faces` writes `<stem>.faces.jpg` in the query photo's folder. It is the normalized query JPEG with each `QueryFace.box` outlined and labelled with its `number`, drawn by the shared `drawing.draw_boxes` helper. Its size is at most 1024 px on the longest side, it overwrites any earlier copy, and it is never read back by the script.

## State transitions

None. The search is a single pass:

**validate args → connect and check profile → load and normalize photo → detect faces → (list and draw) or (search each selected face → print)**

Each arrow can end the run with the exit status in [contracts/face-matcher-cli.md](contracts/face-matcher-cli.md#exit-statuses).
