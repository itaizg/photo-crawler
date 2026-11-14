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
| `page_cap` | 50 | Maximum pages per site; `null` = no cap |
| `page_rate` | 1.0 | Page requests per second, per host (0.1–10) |
| `image_rate` | 5.0 | Image requests per second, per host (0.1–20) |

The `summarize` task's log ends with a table: pages crawled, images found, reused, skipped (by reason), indexed, faces stored, failures, links abandoned, and outdated page links. A page link is outdated when a re-crawl of the page no longer finds that image there. The download task logs each such page (`outdated links page=… count=…`), the links are kept, and the search ignores them.

**Re-runs** skip every image URL already known, reuse identical content found under a new URL, and still record new pages. A link that fails is retried on later runs, **at most 3 times**; then it is abandoned and logged. To give abandoned links another chance:

```bash
docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d faces -c "DELETE FROM fetch_attempt WHERE attempts >= 3; UPDATE image SET detect_attempts = 0 WHERE status = '\''pending'\'';"'
```

If you re-run a single task by hand in the Airflow UI, clear it **with downstream** (the default), so detection picks up what it stages.

To look at a stored image, byte-identical to the database copy, or with its face boxes drawn:

```bash
docker compose exec airflow-scheduler python -m face_index.export_image --id 42 --boxes -o /tmp/42.jpg
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
init_schema ─┬─ crawl_cnn    ── download_cnn    ─┐
             └─ crawl_people ── download_people ─┴─ plan_batches ── detect (mapped) ── summarize
```

- **Ports and adapters.** `face_index` holds all logic and never imports Airflow; DAG tasks are thin wrappers. The pipeline depends only on protocols (`Fetcher`, `Pacer`, `ImageNormalizer`, `FaceEmbedder`, `IndexStore`), so the whole pipeline runs offline in tests with fakes.
- **One database.** Postgres + pgvector holds image metadata, the converted JPEG bytes, page links, faces and embeddings. An image becomes `indexed` in the same transaction that stores its faces; staged (`pending`) images are invisible to search. No Redis: the LocalExecutor needs no broker, and the per-host rate limit is one Postgres row per host.
- **Download and detection are decoupled.** Downloads stage images as `pending`; detection runs in parallel batches (`face_cpu` pool, 2 slots by default) with row locks, so no image is processed twice.
- **Polite and safe crawling.** robots.txt is obeyed; each host has a shared page budget and image budget; every redirect hop is checked before it is requested; requests to private or local network addresses are refused, and connections are pinned to the checked IP. Downloads stop at 20 MB and images over 50 megapixels are not decoded.
- **Normalization.** Every stored image is an RGB JPEG (quality 90, no EXIF) whose longest side is at most 1024 px; images are never upscaled. SVGs and images under 64 px are skipped with a recorded reason.
- **Incremental by URL and by content.** A known URL is never downloaded again; a new URL whose SHA-256 matches a stored image is linked to it without re-detecting faces. Lead images (`og:image`) are marked per page.
- **Failure isolation.** One bad page or image is logged with its URL and counted; the run continues. Database or model outages fail the task instead, so Airflow retries it.
- **No secrets in git.** The local database uses no password (trust auth, reachable only from the Compose network and 127.0.0.1); Airflow's JWT secret is generated at first start into a private volume.

## Tests and smoke runs

```bash
docker compose up -d postgres         # loopback database for integration tests
uv sync --locked --group dag          # `dag` adds Airflow for the DAG tests
uv run pytest -m "not model"          # unit, integration, DAG and offline smoke tests
uv run pytest -m model                # real DeepFace model
scripts/smoke.sh                      # the real DAG in the stack, depth 0, one page per site
```

| Tier | What it proves | Time |
|---|---|---|
| Offline smoke (`-m smoke`) | The whole pipeline, run 4 times, on a tiny mock web: storage, dedup, links, skips, re-runs, 3-attempt cap | ~1 s |
| Model smoke (`-m model`) | The real model; optional face photos go in `tests/fixtures/images/` | ~15 s |
| Stack smoke (`scripts/smoke.sh`) | Real Airflow, real model, live home pages | ~4 min |

## Example usage and expected outputs

All numbers below come from real runs on 2026-11-11, on an Apple M-series machine with 14 cores and 8 GB for Docker. This is not the spec's reference machine (4 cores, 16 GB), and only CNN could be crawled (people.com refuses the crawler, see Known limitations).

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

**Stack smoke** (`scripts/smoke.sh`, depth 0, one page per site), on the index from the runs above, so the DAG run took 46 s (most home-page images were already known; on an empty index it takes about 4 min). The checks count only this run's pages:

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

## Known limitations

- **SC-011 (a default run on both sites within 30 minutes) is open.** The one measured run took 72 minutes for CNN alone, on the machine above rather than the reference machine, so it neither passes nor formally fails the criterion. CNN articles carry about 100 images each, so 50 pages give about 4,700 images; detection, at about 0.7 s per image with 2 workers, is most of the time. An estimate from those per-image times, not a measurement: about 15 pages fit 30 minutes for one site, and about 10 pages per site if both sites are reachable (downloads run in parallel per host, detection does not). Raising `FACE_WORKERS` helps if Docker has the memory. Changing the default `page_cap` or the target is an open decision.

- **people.com refuses the crawler** with HTTP 402 for its honest User-Agent (`photo-crawler/0.1.0`). The pipeline records the failure, retries it on up to 3 runs, and indexes CNN normally. The crawler does not disguise itself to get around this; crawling People needs the site's permission.
- **A page can briefly lose its links after a failed download task.** If the download task dies partway through a page (the database or model is lost), that page's later links look outdated to the search until Airflow retries the task, which refreshes them.
- **Several sizes of one photo** are stored as separate images by design, so `--details` can list the same picture more than once under one page. The page itself is listed once.
- **Faces that are depictions** (statues, posters) or tiny background faces are indexed and searched like any other face. A size filter is an open decision ([TODO.md](TODO.md)).
- **A look-alike can pass the default threshold.** First live check (2026-11-13, CNN index of about 9.5k faces): searching with an indexed one-face selfie returned its own 4 pages (distance ≤ 0.004) and one more page at 0.261. That page shows a different man, whose face is only about 55 px wide in a 270×480 red-carpet photo. Small faces give less reliable embeddings, so this is the case [noisy-index-faces.md](docs/briefs/noisy-index-faces.md) is about. The threshold was not tuned to hide it; the labelled evaluation (SC-001 to SC-003) is what decides whether a face-size filter or a stricter default is needed.
- **Accuracy (SC-001 to SC-003)** is measured with `scripts/evaluate_search.py` against hand-labelled photos, which are not in the repository (licensing). See [specs/002-face-search/quickstart.md](specs/002-face-search/quickstart.md) §7.
