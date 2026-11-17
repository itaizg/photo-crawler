# photo-crawler

System for crawling a website. It extracts images containing people with visible faces, and indexes them for image-based search. The system allows the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character.

This repository contains the **image indexer** (the Airflow pipeline, feature 001) and the **face matching script** (`face_index.face_matcher`, feature 002). Specs, plans and contracts are under `specs/`.

## How to run the pipeline

Requirements: Docker with Compose v2 and at least 8 GB of memory for Docker.

```bash
docker compose up -d --build --wait
```

That one command builds the Airflow image (Python 3.13, DeepFace, model weights cached at build time), and starts Postgres with pgvector, the Airflow API server, scheduler and DAG processor. No `.env` is needed. The UI is at http://localhost:8080.

Trigger a run with the defaults (depth 1, at most 50 pages per site, 1 page request/s and 5 image requests/s per host):

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer
```

Or pass your own settings (all optional):

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"depth": 1, "page_cap": null, "page_rate": 1.0, "image_rate": 5.0}'
```

| Param | Default | Meaning |
|---|---|---|
| `depth` | 1 | Link hops from each home page (0–10); 0 = home pages only |
| `page_cap` | 50 | Maximum page requests per site (failed and non-HTML ones count too); `null` = no cap |
| `page_rate` | 1.0 | Page requests per second, per host (0.1–10) |
| `image_rate` | 5.0 | Image requests per second, per host (0.1–20) |

The `summarize` task's log ends with a table: pages crawled, images found, reused, skipped (by reason), indexed, faces stored, failures, dead links (HTTP 404/410), known dead links not requested, links abandoned, and outdated page links. A page link is outdated when a re-crawl of the page no longer finds that image there. The download task logs each such page (`outdated links page=… count=…`), the links are kept, and the search ignores them.

**Re-runs** skip every image URL already known, reuse identical content found under a new URL, and still record new pages. A link that fails is retried on later runs, **at most 3 times**; then it is abandoned and logged. To give abandoned links another chance:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM fetch_attempt WHERE attempts >= 3; UPDATE image SET detect_attempts = 0 WHERE status = '\''pending'\'';"'
```

**Dead links** (HTTP 404/410) are not failures, but they are logged (`page dead url=… status=404`), counted, and stored in the `dead_link` table. A link that answered 410 is not requested again; one that answered 404 is requested again after 30 days (`config.DEAD_LINK_RECHECK`). To list them, or to request them all again on the next run:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "SELECT url, kind, status, last_checked_at FROM dead_link ORDER BY last_checked_at DESC;"'
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM dead_link;"'
```

If you re-run a single task by hand in the Airflow UI, clear it **with downstream** (the default), so detection picks up what it stages.

To look at a stored image, byte-identical to the database copy, or with its face boxes drawn:

```bash
docker compose exec airflow-scheduler uv run --no-project python -m face_index.export_image --id 42 --boxes -o /tmp/42.jpg
docker compose cp airflow-scheduler:/tmp/42.jpg .
```

## How to use the face matching script

The script takes a photo, finds every face in it, and prints every crawled page that shows the same person. It reads the index the pipeline built and never writes to it. Run the pipeline at least once first.

1. Put the photo in `queries/` at the project root. That folder is shared with the script's container, so `queries/alice.jpg` is the same path on your machine and inside the container. Its contents are git-ignored.
2. Run:

```bash
docker compose run --rm face-matcher queries/alice.jpg
```

The `face-matcher` service uses the pipeline's image, so the photo is processed by exactly the same model setup as the index: RetinaFace, Facenet512, cosine distance, and the same JPEG ≤ 1024 px normalization. `docker compose up` does not start it; `run` starts it for one search and removes it afterwards. The first search after a build takes about 10–15 s, mostly loading TensorFlow and the model.

**Output.** One group per face found in the photo. Faces are numbered from 1, left to right, and each header shows the face's box in the normalized photo. Under each header are the matching page URLs, closest match first, each page once:

