# photo-crawler

System for crawling a website. It extracts images containing people with visible faces, and indexes them for image-based search. The system allows the user to provide an image of a specific character and return a list of all pages on the site that contain an image of that same character.

This repository contains the **image indexer** (the Airflow pipeline, feature 001), the **face matching script** (`face_index.face_matcher`, feature 002) and a labelled **evaluation set** that measures the search's accuracy (feature 003). Specs, plans and contracts are under `specs/`; open items are in [TODO.md](TODO.md).

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
| `reindex` | false | Embed the images indexed under another model profile again, from their stored JPEGs (see "Re-index after a model change") |

The `summarize` task's log ends with a table: pages crawled, images found, reused, skipped (by reason), indexed, faces stored, failures, dead links (HTTP 404/410), known dead links not requested, links abandoned, and outdated page links. A page link is outdated when a re-crawl of the page no longer finds that image there. The download task logs each such page (`outdated links page=… count=…`), the links are kept, and the search ignores them.

**Re-index after a model change.** The face model, its detector settings and the DeepFace and TensorFlow versions form the model profile key. The search compares only faces of the current profile, so after the key changes (as it did when the model changed from Facenet512 to ArcFace) the existing faces cannot be found until they are embedded again. Run the pipeline with `reindex` set:

```bash
docker compose exec airflow-scheduler airflow dags trigger image_indexer \
  --conf '{"reindex": true, "page_cap": 1}'
```

The first task deletes the faces of every other profile in one transaction, and sets those images back to `pending`; their stored JPEGs stay. The normal detection tasks then embed them with the current model, so nothing is downloaded again. The re-index takes about as long as the first index (an hour or more for 5,000 images on CPU), and `page_cap: 1` keeps the crawl in the same run to one page per site. While it runs, **search covers only the images already re-embedded**; an image is never in the index under two profiles. If the run is interrupted, or the per-run batch limit defers some images (the log says `detection deferred`), trigger it again: images still `pending` are picked up and nothing is duplicated. Images with no faces keep their status: their JPEGs are not kept, so they cannot be looked at again. The re-index therefore refuses to start when the detection settings (detector, alignment, minimum confidence) differ from those of an earlier profile; start a fresh index then. Start a re-index only when no other run is in progress (the DAG runs one at a time, so wait for a running one to finish). Old `model_profile` rows are kept as history. A run without `reindex` never touches other profiles' faces.

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

The `face-matcher` service uses the pipeline's image, so the photo is processed by exactly the same model setup as the index: RetinaFace, ArcFace, cosine distance, and the same JPEG ≤ 1024 px normalization. `docker compose up` does not start it; `run` starts it for one search and removes it afterwards. The first search after a build takes about 10–15 s, mostly loading TensorFlow and the model.

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
| Face embeddings | DeepFace **ArcFace** (512-d), aligned, `base` normalization, **cosine** distance (Facenet512 until 2026-11-17; see Experiments) |
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
export FACE_INDEX_TEST_DSN="postgresql://faces:$(docker compose exec -T postgres cat /run/db-secrets/postgres_password)@127.0.0.1:5432/faces_test"
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

The search is measured against hand-labelled label sets in [`queries/eval/`](queries/eval/): at least 5 people who appear on the crawled site, with every page they appear on, and at least 5 who do not, each with one openly licensed photo from outside the site. The criteria are the face search's SC-001 to SC-003: at least 80% of each person's pages found, no page returned that does not show the person, and nothing returned for people not on the site. Run it against the Compose database, as the read-only role:

```bash
PGPASSWORD="$(docker compose exec -T postgres cat /run/db-secrets/reader_password)" FACE_INDEX_DSN=postgresql://faces_reader@127.0.0.1:5432/faces uv run python scripts/evaluate_search.py
```

It prints a per-label table, then `PASS` (exit 0) or `FAIL` (exit 1); exit 2 means it could not evaluate. CI validates the label files and runs the evaluator against a mock on every pull request, and attaches an HTML report of the committed results (`eval-report`).

