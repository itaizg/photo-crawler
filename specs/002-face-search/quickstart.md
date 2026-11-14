# Quickstart: Validating the Face Search Script

How to prove feature 002 works end to end. The command line and output formats are defined in [contracts/face-matcher-cli.md](contracts/face-matcher-cli.md), and the query in [contracts/search-query.md](contracts/search-query.md). This guide only runs them.

## 1. Prerequisites

- The stack from feature 001 is running (`docker compose up -d --build`), and at least one `image_indexer` run has completed, so the index has faces.
- Query photos go in `queries/` at the project root. That folder is shared with the container, and photos in it are git-ignored. Use photos you are licensed to use, and not copies of images taken from the indexed pages, so that a match proves recognition rather than duplicate detection.
  - `queries/one.jpg`: one clearly visible person who appears on the indexed CNN pages.
  - `queries/group.jpg`: two or more people, at least one of them indexed.
  - `queries/absent.jpg`: one person who is not on the site.
- No face in `queries/` is needed for the offline tests in §6.

## 2. One-command search (User Story 1)

```bash
docker compose run --rm face-matcher queries/one.jpg
```

Expected:
- One group `# face 1 …: N pages`, followed by N page URLs, each once, closest first.
- Exit status 0 (`echo $?`).
- Every listed page shows the person when opened (SC-002).

```bash
docker compose run --rm face-matcher queries/one.jpg --details
```

Expected:
- Every distance is below `0.300`.
- Pages whose lead image matched carry `lead`.
- Each page lists its matching image URLs.

## 3. High confidence (User Story 2)

| Command | Expected |
|---|---|
| `docker compose run --rm face-matcher queries/absent.jpg` | `# face 1 …: no matching pages`, exit 0 (SC-003) |
| `… queries/one.jpg --threshold 0.2` | A subset of the default result (acceptance scenario 2.3) |
| `… queries/one.jpg --threshold 3` | Usage error naming the range (0, 2], exit 64, no model load |

## 4. Several faces (User Story 3) and timing (SC-006)

```bash
time docker compose run --rm face-matcher queries/group.jpg
```

Expected: one group per face (`face 1`, `face 2`, …, numbered left to right), and a real time under 30 s. Note the index size alongside the time (SC-006):

```bash
docker compose exec postgres psql -U faces -d faces -Atc "SELECT (SELECT count(*) FROM face), (SELECT count(*) FROM image WHERE status = 'indexed'), (SELECT count(*) FROM page)"
```

```bash
docker compose run --rm face-matcher queries/group.jpg --list-faces
```

```bash
ls -l queries/group.faces.jpg
```

Expected:
- One line per face with its box, then `# saved queries/group.faces.jpg`. The file exists on the host after the container is gone, and the numbers drawn on it match the printed list.
- Running the command again overwrites the copy (same file, new modification time).

```bash
docker compose run --rm face-matcher queries/group.jpg --face 2
```

Expected: only the `# face 2` group. `--face 9` gives `error: --face 9, but only K faces were found`, exit 64.

## 5. Error and edge cases (SC-005)

Each case should print one clear `error:` line with no traceback.

| Setup | Expected exit |
|---|---|
| `queries/missing.jpg` (no such file) | 2, with the hint to put photos in `queries/` |
| A text file renamed to `queries/bad.jpg` | 2, "not a decodable image" |
| A 40×40 px image | 2, "at least 64 px on the shortest side" |
| A landscape photo with no people | 3, "no face detected" |
| `docker compose stop postgres`, then search | 1, "database unavailable" (restart with `docker compose start postgres`) |
| A fresh, empty `faces` database (or a test database without a pipeline run) | 4, "run the image_indexer pipeline first" |
| `chmod a-w queries`, then `--list-faces` | Face list printed, then 5 (restore with `chmod u+w queries`) |

Read-only check (SC-007): record the counts, run a few searches, then compare:

```bash
docker compose exec postgres psql -U faces -d faces -Atc "SELECT (SELECT count(*) FROM page), (SELECT count(*) FROM image), (SELECT count(*) FROM page_image), (SELECT count(*) FROM face), (SELECT max(last_seen_at) FROM page_image)"
```

The output must be identical before and after the searches.

## 6. Automated tests

```bash
uv run pytest tests/unit/test_search.py tests/unit/test_face_matcher_cli.py tests/unit/test_drawing.py
```

```bash
docker compose up -d postgres && uv run pytest tests/integration/test_search_store.py tests/integration/test_index_read.py
```

```bash
uv run pytest -m model tests/smoke/test_face_matcher_smoke.py tests/unit/test_model_profile.py
```

- The unit tests use the fake embedder and the in-memory index, with no database or model.
- The integration tests use loopback Postgres and the SQL loaded from the contract files.
- The `model` tests use the real weights, and the face-photo cases need `tests/fixtures/images/face_one.jpg` / `face_two.jpg`.

## 7. Accuracy evaluation (SC-001 to SC-003)

1. Build `queries/eval/labels.toml` as described in [research R8](research.md#r8-measuring-sc-001-to-sc-003):
   - at least 5 people on the site, each with a photo and their labelled page URLs;
   - at least 5 absent people.
2. Run the evaluation from the host. It is a development tool, so it is not baked into the image. It uses the same `face_index` code and the same model profile, as long as the host's `uv.lock` versions match the image, which the profile check enforces:

   ```bash
   FACE_INDEX_DSN=postgresql://faces@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py queries/eval/labels.toml
   ```
3. Expected: a per-person table of expected, returned, recall, extra pages and the missed URLs, then `PASS` with exit 0 when every recall is ≥ 0.80, no extra pages were returned, and no absent person matched.

The numbers, with the labelling method and its bias, go into the README.

## 8. Final check (constitution, Quality Gates)

Rebuild and start the full stack (`docker compose up -d --build`), trigger `image_indexer`, and wait for the run to succeed. Then run §2 to §5 on the index that run produced, and paste the real outputs of §2, §3 (absent) and §4 (`--list-faces`) into the README's "Example usage and expected outputs".

## Requirement coverage

| Requirement | Verified by |
|---|---|
| FR-001, FR-016 | §2 (Compose command); unit test asserts no `airflow` import |
| FR-002, FR-003 | §2; smoke test with the real model; unit test that the query goes through the normalizer |
| FR-004, FR-005, FR-005a | `test_search_store.py`: threshold boundary, other profile ignored, `pending` image ignored |
| FR-006, FR-006a, FR-007 | `test_search_store.py`: image on several pages, page with two matching images, outdated link ignored, ordering |
| FR-008 | CLI unit test of `--details` formatting; §2 |
| FR-009 | CLI unit test with an empty result; §3 |
| FR-010, FR-011 | `test_search.py` numbering and selection; §4 |
| FR-012 | CLI unit test with `tmp_path`: file name, overwrite, printed path; §4 |
| FR-013 | `test_search_store.py`: a write in the adapter's session raises `ReadOnlySqlTransaction`; §5 counts |
| FR-014, FR-015 | One CLI unit test per exit status; §5 |
| FR-017 | README review in §8 |
| FR-018 | `test_pipeline_failures.py` (log line, summary row) and `test_store_index.py` (count, no deletes); the `summarize` task log of the §8 run shows the "outdated page links" row |
| SC-001 to SC-003 | §7 |
| SC-004 | `test_search_store.py` and CLI test (no duplicate URL in a group) |
| SC-005 | §5 and the exit-status tests |
| SC-006 | §4 `time` |
| SC-007 | §5 counts and the read-only test |