```text
# face 1 x=212 y=96 w=118 h=151: 3 pages
https://edition.cnn.com/2026/11/10/politics/example-a
https://edition.cnn.com/politics
https://edition.cnn.com/

# face 2 x=530 y=120 w=104 h=133: no matching pages
```

Results go to standard output, and progress and errors go to standard error. `grep -v '^#'` leaves only the URLs.

| Option | Default | Meaning |
|---|---|---|
| `--threshold T` | `0.30` | A stored face matches when its cosine distance is **below** T. T must be in (0, 2]. 0.30 is DeepFace's recommended threshold for Facenet512 with cosine distance; lower is stricter. |
| `--min-face PX` | `0` (every face) | Compare only indexed faces whose box is at least PX pixels on its shorter side, in the normalized image (longest side at most 1024). Small background faces give unreliable embeddings, so this can remove false matches; it can also drop a person who appears only small. Off by default until measured ([noisy-index-faces.md](docs/briefs/noisy-index-faces.md)). |
| `--face N` | all faces | Search only face N |
| `--details` | off | Also print each page's best distance, `lead` when the match is the page's main (preview) image, and the matching image URLs |
| `--list-faces` | off | Print the faces with their boxes and save `queries/<name>.faces.jpg` with the numbers drawn on. No search. It stays on your machine and is overwritten next time. |

A page counts only if its latest successful crawl still showed the matching image, so a page that stopped showing the person is not returned. Faces are compared only with faces indexed by the same model version, and only on images whose indexing has finished.

| Exit status | Meaning |
|---|---|
| 0 | Success, including "no matching pages" |
| 1 | Database or face model unavailable |
| 2 | Query file problem: missing, unreadable, not a photo, SVG, shorter side under 64 px, or over 50 megapixels |
| 3 | No face detected in the photo |
| 4 | Index not ready: no faces for the current model profile, so run the pipeline first |
| 5 | `--list-faces` could not write the annotated copy (the face list is still printed) |
| 64 | Usage error, including `--face N` beyond the number of faces |

From the host, without Docker, the same module runs against the Compose database:

```bash
FACE_INDEX_DSN=postgresql://faces@127.0.0.1:5432/faces uv run python -m face_index.face_matcher queries/alice.jpg
```

The full command-line contract is in [specs/002-face-search/contracts/face-matcher-cli.md](specs/002-face-search/contracts/face-matcher-cli.md).

## Libraries and models

| Purpose | Choice |
|---|---|
| Orchestration | Apache Airflow 3.4 (LocalExecutor, Task SDK) |
| Face detection | DeepFace with the **RetinaFace** detector, confidence ≥ 0.9 |
| Face embeddings | DeepFace **Facenet512** (512-d), aligned, `base` normalization, **cosine** distance |
| Storage | PostgreSQL 17 + **pgvector** (HNSW index, `vector_cosine_ops`) |
| Crawling | httpx (manual redirects, IP pinning), BeautifulSoup + lxml, `urllib.robotparser` |
| Images | Pillow |
| Tooling | uv, ruff, ty, pytest, pre-commit, GitHub Actions |

The model setup lives in one place, `src/face_index/model_profile.py`, and is recorded in the `model_profile` table, so the search script can check it embeds queries the same way.

## Approach and architecture decisions

Full reasoning: [`specs/001-image-indexer/research.md`](specs/001-image-indexer/research.md) (R1–R13).

```
init_schema ── crawl_cnn ── download_cnn ── plan_batches ── detect (mapped) ── summarize
```

People is configured but disabled (see Known limitations); each enabled site gets its own crawl and download pair, in parallel.

```
```

