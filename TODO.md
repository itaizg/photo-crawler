# TODO

## Future resolution

- [ ] **Search script: query images with several faces.** Currently every detected face is searched and results are grouped per face (`--face N` narrows to one). Revisit once real queries have been tried:
  - Do background or blurry faces add noisy result groups? If so, consider a minimum face size or detection-confidence filter.
  - Is an interactive "which face?" prompt (when run in a terminal) worth bringing back?
  - Should `--face largest` be offered as a shortcut?
- [ ] **Search script: noisy faces from the index.** A 50-image spot check of the first full CNN run (2026-11-11) found every box on a face, but some faces are depictions (statues, posters, helmet artwork, silhouettes) and some are tiny background faces (12–16 px). Consider a minimum face size, or ranking by face size, when matching. Renditions of the same photo at different sizes are stored as separate images by design, so results may list the same picture twice under one page.
