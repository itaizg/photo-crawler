# Contract: Reading the Index (for `face_matcher.py`)

This is what the pipeline guarantees to the search script (feature 002). It is what makes SC-009 true: the search queries the stored embeddings with no further processing.

## Guarantees

1. **Same model**: a `model_profile` row exists whose `key` equals `face_index.model_profile.KEY`. Every `face.model_profile_id` points to it. The search script imports the same module, embeds its query with the same settings, and must refuse to run if the row is missing.
2. **Visibility**: only faces whose image has `status = 'indexed'` are complete. `pending` images can exist mid-run and must be excluded. Faces exist only for `indexed` images, so joining through `image` with that filter is enough.
3. **Distance**: cosine. pgvector's `<=>` operator returns the cosine *distance* (`1 − cosine similarity`), which is DeepFace's `cosine` metric directly. The DeepFace threshold for Facenet512 + cosine (0.30) applies unchanged.
4. **Pages**: every page that shows an image is reachable through `page_image`. `is_lead` marks the page whose declared lead image it is.
5. **Read-only**: the search needs only `SELECT`. A read-only role is optional (YAGNI); the shared `.env` connection settings are enough.

## Reference query: all pages with a matching face

```sql
-- %(q)s: query embedding (vector(512)); %(max_dist)s: e.g. 0.30
SELECT p.url,
       min(f.embedding <=> %(q)s)        AS best_distance,
       bool_or(pi.is_lead)               AS is_lead_on_some_match,
       count(DISTINCT i.id)              AS matching_images
FROM face f
JOIN image i       ON i.id = f.image_id AND i.status = 'indexed'
JOIN page_image pi ON pi.image_id = i.id
JOIN page p        ON p.id = pi.page_id
WHERE f.embedding <=> %(q)s < %(max_dist)s
GROUP BY p.url
ORDER BY best_distance;
```

**Note for feature 002**: a `WHERE distance < threshold` filter does not use the HNSW index. At this scale (≤ ~10^5 faces) an exact scan takes under a second and returns *every* match, which suits "all pages". If the scale grows, use `ORDER BY f.embedding <=> q LIMIT k` with `SET hnsw.iterative_scan = relaxed_order`, so the index keeps scanning until enough rows pass the filter. That choice belongs to the search script's plan.

## Fetching a matched image (optional output)

`SELECT jpeg, width, height FROM image WHERE id = %s`, together with `face.x, y, w, h`, gives the exact image and face box. They come from the same coordinate space by construction (FR-009).