- **Ports and adapters.** `face_index` holds all logic and never imports Airflow; DAG tasks are thin wrappers. The pipeline depends only on protocols (`Fetcher`, `Pacer`, `ImageNormalizer`, `FaceEmbedder`, `IndexStore`), so the whole pipeline runs offline in tests with fakes.
- **One database.** Postgres + pgvector holds image metadata, the converted JPEG bytes, page links, faces and embeddings. An image becomes `indexed` in the same transaction that stores its faces; staged (`pending`) images are invisible to search. No Redis: the LocalExecutor needs no broker, and the per-host rate limit is one Postgres row per host.
- **Download and detection are decoupled.** Downloads stage images as `pending`; detection runs in parallel batches (`face_cpu` pool, 2 slots by default) with row locks, so no image is processed twice.
- **Polite and safe crawling.** robots.txt is obeyed; each host has a shared page budget and image budget; every redirect hop is checked before it is requested; requests to private or local network addresses are refused, and connections are pinned to the checked IP. Downloads stop at 20 MB and images over 50 megapixels are not decoded.
- **Normalization.** Every stored image is an RGB JPEG (quality 90, no EXIF) whose longest side is at most 1024 px; images are never upscaled. SVGs and images under 64 px are skipped with a recorded reason.
- **Incremental by URL and by content.** A known URL is never downloaded again; a new URL whose SHA-256 matches a stored image is linked to it without re-detecting faces. Lead images (`og:image`) are marked per page.
- **Failure isolation.** One bad page or image is logged with its URL and counted, and the run continues with everything else. A dead link (HTTP 404 or 410) is not a failure: it is logged, counted in its own summary row, and recorded so it is not requested again (see "Dead links"); a stored page that is now gone stops matching in search (its links count as outdated). The crawl or download task in which it happened is marked failed after it has finished and published its result, so the later tasks still run, and `summarize` then marks the run failed: a failed request is never hidden behind a green run. Database or model outages fail the task without a result, so Airflow retries it.
- **No secrets in git.** Database passwords and Airflow's JWT secret are generated at first start into private volumes (the pipeline writes as `faces_writer`, search reads as `faces_reader`, SELECT only).

## Tests and smoke runs

```bash
docker compose up -d postgres         # loopback database for integration tests
uv sync --locked --group dag          # `dag` adds Airflow for the DAG tests
uv run pytest -m "not model"          # unit, integration, DAG and offline smoke tests
uv run pytest -m model                # real DeepFace model
scripts/smoke.sh                      # the real DAG in the stack, depth 0, one page per site
```

**Project in iCloud Drive?** iCloud sets the macOS `hidden` flag on the venv's `.pth` files and
Python 3.13 skips hidden ones, so `import face_index` fails outside pytest (pytest adds `src` itself).
Keep the venv outside iCloud (`export UV_PROJECT_ENVIRONMENT=$HOME/.venvs/photo-crawler`), or run
`chflags nohidden .venv/lib/python3.13/site-packages/*.pth`. Delete any `… 2.pth` conflict copies.

| Tier | What it proves | Time |
|---|---|---|
| Offline smoke (`-m smoke`) | The whole pipeline, run 4 times, on a tiny mock web: storage, dedup, links, skips, re-runs, 3-attempt cap | ~1 s |
| Model smoke (`-m model`) | The real model; optional face photos go in `tests/fixtures/images/` | ~15 s |
| Stack smoke (`scripts/smoke.sh`) | Real Airflow, real model, live home pages | ~4 min |

## Example usage and expected outputs

All numbers below come from real runs on 2026-11-11, on an Apple M-series machine with 14 cores and 8 GB for Docker. This is not the spec's reference machine (4 cores, 16 GB), and only CNN could be crawled (people.com refuses the crawler, see Known limitations). These runs predate disabling people.com, so the smoke output below still shows its 402.

**First default run** (`airflow dags trigger image_indexer`, empty index), 72 min. This is the `summarize` task log:

```
metric                               count
pages crawled                           50
  cnn                                   50
images found                          5575
downloaded                            4710
reused by URL                          862
reused by fingerprint                    3
staged for detection                  4704
skipped                                  5
  robots_disallowed                      2
  too_small                              3
newly indexed (with faces)            3034
analyzed, no faces                    1670
faces stored                          9346
failures                                 2
  crawl                                  1
  download                               1
abandoned (3 attempts)                   0
```

