# TODO

## Future resolution

- [ ] **Search script: query images with several faces.** Brief: [multi-face-queries.md](docs/briefs/multi-face-queries.md). Currently every detected face is searched and results are grouped per face (`--face N` narrows to one). Revisit once real queries have been tried:
  - Do background or blurry faces add noisy result groups? If so, consider a minimum face size or detection-confidence filter.
  - Is an interactive "which face?" prompt (when run in a terminal) worth bringing back?
  - Should `--face largest` be offered as a shortcut?
- [ ] **Search script: noisy faces from the index.** Brief: [noisy-index-faces.md](docs/briefs/noisy-index-faces.md). A 50-image spot check of the first full CNN run (2026-11-11) found every box on a face, but some faces are depictions (statues, posters, helmet artwork, silhouettes) and some are tiny background faces (12–16 px). Consider a minimum face size, or ranking by face size, when matching. Renditions of the same photo at different sizes are stored as separate images by design, so results may list the same picture twice under one page.
- [ ] **Search accuracy fails SC-001 and SC-003 on the first label set.** Results: README, [Search accuracy evaluation](README.md#results). On the corrected 2026-11-16 set, 2 of 5 people are found on none of their pages, and 2 of 5 absent people match 4 pages, while no present person gets an extra page. True matches reach a distance of 0.392 and wrong ones start at 0.233, so no threshold passes. A `--min-face PX` size filter now exists (default 0, off); the next step is a logged run at the pre-chosen 40 px, then a second label set before changing any default ([noisy-index-faces.md](docs/briefs/noisy-index-faces.md)). Do not tune the threshold or the face size to pass.

## Open decisions (from the Sourcery review, 2026-11-11)

- [ ] **Model upgrades hide existing faces.** Brief: [reindex-on-model-upgrade.md](docs/briefs/reindex-on-model-upgrade.md). A DeepFace or TensorFlow version change gives a new model-profile key; searches filter by the current profile, so faces stored under the old profile stop matching, and nothing re-embeds them. Options: a re-index step that sets `indexed` images back to `pending` for the new profile (the JPEG bytes are kept), or a documented "rebuild the index" step before searching.
- [x] **Pages keep links to images they no longer show.** Brief: [stale-page-links.md](docs/briefs/stale-page-links.md). Resolved in feature 002: the search ignores links older than the page's `last_crawled_at` (FR-006a), and the pipeline reports them in the download task log and the run summary (FR-018). They are kept, not deleted. Deleting them stays an open option if storage ever matters.
