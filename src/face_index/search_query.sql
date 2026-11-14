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