Per stage: crawl 0.9 min, download 16.3 min (held at the 5 req/s image limit), face detection 54.8 min (2 workers, about 1.4 s per image each). The crawl failure is people.com's HTTP 402; the download failure is one image that returned HTTP 500.

**Second default run** right after the first, 2 min 10 s. Known URLs are not downloaded again; new articles add new images:

```
images found                          5609
downloaded                              72
reused by URL                         5534
newly indexed (with faces)              63
analyzed, no faces                       9
faces stored                           138
```

**Stack smoke** (`scripts/smoke.sh`, depth 0, one page per site), on the index from the runs above, so the DAG run took 46 s (most home-page images were already known; on an empty index it takes about 4 min). The checks count only this run's pages and the links it saw on them, except the orphan-face check, which covers the whole `face` table:

```
INFO: cnn crawled (1 page)
WARN: people has no page this run (see the failing links below)
PASS: at least one site crawled this run (1)
PASS: faces on this run's pages (392)
PASS: images are JPEG and at most 1024 px (0)
PASS: every face is linked to a page (0)
PASS: every face on this run's pages has a 512-d embedding (0)
failing link this run: https://people.com/ | 3 | http 402
SMOKE PASSED
```

**Stored data checks** (quickstart §3), on the index after both runs:

| Check | Result |
|---|---|
| Indexed images that are not JPEG or exceed 1024 px | 0 |
| Faces not linked to a page | 0 |
| Faces on images that are not `indexed` | 0 |
| Images shared by more than one page | 549 (one is on 47 pages) |
| Lead marks | 49, for 50 pages |
| Model profile | `retinaface+Facenet512+cosine+align+base@deepface0.0.104/tf2.21`, 512 |
| Analyzed images missing dimensions or a time | 0 |

**Spot check** (SC-004): 50 random indexed images exported with `--boxes`. Every box is on a face. Some are depictions (a statue, posters, helmet artwork) or tiny background faces.

## Search accuracy evaluation

The search is measured against hand-labelled label sets committed under [`queries/eval/`](queries/eval/), each from one crawl of the site: at least 5 people who appear on it, with every page they appear on, and at least 5 people who do not. Each person has one openly licensed photo that is not from the site, named by a neutral label (`person-a`, `absent-a`) and attributed in `attribution.csv`. Every set includes hard cases: a look-alike of a labelled person, a page where the face is small, and two labelled people who resemble each other when the crawl has them. The criteria are the face search's SC-001 to SC-003: at least 80% of each person's pages found, no page returned that does not show the person, and nothing returned for people not on the site.

