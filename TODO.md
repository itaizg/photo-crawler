# TODO

## Future resolution

- [ ] **Submission review findings (PR #16 graded 73/100).** Brief: [submission-review.md](docs/briefs/submission-review.md); spec: [004-submission-review-fixes](specs/004-submission-review-fixes/spec.md). One item per goal, closed as each lands:
  - [ ] 1. The search uses its HNSW index (nearest neighbours first, growing batch, exact fallback).
  - [ ] 2. ArcFace's and the baseline's numbers reported separately, recall at zero false matches first.
  - [ ] 3. ArcFace re-run with its own input scaling (added row, for information).
  - [ ] 4. Query expansion skips renditions; six rows re-scored as corrections.
  - [ ] 5. Label bias documented, second-set method fixed, blind audit of the 2026-11-16 set.
  - [ ] 6. ArcFace as a selectable profile (revised once PR #17 lands).
  - [ ] 7. README under 150 lines, no internal identifiers, current dated outputs, coverage stated.
  - [ ] 8. A reviewer's map of where each deliverable lives.
  - [ ] 9. Secret files no longer world-readable.
  - [ ] 10. pgvector image pinned in Compose and CI.
  - [ ] 11. No default page cap; first default run measured.
- [ ] **Search script: query images with several faces.** Brief: [multi-face-queries.md](docs/briefs/multi-face-queries.md). Currently every detected face is searched and results are grouped per face (`--face N` narrows to one). Revisit once real queries have been tried:
  - Do background or blurry faces add noisy result groups? If so, consider a minimum face size or detection-confidence filter.
  - Is an interactive "which face?" prompt (when run in a terminal) worth bringing back?
  - Should `--face largest` be offered as a shortcut?
- [ ] **Search script: noisy faces from the index.** Brief: [noisy-index-faces.md](docs/briefs/noisy-index-faces.md). A 50-image spot check of the first full CNN run (2026-11-11) found every box on a face, but some faces are depictions (statues, posters, helmet artwork, silhouettes) and some are tiny background faces (12–16 px). Consider a minimum face size, or ranking by face size, when matching. Renditions of the same photo at different sizes are stored as separate images by design, so results may list the same picture twice under one page.
- [ ] **Search accuracy fails SC-001 and SC-003 on the first label set.** Results: [search-evaluation.md](docs/search-evaluation.md#results). On the corrected 2026-11-16 set, 2 of 5 people are found on none of their pages, and 2 of 5 absent people match 4 pages, while no present person gets an extra page. True matches reach a distance of 0.392 and wrong ones start at 0.233, so no threshold passes. A `--min-face PX` size filter was tried at the pre-chosen 40 px: the same 4 false matches remain (they are clear faces) and person-c loses a true page, so it stays off by default ([search-evaluation.md](docs/search-evaluation.md#face-size-filter-tried-at-40-px-not-adopted)). The offline experiments ([search-experiments.md](docs/briefs/search-experiments.md), [search-evaluation.md](docs/search-evaluation.md#experiments)) found that re-scoring (CSLS, T-norm, query expansion) does not help and that ArcFace with raw cosine is the one combination worth confirming on a second label set (recall at zero false matches 0.88, 14 of 16 true pages, against Facenet512's 0.62, 10 of 16; neither passes at any single threshold). No default changes yet. Do not tune the threshold or the face size to pass.
- [x] **Label a second crawl as a held-out set.** Done: set 2026-11-17 (10 present, 10 absent). The ArcFace candidate was not confirmed on it (AUC 0.912 against 0.913, recall at zero false matches 0.28 for both), so the default stays Facenet512 ([search-evaluation.md](docs/search-evaluation.md#confirmation-on-the-2026-11-17-set-not-confirmed)).
- [ ] **Search: several query photos per person.** Out of scope for the first experiments; needs 2 more openly licensed photos per label.

## Open decisions (from the Sourcery review, 2026-11-11)

- [ ] **Model upgrades hide existing faces.** Brief: [reindex-on-model-upgrade.md](docs/briefs/reindex-on-model-upgrade.md). A DeepFace or TensorFlow version change gives a new model-profile key; searches filter by the current profile, so faces stored under the old profile stop matching, and nothing re-embeds them. Options: a re-index step that sets `indexed` images back to `pending` for the new profile (the JPEG bytes are kept), or a documented "rebuild the index" step before searching.
- [x] **Pages keep links to images they no longer show.** Brief: [stale-page-links.md](docs/briefs/stale-page-links.md). Resolved in feature 002: the search ignores links older than the page's `last_crawled_at` (FR-006a), and the pipeline reports them in the download task log and the run summary (FR-018). They are kept, not deleted. Deleting them stays an open option if storage ever matters.
