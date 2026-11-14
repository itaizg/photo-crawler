# Contract: `face_matcher` Command Line

The interface the searcher uses, and what tests assert. Implemented in `src/face_index/face_matcher.py`.

## Invocation

```text
docker compose run --rm face-matcher IMAGE [--threshold T] [--face N] [--details]
docker compose run --rm face-matcher IMAGE --list-faces

# Same module from the host, against the Compose database on 127.0.0.1:
FACE_INDEX_DSN=postgresql://faces@127.0.0.1:5432/faces \
  uv run python -m face_index.face_matcher IMAGE [options]
```

`IMAGE` is a path to the query photo. In the container, the working directory mirrors the project root and only `queries/` is mounted, so the photo must be under `queries/` (for example `queries/alice.jpg`). A path that does not exist gets exit status 2, with a hint to put the photo in `queries/`.

## Options

| Option | Default | Meaning | Validation |
|---|---|---|---|
| `--threshold T` | `0.30` (`model_profile.MATCH_THRESHOLD`) | A face matches when its cosine distance is `< T` | Float in (0, 2], or exit 64 |
| `--face N` | all faces | Search only face `N` | Integer ≥ 1. If the photo has fewer faces: exit 64, message states how many were found |
| `--details` | off | Adds distance, lead mark and matching image URLs | – |
| `--list-faces` | off | Print faces and write the annotated copy. No search | Not allowed together with `--face`, `--details` or `--threshold` (exit 64) |
| `-h, --help` | | Usage, defaults and exit statuses | – |

All argument checks run before the database is contacted or the model is loaded.

## Output streams

- **stdout**: results only, UTF-8, `\n` line endings.
- **stderr**: progress (`loading face model…`), notes and errors. Never the DSN.

### Search, default

One group per searched face, in face-number order, with a blank line between groups. The header starts with `# ` and the boxes are in normalized-image pixels.

```text
# face 1 x=212 y=96 w=118 h=151: 3 pages
https://edition.cnn.com/2026/11/10/politics/example-a
https://edition.cnn.com/politics
https://edition.cnn.com/

# face 2 x=530 y=120 w=104 h=133: no matching pages
```

The header says `1 page` for a single page and `N pages` otherwise. The URLs of a group are ordered by best distance, closest first, then by URL. Each URL appears once per group. `grep -v '^#'` gives a bare URL list.

### Search, `--details`

```text
# face 1 x=212 y=96 w=118 h=151: 3 pages
0.112 lead https://edition.cnn.com/2026/11/10/politics/example-a
    image: https://media.cnn.com/api/v1/images/stellar/prod/a.jpg?c=16x9&q=h_810,w_1440
0.187      https://edition.cnn.com/politics
    image: https://media.cnn.com/api/v1/images/stellar/prod/a.jpg?c=16x9&q=h_270,w_480
    image: https://media.cnn.com/api/v1/images/stellar/prod/b.jpg?c=1x1&q=h_768,w_768
0.241      https://edition.cnn.com/
    image: https://media.cnn.com/api/v1/images/stellar/prod/c.jpg?c=3x2&q=h_768,w_1152
```

- The distance has 3 decimals.
- `lead` appears when any matching image is the page's declared lead. Otherwise the column is 4 spaces, so the URLs align.
- There is one `image:` line per matching image, closest first. Several renditions of one photo may each appear (spec assumptions).

### `--list-faces`

```text
# 2 faces in queries/group.jpg (normalized to 1024x683)
1 x=212 y=96 w=118 h=151
2 x=530 y=120 w=104 h=133
# saved queries/group.faces.jpg
```

The first line says `1 face` for a single face. The annotated copy is written next to `IMAGE` as `<stem>.faces.jpg`, overwriting any earlier one. The printed path is relative to the working directory, so it is the same on the host and in the container. The file persists after the container exits, because `queries/` is a bind mount.

## Exit statuses

| Status | Name | When | Example stderr |
|---|---|---|---|
| 0 | OK | Search done, including groups with no matching pages. Faces listed and the copy saved | – |
| 1 | UNAVAILABLE | Database unreachable or timed out. Face model failed to load | `error: database unavailable: OperationalError` |
| 2 | BAD_IMAGE | File missing or unreadable, not a decodable image, SVG, shortest side < 64 px, > 50 MP | `error: queries/x.png: image is 40x40; at least 64 px on the shortest side is needed` |
| 3 | NO_FACE | No face detected in the query (after normalization) | `error: no face detected in queries/x.jpg` |
| 4 | INDEX_NOT_READY | Schema missing, no `model_profile` row for the current key, or zero indexed faces for it | `error: the index has no faces for profile retinaface+Facenet512+…; run the image_indexer pipeline first` |
| 5 | CANNOT_WRITE | `--list-faces` could not write the copy. The face list is still printed first | `error: cannot write queries/group.faces.jpg: Permission denied` |
| 64 | USAGE | Bad arguments, threshold out of range, conflicting options, `--face N` beyond the face count | `error: --face 3, but only 2 faces were found` |

Statuses 1 to 5 never come with a Python traceback. An unexpected exception is reported as `error: unexpected <Type>: <message>` with status 1, and the traceback appears only when `FACE_MATCHER_DEBUG=1`.

## Guarantees

- **No writes**: no database writes. The session is read-only (research R5).
- **No hidden files**: no file other than `<stem>.faces.jpg`, and that only with `--list-faces`.
- **No Airflow**: no import of `airflow` (asserted by a test).
- **Deterministic**: the same index and the same photo give the same output.