Run it from the host against the Compose database, as the read-only role (the password is read from the Compose secrets volume, nothing is typed or committed):

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py
```

It prints a per-label table (expected pages, returned pages, recall, extra pages, label errors, and every missed or extra URL), then `PASS` (exit 0) or `FAIL` (exit 1); exit 2 means it could not evaluate (invalid files, a photo with no usable face, no index). `--set YYYY-MM-DD` picks an older set and `--check` validates the files without a database. With `--log`, the run is appended to [`runs.csv`](queries/eval/runs.csv), and every page's best distance up to 0.50 to `queries/eval/distances.csv`, so a run can be read as a curve, not only as one verdict.

CI validates the files and runs the evaluator against a seeded mock on every pull request. It also renders the committed results as an HTML report: the verdict, the per-label table, a chart of every logged page distance against the threshold, and the run history. The report is attached to each workflow run as the `eval-report` artifact, and its summary appears on the run's page. To build it locally:

```bash
uv run python scripts/eval_report.py --out eval-report.html
```

### Results

**Label set 2026-11-16, run 2026-11-17 10:34 UTC: FAIL.** The run used the labeller's snapshot of the 2026-11-16 CNN crawl (15,822 faces, 5,160 images, 97 pages), profile `retinaface+Facenet512+cosine+align+base@deepface0.0.104/tf2.21`, at the default threshold of 0.30. The table is copied from the run's output; every run is kept in [`queries/eval/runs.csv`](queries/eval/runs.csv).

| label | expected | returned | recall | extra | label errors |
|---|---:|---:|---:|---:|---:|
| person-a | 3 | 3 | 1.00 | 0 | 0 |
| person-b | 3 | 0 | 0.00 | 0 | 0 |
| person-c (small face on 1 page) | 6 | 6 | 1.00 | 0 | 0 |
| person-d (small face on 1 page) | 2 | 0 | 0.00 | 0 | 0 |
| person-e | 2 | 2 | 1.00 | 0 | 0 |
| absent-a | 0 | 0 | - | 0 | 0 |
| absent-b | 0 | 0 | - | 0 | 0 |
| absent-c | 0 | 1 | - | 1 | 0 |
| absent-d (look-alike of person-c) | 0 | 3 | - | 3 | 0 |
| absent-e | 0 | 0 | - | 0 | 0 |

Against the criteria:

- **SC-001, recall of at least 0.80: fails for 2 of 5 people.** person-b and person-d were not found on any of their 5 pages between them.
- **SC-002, no extra pages for present people: passes.** No present person was returned on a page that does not show them.
- **SC-003, nothing returned for absent people: fails for 2 of 5.** absent-c matched 1 page and the look-alike absent-d matched 3. In the match review, the labeller judged that none of these pages shows the person.

**How the labels were completed.** The set was labelled by browsing, and the first run (the first block in `runs.csv`) reported 8 extra pages. The labeller then reviewed every matched image with `face-matcher --details` and confirmed that each extra page does show the person. Pages are labelled from what their images show, so those pages were added to the set. Pages above the threshold were reviewed the same way, which added 3 pages for person-b and person-d that the search does not return. Each person is therefore labelled on the pages the review found, which may still miss a page the search never ranked.

**What the distances show.** The run at 13:19 UTC repeated these counts with `--log`, which also recorded every page's best distance to each query photo up to 0.50 in [`distances.csv`](queries/eval/distances.csv) (the CI report charts them):

| query | true pages (distance) | closest wrong page (distance) |
|---|---|---|
| person-a | 0.150, 0.171, 0.256 | 0.413 |
| person-b | 0.313, 0.342, 0.344 | 0.365 |
| person-c | 0.100 to 0.123 (6 pages) | 0.342 |
| person-d | 0.365, 0.392 | 0.349 |
| person-e | 0.157, 0.158 | 0.370 |
| absent-c | none | 0.280, then 0.349 |
| absent-d | none | 0.233, 0.296, 0.299, then 0.303 |

- **For person-a, c and e the search separates cleanly:** true pages at 0.26 or less, the nearest wrong page at 0.34 or more.
- **person-b is a near miss.** All 3 pages are ranked above any wrong page but lie just past 0.30.
- **person-d cannot be found by a threshold.** A wrong page (0.349) is closer than both true pages.
- **No single threshold separates true from wrong matches.** absent-d's wrong pages start at 0.233, below person-b's true pages. Across the set:

  | threshold | true pages found (of 16) | wrong pages returned |
  |---:|---:|---:|
  | 0.30 | 11 | 4 |
  | 0.35 | 14 | 10, including 1 each for person-c and person-d |
  | 0.40 | 16 | 38 |

  A higher threshold trades the recall failure for more false matches and also breaks SC-002, so the default stays at 0.30. These numbers come from this set alone and are not a basis for choosing a threshold; tuning on them would overfit it.
- **A few pages are close to many faces.** `/travel/news`, `/world/heroes` and `/world/americas` are within 0.40 of three different query photos each, and include absent-d's three false matches. `/world` does the same, but it is an index page with many faces. A face on those pages that resembles many people (small, blurred or side-on) would explain this; it is the case in [noisy-index-faces.md](docs/briefs/noisy-index-faces.md), and those pages' faces are the first thing to inspect.
- **Every labelled page is within 0.50** of its person's photo, so the misses come from where the cutoff sits, not from faces missing from the index.

**Face-size filter: tried at 40 px, not adopted.** `--min-face PX` (on `face_matcher` and the evaluator) compares only indexed faces at least PX pixels on their shorter side. The value was fixed at 40 px before the run, from the brief's common floor for Facenet-style models, not chosen from these results. The run at 14:27 UTC (`min_face_px=40` in `runs.csv`) compares with the unfiltered run at 13:19, and with a control run at 4 px (14:28), which removes almost no face:

| | no filter | 4 px (control) | 40 px |
|---|---:|---:|---:|
| verdict | FAIL | FAIL | FAIL |
| true pages returned (of 16) | 11 | 11 | 10 |
| wrong pages below 0.30 | 4 | 4 | 4 |
| wrong pages below 0.40 | 38 | 38 | 19 |

The control run's 188 logged distances equal the unfiltered run's exactly, so the filter changes nothing by itself; the differences at 40 px come from the faces it removes.

- **It does not remove the false matches.** absent-c's page (0.280) and absent-d's three pages (0.238 to 0.299) come from faces of 40 px or more, so they are clear faces that look like those people, not tiny background faces.
- **It costs a true page.** person-c is no longer found on `/politics/president-donald-trump-47` (0.110 before), where the face is under 40 px. That is the "appears only small" case this set lacked. person-c's recall drops to 0.83, still above 0.80. person-b's `/world/middle-east` page moves from 0.342 to 0.445 for the same reason.
- **It halves the near misses** between 0.30 and 0.40 (38 wrong pages to 19), including the pages that were close to several queries (`/travel/news` for person-c, `/world` for absent-d). Small faces were adding noise there, but not at the threshold in use.

The filter stays available and off by default (0). The remaining false matches are clear look-alikes, which a size filter cannot separate. The next things to try are other approaches: several query photos per person, a stronger embedding model, or score normalization. Any of them needs a second label set to check against.

**Experiments.** Whether hubness reduction (CSLS), score normalization (T-norm), query expansion or the ArcFace model separate true from wrong matches better is measured offline, on a local export of the index, with a grid and metrics fixed before any run: [search-experiments.md](docs/briefs/search-experiments.md). Results will be added here; no default changes before a second label set confirms them. The second set now exists (2026-11-17, below); the experiments' confirmation run on it is still to be done.

**Hard cases in this set.**
- The small-face rows are weak. person-c and person-d appear on the Ukraine page both large and small, so the large face can carry the match. The 40 px run showed that person-c appears only small on `/politics/president-donald-trump-47`; a later set should mark such a page `small_face`.
- The crawl had no on-site look-alike pair: no two labelled people on the site resemble each other (`onsite_pair=false`).

### Results, label set 2026-11-17

**Label set 2026-11-17, run 2026-11-17 18:30 UTC: FAIL.** The second set, on a second crawl of CNN (16,816 faces, 5,577 images, 97 pages), with 10 present and 10 absent people. The profile and threshold are the same as for 2026-11-16. The table is copied from the run's output, and the run is in [`queries/eval/runs.csv`](queries/eval/runs.csv).

| label | expected | returned | recall | extra | label errors |
|---|---:|---:|---:|---:|---:|
| person-a | 3 | 3 | 1.00 | 0 | 0 |
| person-b | 3 | 0 | 0.00 | 0 | 0 |
| person-c | 6 | 5 | 0.83 | 0 | 0 |
| person-d | 7 | 0 | 0.00 | 0 | 0 |
| person-e | 2 | 2 | 1.00 | 0 | 0 |
| person-f (small face on 4 pages) | 23 | 0 | 0.00 | 0 | 0 |
| person-g | 2 | 0 | 0.00 | 0 | 0 |
| person-h | 4 | 3 | 0.75 | 0 | 0 |
| person-i | 4 | 2 | 0.50 | 0 | 0 |
| person-j | 4 | 3 | 0.75 | 0 | 0 |
| absent-a | 0 | 0 | - | 0 | 0 |
| absent-b | 0 | 0 | - | 0 | 0 |
| absent-c | 0 | 1 | - | 1 | 0 |
| absent-d (look-alike of person-c) | 0 | 2 | - | 2 | 0 |
| absent-e | 0 | 0 | - | 0 | 0 |
| absent-f | 0 | 0 | - | 0 | 0 |
| absent-g | 0 | 0 | - | 0 | 0 |
| absent-h | 0 | 0 | - | 0 | 0 |
| absent-i | 0 | 0 | - | 0 | 0 |
| absent-j | 0 | 0 | - | 0 | 0 |

Against the criteria:

- **SC-001, recall of at least 0.80: fails for 7 of 10 people.** Only person-a, person-c and person-e pass. For person-b, person-d, person-f and person-g the search returned none of their labelled pages at 0.30, although every one of those pages is labelled; their closest pages lie just past the threshold (0.313, 0.349, 0.3002 and 0.3008), and person-h, person-i and person-j (0.75, 0.50, 0.75) each missed one or two pages.
- **SC-002, no extra pages for present people: passes.** No present person was returned on a page that does not show them. At 0.35 one is.
- **SC-003, nothing returned for absent people: fails for 2 of 10.** absent-c matched 1 page and the look-alike absent-d matched 2. The labeller judged that none of these pages shows the person (pages that also matched in 2026-11-16).

**What the distances show.** The run logged every page's best distance up to 0.50 in [`distances.csv`](queries/eval/distances.csv).

- **Several misses are just past the threshold.** person-f's closest labelled page is 0.3002 and person-g's is 0.3008. person-b's three pages lie between 0.313 and 0.344, as in 2026-11-16, so that near miss repeats on a new crawl.
- **person-d is the hardest.** 5 of its 7 pages lie between 0.349 and 0.495 and 2 are beyond 0.50. The closest wrong page for person-d is 0.447, so a higher threshold would not return wrong pages first for this person.
- **The formal-portrait query photos probably match worse for the US politicians.** person-f, person-g and person-i have the weakest recall among the new people, and their query photos are formal portraits compared with angled, small or blurred news faces; this is a guess, not tested. person-f has 4 labelled pages (the markets pages, marked `small_face`) where no face lies within 0.50.
- **Across the set:**

  | threshold | true pages found (of 58) | wrong pages returned (present people) | wrong pages returned (absent people) |
  |---:|---:|---:|---:|
  | 0.30 | 18 | 0 | 3 |
  | 0.35 | 31 | 1 | 6 |
  | 0.40 | 43 | 16 | 34 |

  The default stays at 0.30. As in 2026-11-16, no threshold separates the wrong pages from the true ones, and these counts are not a basis for choosing one.

**How the labels were made.** The labels were not made by browsing. For each present person, a page was a candidate if an image on it was returned by the search at up to distance 0.45, or if a caption on a freshly crawled page named the person. The labeller then approved each candidate in a review page that showed the page's images with the detected face boxes. Caption-only candidates and look-alike matches were rejected where the person was not in the image. Two pages the labeller was unsure about (person-f on `/world` and `/world/middle-east`) were left out.

**Hard cases in this set.** The small-face case is person-f on the four `/markets` pages, where the face is small and the person does not also appear large. The look-alike is absent-d for person-c. There is no on-site look-alike pair (`onsite_pair=false`).

**Bias in this set.** The candidates came partly from the baseline search itself, so pages the baseline cannot rank are under-labelled. Pages where a person appears but is neither returned at 0.45 nor named in a caption are missing from the labels. Putin and Trump in particular are named on more pages than are labelled, and recall is therefore probably over-stated for them rather than under-stated.

### Labelling method and its limits

How each set was labelled (browsing, searching or both), by whom and on which crawl date is in [`queries/eval/methods.csv`](queries/eval/methods.csv). The labeller keeps a snapshot of that crawl's index locally, so the numbers can be re-run; it is never committed, because it holds face embeddings of people who gave no licence.

- **Recall is only as good as the labels.** A page the labeller did not find is invisible to the measurement.
- **The sample is small** and limited to public figures with openly licensed photos.
- **Rendition checks are manual.** That a query photo is not a copy of an indexed image is checked in review, from the attribution's source page.
- **Results are tied to one crawl.** The site changes hourly. On an index crawled on another date, labelled pages that are gone are reported as label errors and the run fails; that is drift, not a search failure. To measure a new crawl, add a new label set for it ([quickstart §5](specs/003-evaluation-set/quickstart.md)).

### Correcting a set or taking a photo down

A set may be corrected (a labelling mistake, a wrong licence, a takedown request) as long as every set keeps its minimums: a removed label is replaced in the same change. After a change to the latest set or one of its photos, that set needs a fresh logged run before CI passes, so these results never describe inputs that have since changed. Attribution or methods corrections need no new run, since they change no result ([quickstart §5b](specs/003-evaluation-set/quickstart.md)). Deleting a photo does not remove it from git history; for a takedown, a maintainer also rewrites the history on the remote.

## Known limitations

- **SC-011 (a default run on the enabled sites within 30 minutes) is open.** Only CNN is enabled. The one measured run took 72 minutes for CNN, on the machine above rather than the reference machine, so it neither passes nor formally fails the criterion. CNN articles carry about 100 images each, so 50 pages give about 4,700 images; detection, at about 0.7 s per image with 2 workers, is most of the time. An estimate from those per-image times, not a measurement: about 15 pages fit 30 minutes for one site, and about 10 pages per site if People were enabled too (downloads run in parallel per host, detection does not). Raising `FACE_WORKERS` helps if Docker has the memory. Changing the default `page_cap` or the target is an open decision.

- **people.com is disabled.** It answers the crawler's honest User-Agent (`photo-crawler/0.1.0`) with HTTP 402 from Cloudflare, and its terms forbid scraping. The crawler does not disguise itself to get around this, so the site stays in `config.SITES` with `enabled=False` and the DAG has no `crawl_people`/`download_people` tasks. Leaving People out and documenting why was agreed with the assignment's reviewer (2026-11-15); the other option offered, indexing lead images from People's public news sitemap, was not taken. With the site's permission, set `enabled=True` to crawl it again; while it refuses, a run that includes it records the 402, marks `crawl_people` and the run failed, and still indexes CNN.
- **Several sizes of one photo** are stored as separate images by design, so `--details` can list the same picture more than once under one page. The page itself is listed once.
- **Faces that are depictions** (statues, posters) or tiny background faces are indexed and searched like any other face. A size filter is an open decision ([TODO.md](TODO.md)).
- **A look-alike can pass the default threshold.** First live check (2026-11-13, CNN index of about 9.5k faces): searching with an indexed one-face selfie returned its own 4 pages (distance ≤ 0.004) and one more page at 0.261. That page shows a different man, whose face is only about 55 px wide in a 270×480 red-carpet photo. Small faces give less reliable embeddings, so this is the case [noisy-index-faces.md](docs/briefs/noisy-index-faces.md) is about. The threshold was not tuned to hide it; the labelled evaluation (SC-001 to SC-003) is what decides whether a face-size filter or a stricter default is needed.
- **Accuracy (SC-001 to SC-003)** is measured against the committed evaluation set; see [Search accuracy evaluation](#search-accuracy-evaluation) for the results and their limits.

- The face matching script (and the constitution's final check, "run it against the resulting database") arrives with feature 002.

## Database access and tests

`docker compose up` generates the database passwords on first start into a private `db-secrets`
volume (nothing is committed or put in `.env`). The pipeline connects as `faces_writer`, search
as `faces_reader` (SELECT only), and the Airflow containers find the passwords through
`PGPASSFILE`. `docker compose down -v` removes the volumes together.

To run the integration tests against the Compose database from the host (loopback only):

```bash
export FACE_INDEX_TEST_DSN="postgresql://faces:$(docker compose exec -T postgres cat /run/db-secrets/postgres_password)@127.0.0.1:5432/faces_test"
uv run pytest -m "not model"
```
