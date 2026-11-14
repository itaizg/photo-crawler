# Contract: The Search Query

This is the one query `PostgresSearchIndex.matching_pages` runs for each searched face. The adapter reads it from `src/face_index/search_query.sql`, the single source. The Docker image excludes `specs/`, so it can't read this file. `tests/integration/test_search_store.py` asserts that the SQL block below equals that file, so this document and the code can't drift apart. `index-schema.sql` and `test_schema.py` follow the same pattern.

The session is read-only (`SET default_transaction_read_only = on`, research R5).

```sql
-- %(q)s: query embedding, vector(512)
-- %(max_dist)s: threshold; a face matches when its cosine distance is strictly below it
-- %(profile_id)s: model_profile.id whose key equals face_index.model_profile.KEY
WITH matches AS (
    SELECT f.image_id,
           min(f.embedding <=> %(q)s) AS distance
    FROM face f
    JOIN image i ON i.id = f.image_id AND i.status = 'indexed'
    WHERE f.model_profile_id = %(profile_id)s
      AND f.embedding <=> %(q)s < %(max_dist)s
    GROUP BY f.image_id
)
SELECT p.url,
       min(m.distance)                                  AS best_distance,
       bool_or(pi.is_lead)                              AS lead,
       array_agg(u.url ORDER BY m.distance, u.url)
           FILTER (WHERE u.url IS NOT NULL)             AS image_urls
FROM matches m
JOIN page_image pi ON pi.image_id = m.image_id
JOIN page p        ON p.id = pi.page_id
                  AND pi.last_seen_at >= p.last_crawled_at
LEFT JOIN LATERAL (
    SELECT iu.url
    FROM image_url iu
    WHERE iu.image_id = m.image_id
    ORDER BY iu.first_seen_at, iu.url
    LIMIT 1
) u ON true
GROUP BY p.url
ORDER BY best_distance, p.url;
```

## What each clause guarantees

| Clause | Requirement | Note |
|---|---|---|
| `f.model_profile_id = …` | FR-003, FR-005a | Faces from an older model runtime are never compared (read contract guarantee 1) |
| `i.status = 'indexed'` | FR-005a | Images still mid-run (`pending`) are invisible (guarantee 2) |
| `<=> … < %(max_dist)s` | FR-005 | Strict comparison. No `ORDER BY … LIMIT` on `face`, so this is an exact scan and returns every match (research R3) |
| `GROUP BY f.image_id` in `matches` | FR-007 | An image with several matching faces counts once, with its closest face |
| `pi.last_seen_at >= p.last_crawled_at` | FR-006a | A link the page's latest crawl didn't refresh is ignored (research R4) |
| `GROUP BY p.url` | FR-007, SC-004 | Each page once per searched face |
| `bool_or(pi.is_lead)` | FR-008 | Lead mark for `--details` |
| lateral `image_url … LIMIT 1` | FR-008 | One deterministic address per matching image (research R9) |
| `ORDER BY best_distance, p.url` | FR-007 | Closest first, with a stable tie order for tests |

## Companion queries

These are run once per invocation, before any search:

```text
profile_id:          SELECT id FROM model_profile WHERE key = %s
indexed_face_count:  SELECT count(*) FROM face f JOIN image i ON i.id = f.image_id
                     WHERE f.model_profile_id = %s AND i.status = 'indexed'
```

If the `model_profile`, `face` or `image` table is missing, Postgres raises `UndefinedTable`. The adapter maps it to `IndexNotReady`, and the CLI exits with status 4.

## Change to the pipeline's read contract

[index-read.md](../../001-image-indexer/contracts/index-read.md) gains:

> 6. **Link freshness**: `upsert_page` sets `page.last_crawled_at` before the download step links that page's images. Every link the step refreshes therefore has `last_seen_at >= last_crawled_at`, both from the database clock. A link not seen in the page's latest successful crawl is strictly older. A page whose fetch failed keeps its previous `last_crawled_at`.

Its reference query gains the same `AND pi.last_seen_at >= p.last_crawled_at` join condition, and `test_index_read.py` gains an outdated-link case.
