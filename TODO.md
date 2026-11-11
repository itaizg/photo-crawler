# TODO

## Future resolution

- [ ] **Search script: query images with several faces.** Currently every detected face is searched and results are grouped per face (`--face N` narrows to one). Revisit once real queries have been tried:
  - Do background or blurry faces add noisy result groups? If so, consider a minimum face size or detection-confidence filter.
  - Is an interactive "which face?" prompt (when run in a terminal) worth bringing back?
  - Should `--face largest` be offered as a shortcut?