**Result on label set 2026-11-16, at the default threshold of 0.30: FAIL.** 5 present people (16 true pages) and 5 absent people, on a 15,822-face CNN index:

- **SC-001 fails for 2 of 5 people.** person-b and person-d were found on none of their pages (5 between them); person-a, c and e were found on all of theirs.
- **SC-002 passes.** No present person was returned on a page that does not show them.
- **SC-003 fails for 2 of 5 absent people,** with 4 wrong pages, 3 of them for a look-alike.
- **No threshold passes.** True matches reach a distance of 0.392 and wrong ones start at 0.233. The threshold was not tuned to hide this.

**What was tried since.** A face-size filter (`--min-face 40`) kept the same 4 false matches and lost one true page, so it stays off. Re-scoring (CSLS, T-norm, query expansion), measured offline in 12 combinations with both models, ranked fewer true pages above every wrong page in every case. Swapping Facenet512 for **ArcFace** (raw cosine) is the one candidate: AUC 0.994 to 0.999, and true pages ranked above every wrong page 10 to 14 of 16. It is exploratory, on 10 people, and must be confirmed on a second label set, with its own threshold, before the default changes ([TODO.md](TODO.md)).

**How the labels were made, and their bias.** One labeller browsed the 2026-11-16 crawl for each person's pages, then reviewed every page the search matched or nearly matched (`face-matcher --details`) and added the pages that do show the person; the method is recorded in [`methods.csv`](queries/eval/methods.csv). Recall is only as good as those labels: a page that neither browsing nor the search surfaced is invisible to the measurement, and because the review started from the search's own matches, the labels lean towards pages the search ranks well. The sample is small (10 people, all public figures with openly licensed photos), one labeller judged every page, and the results describe that one crawl, since the site changes hourly.

Full results, distances, the experiments and the labelling method and its limits: [docs/search-evaluation.md](docs/search-evaluation.md).

## Known limitations

- **SC-011 (a default run on the enabled sites within 30 minutes) is open.** Only CNN is enabled. The one measured run took 72 minutes for CNN, on the machine above rather than the reference machine, so it neither passes nor formally fails the criterion. CNN articles carry about 100 images each, so 50 pages give about 4,700 images; detection, at about 0.7 s per image with 2 workers, is most of the time. An estimate from those per-image times, not a measurement: about 15 pages fit 30 minutes for one site, and about 10 pages per site if People were enabled too (downloads run in parallel per host, detection does not). Raising `FACE_WORKERS` helps if Docker has the memory. Changing the default `page_cap` or the target is an open decision.
- **people.com is disabled.** It answers the crawler's honest User-Agent (`photo-crawler/0.1.0`) with HTTP 402 from Cloudflare, and its terms forbid scraping. The crawler does not disguise itself to get around this, so the site stays in `config.SITES` with `enabled=False` and the DAG has no `crawl_people`/`download_people` tasks. Leaving People out and documenting why was agreed with the assignment's reviewer (2026-11-15); the other option offered, indexing lead images from People's public news sitemap, was not taken. With the site's permission, set `enabled=True` to crawl it again; while it refuses, a run that includes it records the 402, marks `crawl_people` and the run failed, and still indexes CNN.
- **Several sizes of one photo** are stored as separate images by design, so `--details` can list the same picture more than once under one page. The page itself is listed once.
- **Faces that are depictions** (statues, posters) or tiny background faces are indexed and searched like any other face. A 40 px size filter was measured and not adopted, because the false matches are clear look-alike faces ([search-evaluation.md](docs/search-evaluation.md)).
- **The search fails its accuracy criteria** on the first label set (see [Search accuracy evaluation](#search-accuracy-evaluation)): 2 of 5 people are missed and a look-alike passes the threshold. The threshold was not tuned to hide this; ArcFace is the candidate fix, pending a second label set.
