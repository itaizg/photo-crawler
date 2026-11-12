# TODO

## Future resolution

- [ ] **Search script: query images with several faces.** Brief: [multi-face-queries.md](docs/briefs/multi-face-queries.md). Currently every detected face is searched and results are grouped per face (`--face N` narrows to one). Revisit once real queries have been tried:
  - Do background or blurry faces add noisy result groups? If so, consider a minimum face size or detection-confidence filter.
  - Is an interactive "which face?" prompt (when run in a terminal) worth bringing back?
  - Should `--face largest` be offered as a shortcut?
- [ ] **Search script: noisy faces from the index.** Brief: [noisy-index-faces.md](docs/briefs/noisy-index-faces.md). A 50-image spot check of the first full CNN run (2026-11-11) found every box on a face, but some faces are depictions (statues, posters, helmet artwork, silhouettes) and some are tiny background faces (12–16 px). Consider a minimum face size, or ranking by face size, when matching. Renditions of the same photo at different sizes are stored as separate images by design, so results may list the same picture twice under one page.

## Open decisions (from the Sourcery review, 2026-11-11)

- [ ] **Model upgrades hide existing faces.** Brief: [reindex-on-model-upgrade.md](docs/briefs/reindex-on-model-upgrade.md). A DeepFace or TensorFlow version change gives a new model-profile key; searches filter by the current profile, so faces stored under the old profile stop matching, and nothing re-embeds them. Options: a re-index step that sets `indexed` images back to `pending` for the new profile (the JPEG bytes are kept), or a documented "rebuild the index" step before searching.
- [ ] **Pages keep links to images they no longer show.** Brief: [stale-page-links.md](docs/briefs/stale-page-links.md). Download adds and refreshes links (`page_image.last_seen_at`) but never removes one. Options: delete links of a re-crawled page that were not seen this run, or have search ignore links older than the page's `last_crawled_at`.
