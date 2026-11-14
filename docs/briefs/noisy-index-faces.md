# Mission Brief — Noisy Faces in the Index

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [TODO.md](../../TODO.md), "Search script: noisy faces from the index". Lands with or after feature 002.

## Mission

> "Returns the pages containing images of the same character with high confidence."

A 50-image spot check of the first full CNN run (2026-11-11) found every box on a face, but not every face is useful. Some are depictions (statues, posters, helmet artwork, silhouettes); some are tiny background faces of 12 to 16 px, whose embeddings are unreliable. Also, renditions of one photo at different sizes are stored as separate images by design, so one page can list the same picture twice. This mission decides how search keeps those from hurting precision.

## Goals

1. **Measure**: from the live index, report the distribution of face sizes, and how often small faces show up in top matches for known queries.
2. **Filter or rank**: drop matches below a minimum face size, or rank results by face size, whichever measurement supports. Expose it as a search flag with a documented default.
3. **Collapse renditions**: in result output, list one page once even when several renditions of the same photo matched. This is already a success criterion of the search brief; confirm it holds.

## Constraints

- **Search side first**: the index stays complete (the assignment asks to store "face bounding boxes, and face embeddings" for all detected faces). Filtering happens at query time unless measurement shows an index-time filter is clearly better. If so, that change goes through the pipeline spec.
- **Box sizes are in normalized-image pixels** (longest side at most 1024), so a threshold means the same thing for every image.
- **Read contract**: any new filter uses columns the read contract already exposes (`face.w`, `face.h`, `face.confidence`).

## Non-Goals

- Classifying real faces versus depictions (statues, posters). No reliable pre-trained classifier is in scope; if size filtering doesn't help with these, document them as a known limitation.
- De-duplicating renditions in the index.

## Success Criteria

1. For a set of known queries, the chosen filter removes false matches without dropping true pages, and the numbers are recorded in the README.
2. A page appears once in the output, however many of its images matched.
3. The flag and its default are documented, and the TODO item is closed.

## Open Questions

- **Threshold**: what minimum face size? 40 px is a common floor for Facenet-style models; the measurement should confirm or replace it.
- **Filter or rank**: drop small faces, or keep them and sort them last?
- **Shared with query side**: should [multi-face-queries.md](multi-face-queries.md) use the same threshold for query faces?
