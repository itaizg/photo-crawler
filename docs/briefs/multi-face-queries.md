# Mission Brief — Query Images With Several Faces

Source of truth: [assignment.md](../assignment.md). Quoted text is from the assignment; anything else is interpretation. Origin: [TODO.md](../../TODO.md), "Search script: query images with several faces". This refines the [search script brief](../search-script-brief.md) and lands with or after feature 002.

## Mission

> "allow the user to provide an image of a specific character"

The assignment speaks of one character, but real query photos often show several people. The current decision is to search every detected face, group the results per face, and let `--face N` narrow the search to one. This mission tests that decision against real queries and settles the three open points.

## Goals

1. **Measure the noise**: run the script on a set of real multi-face query photos and record whether background or blurry faces add result groups the user doesn't want.
2. **Filter if needed**: if they do, add a minimum face size or detection-confidence threshold for query faces, with a flag to change it.
3. **Decide on shortcuts**: decide whether `--face largest` and an interactive "which face?" prompt (only when run in a terminal) are worth adding.

## Constraints

- **Same model**: query faces go through the same `model_profile` settings as indexed faces, including the 0.9 confidence floor.
- **Scriptable**: the default must work without a terminal (no prompt when piped or run in CI).
- **High confidence**: a filter may drop query faces, never relax the match threshold.
- **Documented**: README's "How to use the face matching script" covers the behavior and flags.

## Non-Goals

- Identifying who each face is.
- Filtering faces in the index. That is [noisy-index-faces.md](noisy-index-faces.md).
- A GUI for picking faces.

## Success Criteria

1. On the test set, every result group belongs to a face a person would call the subject, or the README explains why it does not.
2. A single-face query gives the same output as before.
3. `--list-faces` numbering stays stable: left to right by bounding box.
4. The chosen behavior is recorded in the search script brief's Decisions section, and the TODO item is closed.

## Open Questions

- **Threshold**: minimum face size in pixels, a fraction of the image, or detection confidence?
- **Default**: all faces (current), or only the largest face, with `--all-faces` to opt in?
- **Prompt**: worth the extra code path, or is `--list-faces` then `--face N` enough?
